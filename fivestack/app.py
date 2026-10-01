"""5-Stack Tracker web server.

    python server.py                 # normal mode (needs config.json with API key + members)
    python server.py --tunnel        # also publish it online through a Cloudflare quick tunnel
    python server.py --demo          # explore the UI with synthetic data, no API key needed
    python server.py --no-browser --port=8090 --no-tunnel
"""
import html
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from email.utils import formatdate, parsedate_to_datetime
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .arcade import ArcadeManager
from .auth import CLEAR_BETTOR_COOKIE, CLEAR_COOKIE, THROTTLE_MSG, Auth
from .bananas import BananaManager
from .bets import TAX_MIN_TRANSFER, TAX_RATE, BetError, BetManager
from .config import CONFIG_PATH, DATA_DIR, TOOLS_DIR, WEB_DIR, config_problems, load_bettor_names, load_config, mask
from .db import DB
from .forecasts import build_forecasts
from .gamestate import ending
from .henrik import HenrikClient, HenrikError
from .insights import betting_report, build_insights
from .odds import OddsEngine
from .recap import build_recap
from .rewards import RewardManager
from .stats import build_stats
from .tracker import ROSTER_MAX, ROSTER_MIN, Tracker, TrackerError
from .tunnel import Tunnel

KNOWN_MAPS = ["Abyss", "Ascent", "Bind", "Breeze", "Corrode", "Fracture", "Haven", "Icebox", "Lotus", "Pearl", "Split", "Sunset"]
KNOWN_AGENTS = [
    "Astra", "Breach", "Brimstone", "Chamber", "Clove", "Cypher", "Deadlock", "Fade", "Gekko", "Harbor",
    "Iso", "Jett", "KAY/O", "Killjoy", "Miks", "Neon", "Omen", "Phoenix", "Raze", "Reyna", "Sage", "Skye", "Sova",
    "Tejo", "Veto", "Viper", "Vyse", "Waylay", "Yoru",
]
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".wav": "audio/wav",
    ".ico": "image/x-icon",
}

LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>5-Stack Tracker · Log in</title><link rel="stylesheet" href="/style.css"></head>
<body><main class="container login"><form method="post" action="/login" class="card">
<div class="brand"><img class="logo" src="/assets/onkey-logo.png" alt="" width="62" height="36"><div><h1>5-Stack Tracker</h1><div class="sub">Enter the squad password</div></div></div>
{error}
<label>Password<input type="password" name="password" autofocus autocomplete="current-password" required></label>
<button class="btn primary" type="submit">Log in</button>
</form></main></body></html>"""


class App:
    def __init__(self, cfg, demo=False, port=8080):
        self.cfg = cfg
        self.demo = demo
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db = DB(os.path.join(DATA_DIR, "demo.db" if demo else "tracker.db"))
        self.engine = OddsEngine(cfg)
        self.bets = BetManager(cfg, self.db, self.engine)
        self.rewards = RewardManager(cfg, self.db, load_bettor_names())
        self.bananas = BananaManager(cfg, self.db, self.bets)
        self.arcade = ArcadeManager(self.db)
        self.auth = Auth(cfg, self.db)
        self.tunnel = Tunnel(cfg, port, TOOLS_DIR)
        self.problems = [] if demo else config_problems(cfg)
        self.client = None
        self.tracker = None
        if demo:
            from . import demo_seed
            demo_seed.seed(self.db)
            self.bananas.earn()
            demo_seed.seed_shop(self.db, self.bananas)
        elif not self.problems:
            self.client = HenrikClient(cfg["api_key"].strip(), min_interval=float(cfg.get("min_request_interval_s", 1.5)))
            self.tracker = Tracker(cfg, self.db, self.client, on_new_matches=self.on_new_matches)
        self.bananas.earn()  # credit gains from before the shop existed (or from a sync that stopped part-way)
        self.started = time.time()

    def on_new_matches(self, matches):
        settled = []
        for m in matches:
            players = self.db.match_players(m["match_id"])
            settled.extend(self.bets.settle_for_match(m, players))
            paid = self.rewards.pay_for_match(m, players)
            if paid:
                print(f"[rewards] {m['map']} {m['result']}: " + ", ".join(f"{r['bettor']} +{r['base'] + r['bonus']:.0f}" for r in paid), flush=True)
        self.bananas.earn()  # bananas for the bets just won and the rewards just paid
        return settled

    def _member_public(self, m):
        return {"puuid": m["puuid"], "name": m["name"], "tag": m["tag"], "nickname": m.get("nickname") or m["name"],
                "bettor": self.rewards.account_name(m),  # their betting account, so bettors share the member's colour
                "linked": bool(m.get("bettor")),  # that account is the one they signed up / linked with themselves
                "active": bool(m.get("active", 1)), "previous_name": m.get("previous_name"),
                "added_at": m.get("added_at"), "name_checked_ts": m.get("name_checked_ts")}

    def members_public(self):
        """The active squad as the page sees it: each member's current Riot ID, nickname and betting account."""
        return [self._member_public(m) for m in self.db.members()]

    def pool_public(self):
        """Everyone in the pool: the active squad first, then the bench."""
        return [self._member_public(m) for m in self.db.pool()]

    def own_member(self, bettor):
        """The pool entry a betting account owns, as the page sees it."""
        m = self.db.member_by_bettor(bettor["name"])
        return {"puuid": m["puuid"], "name": m["name"], "tag": m["tag"], "nickname": m.get("nickname") or m["name"],
                "active": bool(m.get("active", 1))} if m else None

    def link_riot_id(self, bettor, riot_id, nickname=""):
        """Put a betting account's Riot ID on the squad (see Tracker.set_riot_id); starts a re-scan if the games
        that count may have changed. Returns the tracker's result, or {warning} when it can't be done here."""
        if self.demo:
            return {"warning": "Riot IDs can't be linked in demo mode."}
        if not self.tracker:
            return {"warning": "Riot IDs can be linked once the HenrikDev API key is set."}
        try:
            r = self.tracker.set_riot_id(bettor["name"], riot_id, nickname)
        except (TrackerError, HenrikError) as e:
            return {"warning": getattr(e, "message", None) or str(e)}
        if r.get("changes"):  # the active squad changed: pick up anything the stored lines couldn't prove
            self.tracker.request_sync(full=True)
            self.tracker.wake()
        return r

    def roster_info(self):
        return {"min": ROSTER_MIN, "max": ROSTER_MAX, "size": self.db.count_members(), "pool": self.db.count_pool(),
                "editable": bool(self.tracker) and not self.demo, "admin_required": bool(self.auth.admin_password)}

    def status(self):
        members = self.members_public()
        log = list(self.tunnel.logs)
        st = {
            "configured": not self.problems,
            "demo": self.demo,
            "problems": self.problems,
            "region": self.cfg.get("region"),
            "modes": self.cfg.get("modes") or [],
            "poll_interval_minutes": self.cfg.get("poll_interval_minutes", 10),
            "members": members,
            "expected_members": len(members),
            "roster": self.roster_info(),
            "games": self.db.count_matches(),
            "record": self.db.record(),
            "api_key_masked": mask(self.cfg.get("api_key")),
            "uptime_s": round(time.time() - self.started),
            "house_edge": self.engine.edge,
            "starting_balance": self.bets.starting,
            "bet_grace_minutes": self.bets.grace_s / 60,
            "bet_cancel_minutes": self.bets.cancel_s / 60,
            "game_reward": self.rewards.game,
            "win_reward": self.rewards.win,
            "performance_bonus_max": self.rewards.bonus_max,
            "banana_rate": self.bananas.rate,
            "tax_rate": TAX_RATE,
            "tax_min_transfer": TAX_MIN_TRANSFER,
            "server_time": time.time(),
            "auth": {"enabled": self.auth.enabled, "admin_required": bool(self.auth.admin_password)},
            "tunnel": dict(self.tunnel.state),
        }
        if self.tracker:
            st["tracker"] = dict(self.tracker.state)
            st["ratelimit"] = self.client.ratelimit
            st["api_calls"] = self.client.calls
            log += list(self.tracker.logs)
        log.sort(key=lambda l: l["ts"])
        st["log"] = log[-50:]
        return st

    def content(self):
        agents = {r["agent"] for r in self.db.query("SELECT DISTINCT agent FROM match_players WHERE agent IS NOT NULL")}
        maps = {r["map"] for r in self.db.query("SELECT DISTINCT map FROM matches WHERE map IS NOT NULL")}
        played = defaultdict(list)
        for r in self.db.query(
            "SELECT puuid, agent, COUNT(*) AS n FROM match_players WHERE agent IS NOT NULL GROUP BY puuid, agent ORDER BY n DESC"
        ):
            played[r["puuid"]].append(r["agent"])
        return {
            "maps": sorted(set(KNOWN_MAPS) | maps),
            "agents": sorted(set(KNOWN_AGENTS) | agents),
            "played": played,
        }


class Handler(BaseHTTPRequestHandler):
    app = None
    server_version = "FiveStack/1.0"

    def log_request(self, code="-", size="-"):
        # Keep the console readable: only failed requests are printed.
        try:
            if int(code) >= 400:
                print(f"[http] {code} {self.command} {self.path}", flush=True)
        except (TypeError, ValueError):
            pass

    def log_message(self, fmt, *args):
        return

    # ---- helpers ---------------------------------------------------------
    def _send(self, body, status, ctype, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or []):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=200, extra=None):
        self._send(json.dumps(obj, default=str).encode("utf-8"), status, "application/json; charset=utf-8", extra)

    def _html(self, text, status=200, extra=None):
        self._send(text.encode("utf-8"), status, "text/html; charset=utf-8", extra)

    def _redirect(self, location, cookie=None):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _raw_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _body(self):
        raw = self._raw_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return {}

    def client_ip(self):
        forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return self.headers.get("Cf-Connecting-Ip") or forwarded or self.client_address[0]

    def is_https(self):
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _authed(self):
        return self.app.auth.check_cookie(self.headers.get("Cookie"))

    def _login_page(self, error=None, status=200):
        err = f'<p class="error">{html.escape(error)}</p>' if error else ""
        self._html(LOGIN_PAGE.replace("{error}", err), status)

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        rel = os.path.normpath(path.lstrip("/"))
        full = os.path.normpath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.normpath(WEB_DIR)) or not os.path.isfile(full):
            return self._json({"error": "Not found"}, 404)
        ext = os.path.splitext(full)[1].lower()
        # Browsers keep a copy but check it on every load ("no-cache"); the ETag / Last-Modified let an unchanged file
        # come back as a tiny 304 instead of the whole thing, while an edited file (new time or size) is sent again.
        st = os.stat(full)
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        validators = {"ETag": etag, "Last-Modified": formatdate(st.st_mtime, usegmt=True), "Cache-Control": "no-cache"}
        if self._not_modified(etag, st.st_mtime):
            self.send_response(304)
            for k, v in validators.items():
                self.send_header(k, v)
            self.end_headers()
            return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        for k, v in validators.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _not_modified(self, etag, mtime):
        """Whether the browser's cached copy (If-None-Match, else If-Modified-Since) is still the current file."""
        tags = self.headers.get("If-None-Match")
        if tags:
            return tags.strip() == "*" or etag in [t.strip() for t in tags.split(",")]
        since = self.headers.get("If-Modified-Since")
        if since:
            try:
                return int(mtime) <= parsedate_to_datetime(since).timestamp()
            except (TypeError, ValueError, IndexError, OverflowError):
                return False
        return False

    # ---- routing ---------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        qs = {k: v[-1] for k, v in parse_qs(u.query).items()}
        auth = self.app.auth
        try:
            if path == "/login":
                return self._login_page() if auth.enabled else self._redirect("/")
            if path == "/logout":
                return self._redirect("/login" if auth.enabled else "/", cookie=CLEAR_COOKIE)
            if path == "/style.css" or path.startswith("/assets/"):  # the login page uses these too
                return self._static(path)
            if auth.enabled and not self._authed():
                if path.startswith("/api/"):
                    return self._json({"error": "Login required"}, 401)
                return self._redirect("/login")
            if path.startswith("/api/"):
                self._api_get(path, qs)
            else:
                self._static(path)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _api_get(self, path, qs):
        app = self.app
        if path == "/api/status":
            return self._json(app.status())
        if path == "/api/stats":
            return self._json(build_stats(app.db))
        if path == "/api/insights":
            return self._json(build_insights(app.db))
        if path == "/api/forecasts":
            try:
                return self._json(build_forecasts(app.db, app.engine, qs.get("stat") or "acs", qs.get("player") or None,
                                                  qs.get("map") or None, qs.get("agent") or None))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)
        if path == "/api/seasons":
            return self._json({"current": app.db.season_counts(), "seasons": app.db.seasons()})
        if path == "/api/betting-report":
            return self._json(betting_report(app.db, {m["puuid"]: app.rewards.account_name(m) for m in app.db.members()},
                                             app.engine.edge))
        if path == "/api/content":
            return self._json(app.content())
        if path == "/api/roster":
            pool = app.pool_public()
            return self._json({"members": [m for m in pool if m["active"]], "bench": [m for m in pool if not m["active"]],
                               "roster": app.roster_info()})
        if path == "/api/matches":
            matches = app.db.matches(int(qs.get("limit") or 300))
            by = defaultdict(list)
            for p in app.db.all_match_players():
                by[p["match_id"]].append(p)
            for m in matches:
                m["players"] = sorted(by.get(m["match_id"], []), key=lambda p: -(p.get("score") or 0))
                m["ending"] = ending(m)
            return self._json({"matches": matches})
        if path == "/api/recap":
            bettor_of = {m["puuid"]: app.rewards.account_name(m) for m in app.db.members()}
            return self._json(build_recap(app.db, app.engine, qs.get("match") or None, bettor_of, app.rewards.bonus_max))
        if path.startswith("/api/match/"):
            mid = path.rsplit("/", 1)[1]
            m = app.db.match(mid)
            if not m:
                return self._json({"error": "Match not found"}, 404)
            m["players"] = app.db.match_players(mid)
            return self._json(m)
        if path in ("/api/odds", "/api/odds/custom"):
            ctx = {"map": qs.get("map") or None, "agents": {}}
            raw = qs.get("agents")
            if raw:
                try:
                    ctx["agents"] = json.loads(raw)
                except ValueError:
                    for part in raw.split(","):
                        if ":" in part:
                            k, v = part.split(":", 1)
                            ctx["agents"][k] = v
            if path == "/api/odds":
                return self._json(app.bets.mark_streaks(app.engine.build(app.db, ctx)))
            # A custom line's price and reasonable range: ?puuid=&stat=&line=24.5, or an exact number: &exact=25
            # (plus the same map / agents).
            try:
                if qs.get("exact") is not None:
                    alt = (qs.get("stat") or "", qs.get("puuid") or "", float(qs.get("exact") or "nan"), "exact")
                else:
                    alt = (qs.get("stat") or "", qs.get("puuid") or "", float(qs.get("line") or "nan"))
            except ValueError:
                return self._json({"error": "line / exact must be a number"}, 400)
            board = app.engine.build(app.db, ctx, alts=[alt])
            if not board.get("ready"):
                return self._json({"error": board.get("message", "Odds are not available yet.")}, 400)
            if not board["custom"]:
                return self._json({"error": "Unknown player or stat."}, 400)
            return self._json({"market": board["custom"][0], "house_edge": board["house_edge"]})
        if path == "/api/bets":
            return self._json({"bets": app.db.bets(
                status=qs.get("status") or None, bettor=qs.get("bettor") or None, limit=int(qs.get("limit") or 150)
            )})
        if path == "/api/bettors":
            return self._json({"bettors": app.bets.leaderboard()})
        if path == "/api/rewards":
            return self._json({"rewards": app.db.rewards(int(qs.get("limit") or 40))})
        if path == "/api/transfers":
            return self._json({"transfers": app.db.transfers(bettor=qs.get("bettor") or None, limit=int(qs.get("limit") or 50))})
        if path == "/api/bettor/me":
            me = app.auth.current_bettor(self.headers.get("Cookie"), app.db)
            if not me:
                return self._json({"bettor": None})
            pending = app.db.bets(status="pending", bettor=me["name"])
            # The latest wins, newest settled first: the page celebrates the ones settled since it last looked.
            won = sorted(app.db.bets(status="won", bettor=me["name"], limit=200), key=lambda b: b.get("settled_ts") or 0, reverse=True)
            wins = [{k: b.get(k) for k in ("id", "description", "market_type", "stake", "odds_decimal", "payout", "settled_ts")} for b in won[:20]]
            # Credits other bettors sent them lately, newest first: the page says so once ("Matt sent you 50").
            received = app.db.transfers(received_by=me["name"], limit=20)
            return self._json({"bettor": {**app.bets.public(me), "open_bets": len(pending), "bananas": app.bananas.wallet(me["name"]),
                                          "open_stake": round(sum(b["stake"] for b in pending), 2), "recent_wins": wins,
                                          "recent_received": received,
                                          "recent_taxes": app.db.taxes_collected(me["name"], 20),
                                          "member": app.own_member(me)}})
        if path == "/api/shop":
            return self._json(app.bananas.shop(app.auth.current_bettor(self.headers.get("Cookie"), app.db)))
        if path == "/api/arcade":
            return self._json(app.arcade.summary(app.auth.current_bettor(self.headers.get("Cookie"), app.db)))
        if path == "/api/troop":
            return self._json(app.bananas.troop())
        if path == "/api/troop/profile":
            try:
                return self._json(app.bananas.profile(qs.get("name") or ""))
            except BetError as e:
                return self._json({"error": str(e)}, 404)
        return self._json({"error": "Not found"}, 404)

    # ---- the squad roster ---------------------------------------------------
    def _roster_blocked(self):
        """Answer (and return True) when the roster can't be edited: demo mode, no API key, or no admin password."""
        app = self.app
        if app.demo:
            self._json({"error": "Demo mode: the demo squad is fixed."}, 400)
        elif not app.tracker:
            self._json({"error": "Add your HenrikDev API key first (see Setup)."}, 400)
        elif not app.auth.is_admin(self.headers.get("X-Admin-Password")):
            self._json({"error": "Admin password required for that."}, 403)
        else:
            return False
        return True

    def _roster_reply(self, extra, resync):
        """The squad after a change; a full re-scan is started when the games that count may have changed."""
        app = self.app
        if resync:
            app.tracker.request_sync(full=True)
            app.tracker.wake()
        pool = app.pool_public()
        return self._json({"ok": True, "members": [m for m in pool if m["active"]], "bench": [m for m in pool if not m["active"]],
                           "roster": app.roster_info(), **extra})

    def do_POST(self):
        path = urlparse(self.path).path
        app = self.app
        auth = app.auth
        try:
            if path == "/login":
                raw = self._raw_body()
                if not auth.enabled:
                    return self._redirect("/")
                if "json" in (self.headers.get("Content-Type") or ""):
                    try:
                        password = (json.loads(raw.decode("utf-8") or "{}") or {}).get("password")
                    except ValueError:
                        password = None
                else:
                    password = parse_qs(raw.decode("utf-8", "replace")).get("password", [""])[0]
                ok, err = auth.check_password(self.client_ip(), password)
                if ok:
                    return self._redirect("/", cookie=auth.cookie_header(secure=self.is_https()))
                return self._login_page(err, 401)
            if auth.enabled and not self._authed():
                self._raw_body()
                return self._json({"error": "Login required"}, 401)
            body = self._body()
            if path == "/api/sync":
                if not app.tracker:
                    msg = "Demo mode has no live sync." if app.demo else "Not configured: " + " ".join(app.problems)
                    return self._json({"error": msg}, 400)
                if app.tracker.state.get("syncing"):
                    return self._json({"ok": True, "busy": True}, 202)
                app.tracker.request_sync(full=bool(body.get("full")))
                return self._json({"ok": True, "started": True}, 202)
            if path == "/api/roster":  # a new player joins the bench unless `active` is asked for and there's room
                if self._roster_blocked():
                    return
                r = app.tracker.add_member(body.get("riot_id"), body.get("nickname"), active=bool(body.get("active")))
                return self._roster_reply({"member": r["member"], "added": r["added"], "renamed": r["renamed"],
                                           "changes": r.get("changes")}, resync=bool(r.get("changes")))
            if path == "/api/roster/active":  # the whole line-up at once (drag and drop lands here)
                if self._roster_blocked():
                    return
                r = app.tracker.set_active_list(body.get("puuids") or [])
                return self._roster_reply({"changed": r["changed"], "changes": r["changes"]}, resync=r["changed"])
            if path == "/api/bettor/riot-id/active":  # your own entry: onto the squad or onto the bench
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor first."}, 403)
                if app.demo or not app.tracker:
                    return self._json({"error": "The squad can't be changed here (demo mode or no API key)."}, 400)
                r = app.tracker.set_own_active(me["name"], bool(body.get("active")))
                return self._roster_reply({"changed": r["changed"], "changes": r["changes"], "member": app.own_member(me)},
                                          resync=r["changed"])
            if path == "/api/roster/refresh":
                if app.demo or not app.tracker:
                    return self._json({"error": "Renames are only checked with live tracking."}, 400)
                return self._json({"ok": True, "renamed": app.tracker.refresh_names(force=True),
                                   "members": app.members_public()})
            if path.startswith("/api/roster/"):
                if self._roster_blocked():
                    return
                m = app.tracker.set_nickname(unquote(path.rsplit("/", 1)[1]), body.get("nickname"))
                return self._roster_reply({"member": m}, resync=False)
            if path == "/api/bettor/register":
                b = app.bets.register(body.get("name"), body.get("password"))
                app.bananas.earn()  # a new account's starting bananas
                cookie = auth.bettor_cookie(b, self.is_https())
                reply = {"bettor": app.bets.public(b)}
                riot = (body.get("riot_id") or "").strip()
                if riot:  # signing up with a Riot ID puts the new account's owner on the squad
                    r = app.link_riot_id(b, riot, b["name"])
                    if "warning" in r:
                        reply["warning"] = "Account created, but the Riot ID was not added: " + r["warning"]
                    else:
                        reply["member"] = app.own_member(b)
                return self._json(reply, 201, extra=[("Set-Cookie", cookie)])
            if path == "/api/bettor/riot-id":
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor first."}, 403)
                r = app.link_riot_id(me, body.get("riot_id"), body.get("nickname") or "")
                if "warning" in r:
                    return self._json({"error": r["warning"]}, 400)
                return self._json({"ok": True, "member": app.own_member(me), "added": r["added"], "renamed": r["renamed"],
                                   "replaced": r["replaced"], "active": r["active"], "changes": r.get("changes"),
                                   "members": app.members_public()})
            if path == "/api/bettor/login":
                name = (body.get("name") or "").strip()
                key = f"bettor|{self.client_ip()}|{name.lower()}"
                if auth.throttled(key):
                    return self._json({"error": THROTTLE_MSG}, 429)
                try:
                    b = app.bets.authenticate(name, body.get("password"))
                except BetError as e:
                    auth.record(key, False)
                    time.sleep(0.8)
                    return self._json({"error": str(e)}, 403)
                auth.record(key, True)
                cookie = auth.bettor_cookie(b, self.is_https())
                return self._json({"bettor": app.bets.public(b)}, 200, extra=[("Set-Cookie", cookie)])
            if path == "/api/bettor/logout":
                return self._json({"ok": True}, extra=[("Set-Cookie", CLEAR_BETTOR_COOKIE)])
            if path == "/api/bettor/password":
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor first."}, 403)
                b = app.bets.change_password(me["name"], body.get("old"), body.get("new"))
                cookie = auth.bettor_cookie(b, self.is_https())
                return self._json({"bettor": app.bets.public(b)}, extra=[("Set-Cookie", cookie)])
            if path == "/api/bettor/clear-password":
                if not auth.is_configured_admin(self.headers.get("X-Admin-Password")):
                    return self._json({"error": "Set admin_password in config.json and enter it to reset a bettor."}, 403)
                b = app.bets.clear_password(body.get("name"))
                return self._json({"bettor": app.bets.public(b), "bettors": app.bets.leaderboard()})
            if path == "/api/odds/parlay":  # the bet slip's parlay price, before placing it
                legs, _, quote = app.bets.quote_parlay(body.get("legs"), body.get("context") or {})
                return self._json({**quote, "legs": [{k: leg[k] for k in ("market_id", "selection", "description", "odds_decimal")}
                                                     for leg in legs]})
            if path == "/api/transfers":
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to send credits."}, 403)
                transfer = app.bets.send(me["name"], body.get("to"), body.get("amount"), body.get("note"))
                return self._json({"transfer": transfer, "bettor": app.bets.public(app.db.get_bettor(me["name"])),
                                   "bettors": app.bets.leaderboard()}, 201)
            if path == "/api/bets":
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to place bets."}, 403)
                if body.get("legs"):
                    bet = app.bets.place_parlay(me["name"], body.get("legs"), body.get("stake"), body.get("context") or {})
                else:
                    bet = app.bets.place(
                        me["name"], body.get("market_id"), body.get("selection"),
                        body.get("stake"), body.get("context") or {},
                    )
                return self._json({"bet": bet, "bettors": app.bets.leaderboard()}, 201)
            if path in ("/api/arcade/start", "/api/arcade/finish"):
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to play."}, 403)
                if path.endswith("start"):
                    return self._json(app.arcade.start(me["name"], body.get("game")), 201)
                return self._json(app.arcade.finish(me["name"], body.get("token"), body.get("score")))
            if path in ("/api/shop/buy", "/api/shop/equip"):
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to use the shop."}, 403)
                if path == "/api/shop/buy":
                    out = app.bananas.buy(me["name"], body.get("item"), body.get("target"), body.get("text"))
                else:
                    out = {"worn": app.bananas.equip(me["name"], body.get("slot"), body.get("item"))}
                return self._json({**out, "shop": app.bananas.shop(me)}, 201 if path.endswith("buy") else 200)
            if path == "/api/bettors/reset":
                if not auth.is_admin(self.headers.get("X-Admin-Password")):
                    return self._json({"error": "Admin password required for that."}, 403)
                if str(body.get("confirm") or "").strip().upper() != "RESET":
                    return self._json({"error": "Type RESET to confirm ending the season."}, 400)
                season = app.bets.reset()
                app.bananas.earn()  # the new season's starting bananas (the reset zeroed every wallet)
                return self._json({"ok": True, "season": season, "bettors": app.bets.leaderboard()})
            return self._json({"error": "Not found"}, 404)
        except (BetError, TrackerError) as e:
            return self._json({"error": str(e)}, 400)
        except HenrikError as e:
            return self._json({"error": f"HenrikDev: {e.message}"}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if self.app.auth.enabled and not self._authed():
                return self._json({"error": "Login required"}, 401)
            if path == "/api/bettor/riot-id":  # leave the squad
                app, auth = self.app, self.app.auth
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor first."}, 403)
                if app.demo or not app.tracker:
                    return self._json({"error": "The squad can't be changed here (demo mode or no API key)."}, 400)
                changes = app.tracker.unlink_member(me["name"])
                return self._roster_reply({"changes": changes}, resync=changes["was_active"])
            if path.startswith("/api/roster/"):
                if self._roster_blocked():
                    return
                changes = self.app.tracker.remove_member(unquote(path.rsplit("/", 1)[1]))
                return self._roster_reply({"changes": changes}, resync=changes["was_active"])
            if path.startswith("/api/bets/"):
                auth = self.app.auth
                me = auth.current_bettor(self.headers.get("Cookie"), self.app.db)
                admin = auth.is_configured_admin(self.headers.get("X-Admin-Password"))
                bet = self.app.bets.cancel(int(path.rsplit("/", 1)[1]), by=me["name"] if me else None, admin=admin)
                return self._json({"bet": bet, "bettors": self.app.bets.leaderboard()})
            return self._json({"error": "Not found"}, 404)
        except (BetError, TrackerError) as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)


def main():
    demo = "--demo" in sys.argv
    config_path = CONFIG_PATH
    for arg in sys.argv:
        if arg.startswith("--config="):
            config_path = os.path.abspath(arg.split("=", 1)[1])
    cfg = load_config(config_path)
    host = cfg.get("host") or "127.0.0.1"
    port = int(cfg.get("port") or 8080)
    for arg in sys.argv:
        if arg.startswith("--port="):
            port = int(arg.split("=", 1)[1])
    tunnel_mode = (cfg.get("tunnel") or "off").lower()
    if "--tunnel" in sys.argv:
        tunnel_mode = "quick" if tunnel_mode == "off" else tunnel_mode
    if "--no-tunnel" in sys.argv:
        tunnel_mode = "off"

    app = App(cfg, demo=demo, port=port)
    Handler.app = app
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True

    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1', '') else host}:{port}"
    print(f"5-Stack Tracker {'(DEMO MODE) ' if demo else ''}running at {url}")
    if app.problems:
        print("Setup needed before live tracking works:")
        for p in app.problems:
            print(f"  - {p}")
    elif app.tracker:
        app.tracker.start()
    if app.auth.enabled:
        print("Site password is on: visitors log in at /login.")
    if tunnel_mode != "off":
        if not app.auth.enabled:
            msg = ('Tunnel NOT started: set "site_password" in config.json first so the public link is protected.')
            print(msg)
            app.tunnel.state.update({"mode": tunnel_mode, "status": "error", "error": msg})
        else:
            app.tunnel.start(tunnel_mode)
    if cfg.get("open_browser", True) and "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        app.tunnel.stop()


if __name__ == "__main__":
    main()
