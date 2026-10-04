"""Posts to the squad's Discord channel through a webhook (`discord_webhook` in config.json).

A webhook is a URL made in a channel's settings (Integrations > Webhooks); the site only ever sends to it. It posts:

- one message per new squad game (`game_message()`): the result, the top of the scoreboard, the recap's best
  highlight, who won and lost what betting on it, and any long shot that came in;
- the daily wheel's jackpot when someone lands it (`jackpot_message()`).

`Discord.send()` hands the payload to a daemon thread, so a slow or broken Discord never holds up a sync or a request;
failures are printed and dropped (the status only, never the URL, which is a secret: anyone who has it can post to
the channel). Nothing is posted in demo mode or without a valid webhook URL. Every message turns pings off
(`NO_PINGS`), since names and bet descriptions are typed by people. A first sync of a
long history would be dozens of games at once, so only recent ones are posted (`fresh()`), a few per sync.

The message builders are pure functions of the game's data, which is what the self-test checks; the test never
touches the network (`Discord(url, post=...)` takes the function that does the sending).
"""
import datetime
import json
import threading
import time
import urllib.error
import urllib.request

WEBHOOK_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/",
                    "https://canary.discord.com/api/webhooks/", "https://ptb.discord.com/api/webhooks/")
FRESH_S = 6 * 3600  # only games that started this recently are posted (a first sync brings in the whole history)
MAX_PER_SYNC = 3  # and at most this many from one sync
LONG_SHOT_DECIMAL = 6.0  # a winning bet at these odds or longer gets its own line (the page's gold confetti)
MAX_BET_LINES = 10
WIN_COLOUR, LOSS_COLOUR, GOLD = 0x2ECC71, 0xE74C3C, 0xFFD54A
NAME = "Onkey"  # who the posts come from
# Bettor names and bet descriptions are typed by people: nothing in a post may ping anyone (@everyone, @here, a
# role or a user), whatever a name says.
NO_PINGS = {"parse": []}


def valid_webhook(url):
    return isinstance(url, str) and url.strip().startswith(WEBHOOK_PREFIXES)


def credits(n):
    return f"{n:,.0f}"


def signed(n):
    return f"{'+' if n > 0 else '-' if n < 0 else ''}{credits(abs(n))}"


def _clip(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def fresh(matches, now=None):
    """The games worth posting from one sync: the ones that started in the last FRESH_S, newest MAX_PER_SYNC of them,
    oldest first."""
    now = now or time.time()
    recent = sorted((m for m in matches if now - (m.get("started_ts") or 0) <= FRESH_S), key=lambda m: m.get("started_ts") or 0)
    return recent[-MAX_PER_SYNC:]


def betting_lines(settled):
    """What each bettor made or lost on a game's settled bets (voids and cancelled bets aside): the lines for the
    post, winners first, and the long shots that came in."""
    nets, long_shots = {}, []
    for b in settled or []:
        if b.get("status") not in ("won", "lost"):
            continue
        net = (b.get("payout") or 0) - (b.get("stake") or 0)
        row = nets.setdefault(b["bettor"], {"net": 0.0, "bets": 0, "won": 0})
        row["net"] += net
        row["bets"] += 1
        row["won"] += b["status"] == "won"
        if b["status"] == "won" and (b.get("odds_decimal") or 0) >= LONG_SHOT_DECIMAL:
            long_shots.append(b)
    ranked = sorted(nets.items(), key=lambda kv: -kv[1]["net"])
    lines = [f"{'🟢' if r['net'] > 0 else '🔴' if r['net'] < 0 else '⚪'} **{name}** {signed(r['net'])} "
             f"({r['won']} of {r['bets']} bet{'' if r['bets'] == 1 else 's'} won)" for name, r in ranked[:MAX_BET_LINES]]
    if len(ranked) > MAX_BET_LINES:
        lines.append(f"…and {len(ranked) - MAX_BET_LINES} more")
    shots = [f"🎯 **{b['bettor']}** hit \"{_clip(b.get('description') or 'a bet', 80)}\" at {b['odds_decimal']:.2f} for "
             f"{signed((b.get('payout') or 0) - (b.get('stake') or 0))}"
             for b in sorted(long_shots, key=lambda b: -(b.get("odds_decimal") or 0))[:3]]
    return lines, shots


def game_message(match, board=None, highlight=None, settled=None):
    """The post for one squad game. `board`: the recap's scoreboard rows (nickname, kills, deaths, assists, acs);
    `highlight`: its top highlight ({"title", "detail"}); `settled`: the bets settled on it."""
    won = match.get("result") == "win"
    score = f"{match.get('rounds_won', '?')}-{match.get('rounds_lost', '?')}"
    fields = []
    rows = [r for r in (board or []) if r.get("acs") is not None]
    if rows:
        top = max(rows, key=lambda r: r["acs"])
        kda = "/".join(str(int(top[k])) if top.get(k) is not None else "?" for k in ("kills", "deaths", "assists"))
        fields.append({"name": "Top of the scoreboard", "value": _clip(f"**{top['nickname']}** · {kda} · {top['acs']:.0f} ACS", 1024)})
    lines, shots = betting_lines(settled)
    if lines:
        fields.append({"name": "Betting", "value": _clip("\n".join(lines), 1024)})
    if shots:
        fields.append({"name": "Long shot", "value": _clip("\n".join(shots), 1024)})
    embed = {"title": _clip(f"{'Win' if won else 'Loss'} {score} on {match.get('map') or 'an unknown map'}", 256),
             "color": WIN_COLOUR if won else LOSS_COLOUR, "fields": fields, "footer": {"text": "5-Stack Tracker"}}
    if highlight and highlight.get("title"):
        detail = f"\n{highlight['detail']}" if highlight.get("detail") else ""
        embed["description"] = _clip(f"**{highlight['title']}**{detail}", 2000)
    if match.get("started_ts"):
        embed["timestamp"] = datetime.datetime.fromtimestamp(match["started_ts"], datetime.timezone.utc).isoformat()
    return {"username": NAME, "allowed_mentions": NO_PINGS, "embeds": [embed]}


def jackpot_message(name, amount):
    return {"username": NAME, "allowed_mentions": NO_PINGS, "embeds": [{"title": "JACKPOT!", "color": GOLD,
                                          "description": _clip(f"🌟 **{name}** landed the daily wheel's jackpot: **{credits(amount)} credits**.", 2000),
                                          "footer": {"text": "5-Stack Tracker"}}]}


def test_message():
    return {"username": NAME, "allowed_mentions": NO_PINGS, "content": "Ook. Onkey is connected. Game results and jackpots will be posted here."}


def _post(url, payload, timeout=10):
    """Send one payload to the webhook. Returns (ok, detail). One retry when Discord says to slow down."""
    data = json.dumps(payload).encode("utf-8")
    for attempt in (1, 2):
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "5-Stack-Tracker (webhook, 1.0)"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return True, f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                try:
                    wait = float(json.loads(e.read().decode("utf-8") or "{}").get("retry_after", 1))
                except (ValueError, TypeError):
                    wait = 1.0
                time.sleep(min(5.0, max(0.2, wait)))
                continue
            return False, f"HTTP {e.code}"
        except (urllib.error.URLError, OSError) as e:
            return False, str(e)
    return False, "rate limited"


class Discord:
    def __init__(self, url=None, post=None, background=True):
        """`post(url, payload) -> (ok, detail)` does the sending (the real one by default); `background=False` sends
        inline, for the test command and the self-test."""
        url = (url or "").strip()
        self.url = url if valid_webhook(url) else ""
        self.ignored = bool(url) and not self.url  # something was configured, but it isn't a Discord webhook URL
        self._post = post or _post
        self.background = background

    @property
    def enabled(self):
        return bool(self.url)

    def send(self, payload):
        """Post a message. Returns False when there's no webhook; otherwise True (sent in the background) or, with
        `background=False`, whether it went through."""
        if not self.enabled or not payload:
            return False
        if not self.background:
            return self._deliver(payload)
        threading.Thread(target=self._deliver, args=(payload,), name="discord", daemon=True).start()
        return True

    def _deliver(self, payload):
        try:
            ok, detail = self._post(self.url, payload)
        except Exception as e:  # never let a post take anything else down
            ok, detail = False, str(e)
        if not ok:
            print(f"[discord] post failed: {detail}", flush=True)
        return ok
