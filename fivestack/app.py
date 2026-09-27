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
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .auth import CLEAR_BETTOR_COOKIE, CLEAR_COOKIE, THROTTLE_MSG, Auth
from .bets import BetError, BetManager
from .config import CONFIG_PATH, DATA_DIR, TOOLS_DIR, WEB_DIR, config_problems, load_bettor_names, load_config, mask
from .db import DB
from .gamestate import ending
from .henrik import HenrikClient
from .insights import build_insights
from .odds import OddsEngine
from .rewards import RewardManager
from .stats import build_stats
from .tracker import Tracker
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
    ".ico": "image/x-icon",
}

LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>5-Stack Tracker · Log in</title><link rel="stylesheet" href="/style.css"></head>
<body><main class="container login"><form method="post" action="/login" class="card">
<div class="brand"><span class="logo" aria-hidden="true">5S</span><div><h1>5-Stack Tracker</h1><div class="sub">Enter the squad password</div></div></div>
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
        self.auth = Auth(cfg, self.db)
        self.tunnel = Tunnel(cfg, port, TOOLS_DIR)
        self.problems = [] if demo else config_problems(cfg)
        self.client = None
        self.tracker = None
        if demo:
            from . import demo_seed
            demo_seed.seed(self.db)
        elif not self.problems:
            self.client = HenrikClient(cfg["api_key"].strip(), min_interval=float(cfg.get("min_request_interval_s", 1.5)))
            self.tracker = Tracker(cfg, self.db, self.client, on_new_matches=self.on_new_matches)
        self.started = time.time()

    def on_new_matches(self, matches):
        settled = []
        for m in matches:
            players = self.db.match_players(m["match_id"])
            settled.extend(self.bets.settle_for_match(m, players))
            paid = self.rewards.pay_for_match(m, players)
            if paid:
                print(f"[rewards] {m['map']} {m['result']}: " + ", ".join(f"{r['bettor']} +{r['base'] + r['bonus']:.0f}" for r in paid), flush=True)
        return settled

    def status(self):
        members = [
            {"puuid": m["puuid"], "name": m["name"], "tag": m["tag"], "nickname": m.get("nickname") or m["name"]}
            for m in self.db.members()
        ]
        log = list(self.tunnel.logs)
        st = {
            "configured": not self.problems,
            "demo": self.demo,
            "problems": self.problems,
            "region": self.cfg.get("region"),
            "modes": self.cfg.get("modes") or [],
            "poll_interval_minutes": self.cfg.get("poll_interval_minutes", 10),
            "members": members,
            "expected_members": len(self.cfg.get("members") or []),
            "games": self.db.count_matches(),
            "record": self.db.record(),
            "api_key_masked": mask(self.cfg.get("api_key")),
            "uptime_s": round(time.time() - self.started),
            "house_edge": self.engine.edge,
            "starting_balance": self.bets.starting,
            "game_reward": self.rewards.game,
            "win_reward": self.rewards.win,
            "performance_bonus_max": self.rewards.bonus_max,
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
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

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
            if path == "/style.css":
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
        if path == "/api/content":
            return self._json(app.content())
        if path == "/api/matches":
            matches = app.db.matches(int(qs.get("limit") or 300))
            by = defaultdict(list)
            for p in app.db.all_match_players():
                by[p["match_id"]].append(p)
            for m in matches:
                m["players"] = sorted(by.get(m["match_id"], []), key=lambda p: -(p.get("score") or 0))
                m["ending"] = ending(m)
            return self._json({"matches": matches})
        if path.startswith("/api/match/"):
            mid = path.rsplit("/", 1)[1]
            m = app.db.match(mid)
            if not m:
                return self._json({"error": "Match not found"}, 404)
            m["players"] = app.db.match_players(mid)
            return self._json(m)
        if path == "/api/odds":
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
            return self._json(app.engine.build(app.db, ctx))
        if path == "/api/bets":
            return self._json({"bets": app.db.bets(
                status=qs.get("status") or None, bettor=qs.get("bettor") or None, limit=int(qs.get("limit") or 150)
            )})
        if path == "/api/bettors":
            return self._json({"bettors": app.bets.leaderboard()})
        if path == "/api/rewards":
            return self._json({"rewards": app.db.rewards(int(qs.get("limit") or 40))})
        if path == "/api/bettor/me":
            me = app.auth.current_bettor(self.headers.get("Cookie"), app.db)
            return self._json({"bettor": app.bets.public(me) if me else None})
        return self._json({"error": "Not found"}, 404)

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
            if path == "/api/bettor/register":
                b = app.bets.register(body.get("name"), body.get("password"))
                cookie = auth.bettor_cookie(b, self.is_https())
                return self._json({"bettor": app.bets.public(b)}, 201, extra=[("Set-Cookie", cookie)])
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
            if path == "/api/bets":
                me = auth.current_bettor(self.headers.get("Cookie"), app.db)
                if not me:
                    return self._json({"error": "Sign in as a bettor to place bets."}, 403)
                bet = app.bets.place(
                    me["name"], body.get("market_id"), body.get("selection"),
                    body.get("stake"), body.get("context") or {},
                )
                return self._json({"bet": bet, "bettors": app.bets.leaderboard()}, 201)
            if path == "/api/bettors/reset":
                if not auth.is_admin(self.headers.get("X-Admin-Password")):
                    return self._json({"error": "Admin password required for that."}, 403)
                app.bets.reset()
                return self._json({"ok": True, "bettors": app.bets.leaderboard()})
            return self._json({"error": "Not found"}, 404)
        except BetError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_DELETE(self):
        path = urlparse(self.path).path
        try:
            if self.app.auth.enabled and not self._authed():
                return self._json({"error": "Login required"}, 401)
            if path.startswith("/api/bets/"):
                auth = self.app.auth
                me = auth.current_bettor(self.headers.get("Cookie"), self.app.db)
                admin = auth.is_configured_admin(self.headers.get("X-Admin-Password"))
                bet = self.app.bets.cancel(int(path.rsplit("/", 1)[1]), by=me["name"] if me else None, admin=admin)
                return self._json({"bet": bet, "bettors": self.app.bets.leaderboard()})
            return self._json({"error": "Not found"}, 404)
        except BetError as e:
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
