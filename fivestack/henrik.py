"""Minimal HenrikDev Valorant API client (stdlib only) with rate-limit awareness.

Docs: https://docs.henrikdev.xyz  ·  Keys: https://api.henrikdev.xyz/dashboard/
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = "https://api.henrikdev.xyz"
USER_AGENT = "val-5stack-tracker/1.0 (+local hobby project)"

DEFAULT_MESSAGES = {
    400: "Bad request",
    401: "Invalid or missing API key",
    403: "Forbidden: this key is not allowed to use that endpoint",
    404: "Not found (check the Riot ID and region)",
    408: "Riot API timed out",
    429: "Rate limited by HenrikDev",
    500: "HenrikDev server error",
    501: "Riot API returned an error",
    503: "HenrikDev API unavailable (Riot maintenance?)",
}


class HenrikError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.message = message


def _q(s):
    return urllib.parse.quote(str(s), safe="")


def _error_message(data, code):
    errs = data.get("errors") if isinstance(data, dict) else None
    if isinstance(errs, list) and errs:
        parts = []
        for er in errs:
            if isinstance(er, dict):
                parts.append(str(er.get("message") or er.get("details") or er))
            else:
                parts.append(str(er))
        return "; ".join(parts)
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return DEFAULT_MESSAGES.get(code, f"Request failed ({code})")


class HenrikClient:
    def __init__(self, api_key, min_interval=1.5, timeout=45):
        self.api_key = api_key
        self.min_interval = float(min_interval)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._last = 0.0
        self.calls = 0
        self.ratelimit = {"limit": None, "remaining": None, "reset": None, "updated": None}

    # ---- plumbing --------------------------------------------------------
    def _update_rl(self, headers):
        if not headers:
            return

        def geti(name):
            v = headers.get(name)
            try:
                return int(float(v)) if v is not None else None
            except ValueError:
                return None

        lim, rem, reset = geti("X-RateLimit-Limit"), geti("X-RateLimit-Remaining"), geti("X-RateLimit-Reset")
        if lim is not None or rem is not None:
            self.ratelimit = {"limit": lim, "remaining": rem, "reset": reset, "updated": time.time()}

    def request(self, path, params=None):
        query = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"Authorization": self.api_key, "Accept": "application/json", "User-Agent": USER_AGENT}
        with self._lock:
            last_err = None
            for attempt in range(3):
                wait = self.min_interval - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                req = urllib.request.Request(url, headers=headers)
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        body = resp.read()
                        self._update_rl(resp.headers)
                        self._last = time.time()
                        self.calls += 1
                        return json.loads(body.decode("utf-8"))
                except urllib.error.HTTPError as e:
                    self._last = time.time()
                    self.calls += 1
                    self._update_rl(e.headers)
                    try:
                        data = json.loads(e.read().decode("utf-8"))
                    except Exception:
                        data = {}
                    msg = _error_message(data, e.code)
                    last_err = HenrikError(e.code, msg)
                    if e.code == 429 and attempt < 2:
                        raw = e.headers.get("Retry-After") or e.headers.get("X-RateLimit-Reset") or "15"
                        try:
                            delay = min(max(int(float(raw)), 2), 75)
                        except ValueError:
                            delay = 15
                        time.sleep(delay + 0.5)
                        continue
                    if e.code in (408, 500, 502, 503, 504) and attempt < 2:
                        time.sleep(3)
                        continue
                    raise last_err
                except urllib.error.URLError as e:
                    self._last = time.time()
                    last_err = HenrikError(0, f"Network error: {getattr(e, 'reason', e)}")
                    if attempt < 2:
                        time.sleep(3)
                        continue
                    raise last_err
                except (OSError, ValueError) as e:  # socket timeouts, bad JSON
                    self._last = time.time()
                    last_err = HenrikError(0, f"Request error: {e}")
                    if attempt < 2:
                        time.sleep(3)
                        continue
                    raise last_err
            raise last_err or HenrikError(0, "Request failed after retries")

    # ---- endpoints -------------------------------------------------------
    def account(self, name, tag):
        """v2 account lookup by Riot ID -> dict with puuid, region, name, tag, card, ..."""
        return self.request(f"/valorant/v2/account/{_q(name)}/{_q(tag)}").get("data") or {}

    def account_by_puuid(self, puuid):
        return self.request(f"/valorant/v2/by-puuid/account/{_q(puuid)}").get("data") or {}

    def stored_matches(self, region, puuid, mode=None, size=None, page=None):
        """v1 stored matches: the player's own line for each stored match (long history, cheap)."""
        return self.request(
            f"/valorant/v1/by-puuid/stored-matches/{_q(region)}/{_q(puuid)}",
            {"mode": mode, "size": size, "page": page},
        )

    def match_details(self, region, match_id):
        """v4 full match record (all 10 players, teams, rounds)."""
        return self.request(f"/valorant/v4/match/{_q(region)}/{_q(match_id)}").get("data") or {}

    def matchlist(self, region, platform, puuid, size=10, start=None, mode=None):
        """v4 recent matches with full details for each."""
        return self.request(
            f"/valorant/v4/by-puuid/matches/{_q(region)}/{_q(platform)}/{_q(puuid)}",
            {"size": size, "start": start, "mode": mode},
        ).get("data") or []
