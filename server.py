"""5-Stack Tracker web server.

    python server.py                 # normal mode (needs config.json with API key + members)
    python server.py --tunnel        # also publish it online through a Cloudflare quick tunnel
    python server.py --demo          # explore the UI with synthetic data, no API key needed
    python server.py --no-browser --port=8090 --no-tunnel
"""
import hashlib
import hmac
import html
import json
import os
import secrets
import shutil
import sys
import threading
import time
import traceback
import webbrowser
from collections import defaultdict
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from bets import BetError, BetManager
from db import DB
from henrik import HenrikClient
from odds import OddsEngine
from stats import build_stats
from tracker import Tracker
from tunnel import Tunnel

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")
TOOLS_DIR = os.path.join(ROOT, "tools")
CONFIG_PATH = os.path.join(ROOT, "config.json")
EXAMPLE_PATH = os.path.join(ROOT, "config.example.json")

KNOWN_MAPS = ["Abyss", "Ascent", "Bind", "Breeze", "Corrode", "Fracture", "Haven", "Icebox", "Lotus", "Pearl", "Split", "Sunset"]
KNOWN_AGENTS = [
    "Astra", "Breach", "Brimstone", "Chamber", "Clove", "Cypher", "Deadlock", "Fade", "Gekko", "Harbor",
    "Iso", "Jett", "KAY/O", "Killjoy", "Neon", "Omen", "Phoenix", "Raze", "Reyna", "Sage", "Skye", "Sova",
    "Tejo", "Viper", "Vyse", "Waylay", "Yoru",
]
PLACEHOLDER_IDS = {"friend1#tag1", "friend2#tag2", "friend3#tag3", "friend4#tag4", "yourname#tag"}
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}
COOKIE = "fs_auth"
CLEAR_COOKIE = f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"

LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>5-Stack Tracker · Log in</title><link rel="stylesheet" href="/style.css"></head>
<body><main class="container login"><form method="post" action="/login" class="card">
<div class="brand"><span class="logo" aria-hidden="true">5S</span><div><h1>5-Stack Tracker</h1><div class="sub">Enter the squad password</div></div></div>
{error}
<label>Password<input type="password" name="password" autofocus autocomplete="current-password" required></label>
<button class="btn primary" type="submit">Log in</button>
</form></main></body></html>"""


def load_config(path=CONFIG_PATH):
    if not os.path.exists(path):
        shutil.copy(EXAMPLE_PATH, path)
        print(f"Created {path}. Add your HenrikDev API key and your squad's Riot IDs, then restart.")
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        print(f"{os.path.basename(path)} is not valid JSON: {e}")
        sys.exit(1)
    env_key = os.environ.get("HENRIK_API_KEY")
    if env_key:
        cfg["api_key"] = env_key
    return cfg


def config_problems(cfg):
    problems = []
    key = (cfg.get("api_key") or "").strip()
    if not key or "PASTE" in key.upper() or "YOUR" in key.upper():
        problems.append("Add your HenrikDev API key to config.json (api_key).")
    members = cfg.get("members") or []
    ids = [(m.get("riot_id") if isinstance(m, dict) else m) or "" for m in members]
    bad = [i for i in ids if "#" not in str(i) or str(i).strip().lower() in PLACEHOLDER_IDS]
    if len(members) < 2:
        problems.append("List your squad's Riot IDs (Name#TAG) under members in config.json.")
    elif bad:
        problems.append("Replace the placeholder members in config.json with real Riot IDs: " + ", ".join(str(b) for b in bad))
    if (cfg.get("region") or "").lower() not in {"na", "eu", "ap", "kr", "latam", "br"}:
        problems.append("region must be one of na, eu, ap, kr, latam, br.")
    return problems


def mask(key):
    key = key or ""
    if len(key) < 10:
        return "set" if key else "not set"
    return key[:5] + "..." + key[-3:]


class Auth:
    """Shared squad password -> signed cookie. Optional separate admin password for destructive actions."""

    def __init__(self, cfg, db):
        self.password = (cfg.get("site_password") or "").strip()
        self.admin_password = (cfg.get("admin_password") or "").strip()
        secret = db.get_meta("cookie_secret")
        if not secret:
            secret = secrets.token_hex(32)
            db.set_meta("cookie_secret", secret)
        self.secret = secret.encode()
        self.failures = {}
        self.lock = threading.Lock()

    @property
    def enabled(self):
        return bool(self.password)

    def token(self):
        return hmac.new(self.secret, self.password.encode(), hashlib.sha256).hexdigest()

    def cookie_header(self, secure):
        return f"{COOKIE}={self.token()}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")

    def check_cookie(self, header):
        if not self.enabled:
            return True
        jar = cookies.SimpleCookie()
        try:
            jar.load(header or "")
        except cookies.CookieError:
            return False
        morsel = jar.get(COOKIE)
        return bool(morsel) and hmac.compare_digest(morsel.value, self.token())

    def check_password(self, ip, candidate):
        with self.lock:
            count, until = self.failures.get(ip, (0, 0.0))
            if until > time.time():
                return False, "Too many attempts. Try again in a few minutes."
        ok = hmac.compare_digest((candidate or "").strip(), self.password)
        with self.lock:
            if ok:
                self.failures.pop(ip, None)
            else:
                count += 1
                self.failures[ip] = (count, time.time() + 600 if count >= 8 else 0.0)
        if not ok:
            time.sleep(0.8)
        return ok, (None if ok else "Wrong password.")

    def is_admin(self, candidate):
        if not self.admin_password:
            return True
        return hmac.compare_digest((candidate or "").strip(), self.admin_password)


class App:
    def __init__(self, cfg, demo=False, port=8080):
        self.cfg = cfg
        self.demo = demo
        os.makedirs(DATA_DIR, exist_ok=True)
        self.db = DB(os.path.join(DATA_DIR, "demo.db" if demo else "tracker.db"))
        self.engine = OddsEngine(cfg)
        self.bets = BetManager(cfg, self.db, self.engine)
        self.auth = Auth(cfg, self.db)
        self.tunnel = Tunnel(cfg, port, TOOLS_DIR)
        self.problems = [] if demo else config_problems(cfg)
        self.client = None
        self.tracker = None
        if demo:
            import demo_seed
            demo_seed.seed(self.db)
        elif not self.problems:
            self.client = HenrikClient(cfg["api_key"].strip(), min_interval=float(cfg.get("min_request_interval_s", 1.5)))
            self.tracker = Tracker(cfg, self.db, self.client, on_new_matches=self.on_new_matches)
        self.started = time.time()

    def on_new_matches(self, matches):
        settled = []
        for m in matches:
            settled.extend(self.bets.settle_for_match(m, self.db.match_players(m["match_id"])))
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
        if path == "/api/content":
            return self._json(app.content())
        if path == "/api/matches":
            matches = app.db.matches(int(qs.get("limit") or 300))
            by = defaultdict(list)
            for p in app.db.all_match_players():
                by[p["match_id"]].append(p)
            for m in matches:
                m["players"] = sorted(by.get(m["match_id"], []), key=lambda p: -(p.get("score") or 0))
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
            if path == "/api/bets":
                bet = app.bets.place(
                    body.get("bettor"), body.get("market_id"), body.get("selection"),
                    body.get("stake"), body.get("context") or {},
                )
                return self._json({"bet": bet, "bettors": app.bets.leaderboard()}, 201)
            if path == "/api/bettors":
                b = app.bets.ensure_bettor(body.get("name"))
                return self._json({"bettor": b, "bettors": app.bets.leaderboard()}, 201)
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
                bet = self.app.bets.cancel(int(path.rsplit("/", 1)[1]))
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
