"""Site password, admin password and bettor sessions, all as HMAC-signed cookies."""
import hashlib
import hmac
import secrets
import threading
import time
from http import cookies
from urllib.parse import quote, unquote

COOKIE = "fs_auth"
CLEAR_COOKIE = f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
BETTOR_COOKIE = "fs_bettor"
CLEAR_BETTOR_COOKIE = f"{BETTOR_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
THROTTLE_MSG = "Too many attempts. Try again in a few minutes."


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

    # ---- brute-force throttle (8 failures -> 10 minute lockout per key) -----
    def throttled(self, key):
        with self.lock:
            _, until = self.failures.get(key, (0, 0.0))
            return until > time.time()

    def record(self, key, ok):
        with self.lock:
            if ok:
                self.failures.pop(key, None)
                return
            count, _ = self.failures.get(key, (0, 0.0))
            count += 1
            self.failures[key] = (count, time.time() + 600 if count >= 8 else 0.0)

    def check_password(self, ip, candidate):
        key = f"site|{ip}"
        if self.throttled(key):
            return False, THROTTLE_MSG
        ok = hmac.compare_digest((candidate or "").strip(), self.password)
        self.record(key, ok)
        if not ok:
            time.sleep(0.8)
        return ok, (None if ok else "Wrong password.")

    def is_admin(self, candidate):
        if not self.admin_password:
            return True
        return hmac.compare_digest((candidate or "").strip(), self.admin_password)

    def is_configured_admin(self, candidate):
        """Like is_admin, but False when no admin password is configured at all."""
        return bool(self.admin_password) and self.is_admin(candidate)

    # ---- bettor sessions (signed cookie, invalidated when the password changes) ----
    def bettor_token(self, name, password_hash):
        return hmac.new(self.secret, f"bettor|{name.lower()}|{password_hash}".encode(), hashlib.sha256).hexdigest()

    def bettor_cookie(self, bettor, secure):
        value = f"{quote(bettor['name'], safe='')}.{self.bettor_token(bettor['name'], bettor['password_hash'])}"
        return f"{BETTOR_COOKIE}={value}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")

    def current_bettor(self, header, db):
        jar = cookies.SimpleCookie()
        try:
            jar.load(header or "")
        except cookies.CookieError:
            return None
        morsel = jar.get(BETTOR_COOKIE)
        if not morsel or "." not in morsel.value:
            return None
        qname, token = morsel.value.rsplit(".", 1)
        b = db.get_bettor(unquote(qname))
        if not b or not b.get("password_hash"):
            return None
        return b if hmac.compare_digest(token, self.bettor_token(b["name"], b["password_hash"])) else None
