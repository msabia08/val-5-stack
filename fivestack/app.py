"""5-Stack Tracker web server.

    python server.py                 # normal mode (needs config.json with API key + members)
    python server.py --tunnel        # also publish it online through a Cloudflare quick tunnel
    python server.py --demo          # explore the UI with synthetic data, no API key needed
    python server.py --no-browser --port=8090 --no-tunnel
"""
import html
import secrets
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
from .blackjack import BlackjackManager
from .roulette import RouletteManager
from .crash import CrashManager
from .house import HouseManager
from .poker import PokerManager
from .slots import DEMO_GOLDEN_BOOST, SlotManager
from .stampede import StampedeManager
from .auth import CLEAR_BETTOR_COOKIE, CLEAR_COOKIE, THROTTLE_MSG, Auth
from .bananas import BananaManager
from .bank import BankManager
from .hunt import HuntManager
from .bets import TAX_MIN_TRANSFER, TAX_RATE, BetError, BetManager
from .config import CONFIG_PATH, DATA_DIR, TOOLS_DIR, WEB_DIR, config_problems, load_bettor_names, load_config, mask
from .db import DB
from .discord import Discord, fresh, game_message, jackpot_message, test_message
from .forecasts import build_forecasts
from .gamestate import ending
from .henrik import HenrikClient, HenrikError
from .wheel import WheelManager
from .insights import betting_report, build_insights
from .odds import OddsEngine
from .recap import build_recap
from .rewards import RewardManager
from .stats import build_stats
from .tracker import MODES, ROSTER_MAX, ROSTER_MIN, Tracker, TrackerError
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
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
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
</form></main>{watcher}</body></html>"""
# One visit to the login page in LOGIN_WATCHER_ODDS, the scientist (docs/onkey-lore.md) is watching from the corner.
LOGIN_WATCHER_ODDS = 20
LOGIN_WATCHER = '<img class="login-watcher" src="/assets/scientist.png" alt="" aria-hidden="true">'


class App:
    def __init__(self, cfg, demo=False, port=8080):
        self.cfg = cfg
        self.demo = demo
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db = DB(os.path.join(DATA_DIR, "demo.db" if demo else "tracker.db"))
        purged = self.db.purge_other_modes(MODES)  # only Competitive counts (tracker.MODES); older databases may hold more
        if purged["games"] or purged["lines"]:
            print(f"[tracker] Only Competitive counts now: dropped {purged['games']} game(s) and {purged['lines']} member line(s) "
                  "from other modes", flush=True)
        self.engine = OddsEngine(cfg)
        self.bets = BetManager(cfg, self.db, self.engine)
        self.rewards = RewardManager(cfg, self.db, load_bettor_names())
        self.bananas = BananaManager(cfg, self.db, self.bets, accounts=self.member_accounts)
        self.bank = BankManager(cfg, self.db)
        self.hunt = HuntManager(self.db, cfg)
        self.arcade = ArcadeManager(self.db)
        self.slots = SlotManager(self.db, golden_boost=DEMO_GOLDEN_BOOST if demo else 1)  # demo: Golden Onkeys to test
        self.stampede = StampedeManager(self.db, demo=demo)  # demo: a spin can ask for a feature
        self.house = HouseManager(self.db, self.bets, self.rewards)  # the take (bets and the casino) and what it gives back
        self.wheel = WheelManager(self.db, self.house, unlimited=demo)  # demo: spin as often as you like
        self.blackjack = BlackjackManager(self.db)
        self.roulette = RouletteManager(self.db)
        self.crash = CrashManager(self.db)
        self.poker = PokerManager(self.db)
        self.auth = Auth(cfg, self.db)
        # Posts to the squad's Discord channel (game results, jackpots). Never in demo mode.
        self.discord = Discord("" if demo else cfg.get("discord_webhook"))
        if self.discord.ignored:
            print("[discord] discord_webhook isn't a Discord webhook URL (https://discord.com/api/webhooks/...): nothing will be posted", flush=True)
        self.tunnel = Tunnel(cfg, port, TOOLS_DIR)
        self.problems = [] if demo else config_problems(cfg)
        self.client = None
        self.tracker = None
        if demo:
            from . import demo_seed
            demo_seed.seed(self.db)
            self.db.set_meta("bananas_since", 0)  # every demo game pays bananas
            self.bananas.earn()
            demo_seed.seed_shop(self.db, self.bananas)
            demo_seed.seed_house(self.db, self.house)
            demo_seed.seed_bank(self.db, self.bank)
            demo_seed.seed_hunt(self.db)
        elif not self.problems:
            self.client = HenrikClient(cfg["api_key"].strip(), min_interval=float(cfg.get("min_request_interval_s", 1.5)))
            self.tracker = Tracker(cfg, self.db, self.client, on_new_matches=self.on_new_matches, on_sync=self.on_sync)
        self.bananas.earn()  # games played since the last start (or stored by a sync that stopped part-way)
        self.house.ensure_objectives()  # the next game's secret objectives, once the pot can pay for them
        self.started = time.time()

    def start_casino_clock(self, interval=0.25):
        """Run the live tables' clocks (turn timers, betting windows, the pause between hands) on a daemon thread."""
        def loop():
            while True:
                for manager in (self.blackjack, self.poker, self.roulette, self.crash):
                    try:
                        manager.tick()
                    except Exception as e:  # a clock failure must never stop the others
                        print(f"[casino] {type(manager).__name__}.tick failed: {e}", flush=True)
                time.sleep(interval)
        threading.Thread(target=loop, name="casino-clock", daemon=True).start()

    def with_looks(self, view):
        """A casino table's view plus the shop looks of everyone at it (and the viewer), so the page draws their
        name colours, badges, card backs, chips and seats. Fresh with every answer, so a purchase shows at once."""
        names = {s["bettor"].lower() for s in view.get("seats", []) if s}
        names |= {e["bettor"].lower() for e in view.get("log", []) if e.get("bettor")}
        if view.get("me"):
            names.add(view["me"]["name"].lower())
        looks = self.bananas.looks()
        view["looks"] = {n: looks[n] for n in names if n in looks}
        return view

    def reset_season(self):
        """End the season: the casino tables close first (open blackjack hands refunded, poker seats cashed out), with
        their locks held so nobody sits back down before the reset is done."""
        with self.blackjack.lock, self.poker.lock, self.roulette.lock, self.crash.lock:
            self.blackjack.void_open("The season ended")
            self.crash.void_open("The season ended")
            self.roulette.void_open("The season ended")
            self.poker.close_all("The season ended")
            return self.bets.reset()

    def on_new_matches(self, matches):
        settled, by_match = [], {}
        for m in matches:
            players = self.db.match_players(m["match_id"])
            done = self.bets.settle_for_match(m, players)
            settled.extend(done)
            by_match[m["match_id"]] = done
            paid = self.rewards.pay_for_match(m, players)
            if paid:
                print(f"[rewards] {m['map']} {m['result']}: " + ", ".join(f"{r['bettor']} +{r['base'] + r['bonus']:.0f}" for r in paid), flush=True)
            given = self.house.after_match(m, players, done)  # bad-beat refunds and the secret objectives
            if given:
                print(f"[house] {m['map']}: " + ", ".join(f"{g['bettor']} +{g['amount']:g} ({g['kind']})" for g in given), flush=True)
        self.bananas.earn()  # bananas for the games just stored
        self.announce_games(matches, by_match)
        return settled

    def announce_games(self, matches, settled_by_match):
        """Post the new games to Discord (discord.py): the recent ones only, each with its top highlight and the
        bets settled on it. A failure here never touches settlement."""
        if not self.discord.enabled:
            return
        try:
            bettor_of = {m["puuid"]: self.rewards.account_name(m) for m in self.db.members()}
            for m in fresh(matches):
                recap = build_recap(self.db, self.engine, m["match_id"], bettor_of, self.rewards.bonus_max) or {}
                highlights = recap.get("highlights") or []
                self.discord.send(game_message(m, recap.get("players"), highlights[0] if highlights else None,
                                               settled_by_match.get(m["match_id"])))
        except Exception as e:
            print(f"[discord] could not build the game post: {e}", flush=True)

    def on_sync(self, summary):
        """After every sync, new squad games or not: bananas for every game each member played."""
        self.bananas.earn()

    def member_accounts(self):
        """Each active member's bettor account (created unclaimed if missing): who gets their bananas per game."""
        return {m["puuid"]: self.rewards.bettor_for(m) for m in self.db.members()}

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
            "discord": self.discord.enabled,  # a webhook is set: games and jackpots are posted
            "problems": self.problems,
            "region": self.cfg.get("region"),
            "modes": sorted(MODES),
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
            "banana_per_game": self.bananas.per_game,
            "loan_max": self.bank.max,
            "loan_interest": self.bank.interest,
            "hunt_daily_max": self.hunt.daily_max,
            "hunt_floor": self.hunt.floor,
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


class Server(ThreadingHTTPServer):
    """The stdlib threading server with a longer queue of waiting connections. Its default, request_queue_size 5, is
    the listen() backlog: how many connections can wait to be accepted at once. A page load opens about a dozen at the
    same moment (the scripts, the stylesheet and the first API calls), and on Windows the ones past the backlog are
    refused outright instead of retried, which left the page half-loaded with a script missing."""
    request_queue_size = 64
    daemon_threads = True


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

    @staticmethod
    def _casino_post(app, name, path, body):
        bj, pk, rl = app.blackjack, app.poker, app.roulette
        which = body.get("table") or "solo"
        routes = {
            "/api/blackjack/sit": lambda: bj.sit(name, body.get("seats")),
            "/api/blackjack/leave": lambda: bj.leave(name),
            "/api/blackjack/bet": lambda: bj.bet(name, which, body.get("stake"), body.get("request_id"), body.get("side")),
            "/api/blackjack/emote": lambda: bj.emote(name, which, body.get("emote")),
            "/api/blackjack/tip": lambda: bj.tip(name, which, body.get("amount")),
            "/api/blackjack/action": lambda: bj.action(name, which, body.get("action"), body.get("step")),
            "/api/roulette/spin": lambda: app.with_looks(rl.spin(name, body.get("bets"), body.get("request_id"))),
            "/api/roulette/bet": lambda: app.with_looks(rl.bet(name, body.get("bets"), body.get("request_id"))),
            "/api/crash/bet": lambda: app.with_looks(app.crash.bet(name, body.get("stake"), body.get("auto"), body.get("request_id"))),
            "/api/crash/cashout": lambda: app.with_looks(app.crash.cashout(name)),
            "/api/poker/sit": lambda: pk.sit(name, body.get("buyin")),
            "/api/poker/leave": lambda: pk.leave(name),
            "/api/poker/ready": lambda: pk.set_ready(name, body.get("ready")),
            "/api/poker/settings": lambda: pk.update_settings(name, body.get("settings")),
            "/api/poker/topup": lambda: pk.topup(name, body.get("amount")),
            "/api/poker/action": lambda: pk.act(name, body.get("action"), body.get("amount"), body.get("hand"), body.get("step")),
        }
        if path not in routes:
            raise BetError("Unknown casino action.")
        return routes[path]()

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
        watcher = LOGIN_WATCHER if secrets.randbelow(LOGIN_WATCHER_ODDS) == 0 else ""
        self._html(LOGIN_PAGE.replace("{error}", err).replace("{watcher}", watcher), status)

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
            recap = build_recap(app.db, app.engine, qs.get("match") or None, bettor_of, app.rewards.bonus_max)
            if recap:
                recap["house"] = app.house.for_match(recap["match"]["match_id"])
            return self._json(recap)
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
                return self._json(app.bets.apply_boost(app.bets.mark_streaks(app.engine.build(app.db, ctx))))
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
                                          "recent_giveaways": app.house.payouts(me["name"], 20),
                                          # The daily wheel: whether a spin is waiting (the page's nav dot and chip).
                                          "wheel_ready": app.wheel.spins_left(me["name"]) > 0,
                                          "tokens": app.wheel.token_counts(me["name"]),  # the bet slip's Boost / Insure toggles
                                          # The scientist's offer for Onkey: made to a bettor who's nearly broke, until they refuse it.
                                          "scientist_offer": app.bananas.offer_open(me["name"], me["balance"]),
                                          # Their last settled bets, newest first: Onkey reacts to new ones and losing runs.
                                          "recent_settled": [{k: b.get(k) for k in ("id", "status", "description", "stake", "payout",
                                                                                    "odds_decimal", "settled_ts")}
                                                             for b in sorted((b for b in app.db.bets(bettor=me["name"], limit=200)
                                                                              if b["status"] in ("won", "lost", "void")),
                                                                             key=lambda b: b.get("settled_ts") or 0, reverse=True)[:12]],
                                          "member": app.own_member(me)}})
        if path == "/api/bank":
            me = app.auth.current_bettor(self.headers.get("Cookie"), app.db)
            return self._json({"bank": app.bank.terms(), "me": app.bank.status(me["name"]) if me else None})
        if path == "/api/hunt":
            return self._json(app.hunt.summary(app.auth.current_bettor(self.headers.get("Cookie"), app.db)))
        if path == "/api/shop":
            return self._json(app.bananas.shop(app.auth.current_bettor(self.headers.get("Cookie"), app.db)))
        if path == "/api/arcade":
            return self._json(app.arcade.summary(app.auth.current_bettor(self.headers.get("Cookie"), app.db)))
        if path == "/api/house":
            return self._json(app.house.report())
        if path == "/api/wheel":
            return self._json(app.wheel.summary(app.auth.current_bettor(self.headers.get("Cookie"), app.db)))
        if path == "/api/slots":
            return self._json(app.slots.summary(app.auth.current_bettor(self.headers.get("Cookie"), app.db)))
        if path == "/api/stampede":
            return self._json(app.stampede.summary(app.auth.current_bettor(self.headers.get("Cookie"), app.db)))
        if path == "/api/stampede/pots":
            return self._json({"pots": app.stampede.pots(), "jackpot_log": app.stampede.jackpot_log()})
        if path in ("/api/blackjack", "/api/poker"):
            me = app.auth.current_bettor(self.headers.get("Cookie"), app.db)
            name = me["name"] if me else None
            try:
                since = int(qs["since"]) if "since" in qs else None
            except ValueError:
                since = None
            if path == "/api/poker":
                if since is not None:  # long-poll: answer when the table changes, or after 20 seconds
                    app.poker.wait(app.poker.table, since, 20)
                return self._json(app.with_looks(app.poker.view(name)))
            which = qs.get("table") or "solo"
            if which not in ("solo", "shared"):
                return self._json({"error": "Pick the solo table or the shared table."}, 400)
            if since is not None and which == "shared":
                app.blackjack.wait(app.blackjack.tables["shared"], since, 20)
            if which == "solo" and not name:
                return self._json(app.blackjack.view(None, "shared") | {"table": "solo", "seats": [], "log": [], "looks": {}})
            return self._json(app.with_looks(app.blackjack.view(name, which)))
        if path == "/api/roulette":
            me = app.auth.current_bettor(self.headers.get("Cookie"), app.db)
            name = me["name"] if me else None
            which = qs.get("table") or "solo"
            if which not in ("solo", "shared"):
                return self._json({"error": "Pick the solo table or the shared table."}, 400)
            try:
                since = int(qs["since"]) if "since" in qs else None
            except ValueError:
                since = None
            if since is not None and which == "shared":  # long-poll: answer when the table changes, or after 20 seconds
                app.roulette.wait(app.roulette.tables["shared"], since, 20)
            if which == "solo" and not name:  # signed out: the layout, with nothing on it
                return self._json(app.roulette.view(None, "shared") | {"table": "solo", "seats": [], "log": [], "history": [],
                                                                       "phase": "betting", "result": None, "looks": {}})
            return self._json(app.with_looks(app.roulette.view(name, which)))
        if path == "/api/crash":
            me = app.auth.current_bettor(self.headers.get("Cookie"), app.db)
            try:
                since = int(qs["since"]) if "since" in qs else None
            except ValueError:
                since = None
            if since is not None:  # long-poll: answer when the round moves, or after 20 seconds
                app.crash.wait(app.crash.table, since, 20)
            return self._json(app.with_looks(app.crash.view(me["name"] if me else None)))
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
                return self._json({**quote, "legs": [{**{k: leg[k] for k in ("market_id", "selection", "description", "odds_decimal", "boost")},
                                                      "boost_token": bool(leg.get("boost_token"))} for leg in legs]})
            if path == "/api/scientist/refuse":  # "Onkey is not for sale": the answer to his offer, and a title for it
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in first."}, 403)
                return self._json({"item": app.bananas.refuse_offer(me["name"])})
            if path in ("/api/hunt/start", "/api/hunt/click", "/api/hunt/next"):
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to hunt bananas."}, 403)
                if path.endswith("/start"):
                    return self._json({"me": app.hunt.start(me["name"])})
                if path.endswith("/next"):  # the page's timer ran out on a golden banana, a bunch or Greg
                    return self._json(app.hunt.nudge(me["name"]))
                out = app.hunt.click(me["name"], body.get("x"), body.get("y"), air=body.get("air"), shoo=bool(body.get("shoo")),
                                     claw=body.get("claw"))
                return self._json({**out, "balance": round(app.db.get_bettor(me["name"])["balance"], 2)})
            if path in ("/api/bank/borrow", "/api/bank/repay"):
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to use the bank."}, 403)
                if path.endswith("/borrow"):
                    out = {"loan": app.bank.borrow(me["name"], body.get("amount"))}
                else:
                    out = {"repaid": app.bank.repay(me["name"], body.get("amount"))}
                return self._json({**out, "me": app.bank.status(me["name"]), "bank": app.bank.terms(),
                                   "bettor": app.bets.public(app.db.get_bettor(me["name"])), "bettors": app.bets.leaderboard()}, 201)
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
                        body.get("stake"), body.get("context") or {}, tokens=body.get("tokens"),
                    )
                return self._json({"bet": bet, "bettors": app.bets.leaderboard()}, 201)
            if path == "/api/wheel/spin":
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to spin the wheel."}, 403)
                spun = app.wheel.spin(me["name"], force=body.get("segment"))  # `segment` is honoured in demo mode only
                if spun.get("prize") == "jackpot" and (spun.get("amount") or 0) > 0:
                    app.discord.send(jackpot_message(spun["bettor"], spun["amount"]))
                return self._json(spun)
            if path == "/api/slots/spin":
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to spin."}, 403)
                return self._json(app.slots.spin(me["name"], body.get("machine"), body.get("stake"), body.get("request_id")))
            if path == "/api/stampede/spin":
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to spin."}, 403)
                return self._json(app.stampede.spin(me["name"], body.get("stake"), body.get("request_id"),
                                                    force=body.get("force")))  # honoured in demo mode only
            if path.startswith(("/api/blackjack/", "/api/poker/", "/api/roulette/", "/api/crash/")):
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to play."}, 403)
                return self._json(self._casino_post(app, me["name"], path, body))
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
                season = app.reset_season()
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
    if "--discord-test" in sys.argv:  # send one test message to the configured webhook and exit
        hook = Discord(cfg.get("discord_webhook"), background=False)
        if not hook.enabled:
            print("Set discord_webhook in config.json to a Discord webhook URL (https://discord.com/api/webhooks/...) first.")
            sys.exit(1)
        ok = hook.send(test_message())
        print("Sent a test message to Discord." if ok else "Discord did not accept the message (see the line above).")
        sys.exit(0 if ok else 1)
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
    srv = Server((host, port), Handler)

    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1', '') else host}:{port}"
    print(f"5-Stack Tracker {'(DEMO MODE) ' if demo else ''}running at {url}")
    if app.problems:
        print("Setup needed before live tracking works:")
        for p in app.problems:
            print(f"  - {p}")
    elif app.tracker:
        app.tracker.start()
    app.start_casino_clock()
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
