"""Write the field structure of every HenrikDev endpoint the tracker uses (or could use) to henrik_shapes.md.

Only field names, types, list sizes and a few harmless sample values (site letters, team colours, mode names)
are written: no API key, player names, tags or IDs. List items are merged, so a field that only some items
have (a round with a defuse, say) still shows up, marked "sometimes".

Used to build docs/DATA.md; re-run it if HenrikDev changes their responses. Usage (PowerShell), from the
project folder, with $py pointing at a Python 3.10+ interpreter:
    $env:HENRIK_API_KEY = "your-key"
    & $py scripts/inspect_henrik.py <region> "<Name#TAG>"
    Remove-Item Env:HENRIK_API_KEY
The output file (henrik_shapes.md) is gitignored.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://api.henrikdev.xyz"
SHOW_VALUES = {"site", "winning_team", "team", "team_id", "result", "ceremony", "type", "mode", "mode_type", "platform",
               "region", "cluster", "won", "short", "has_won", "rounds_won", "rounds_lost", "patched_tier"}
PRIVATE = {"puuid", "name", "tag", "id", "match_id", "party_id", "card", "title", "game_name", "game_tag"}
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", re.I)  # e.g. Deathmatch puts the player's own ID in stats.team


def get(path, key, params=None):
    url = BASE + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": key, "User-Agent": "val-5-stack-inspect"})
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.load(res), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError) as e:
        return None, str(e)


def merge(node, value, key):
    """Fold one value into a schema node: {"types": set, "seen": n, "keys": {...}, "items": node, "sample": v, "len": [..]}"""
    node.setdefault("types", set()).add(type(value).__name__)
    node["seen"] = node.get("seen", 0) + 1
    if isinstance(value, dict):
        keys = node.setdefault("keys", {})
        node["dicts"] = node.get("dicts", 0) + 1
        for k, v in value.items():
            merge(keys.setdefault(k, {}), v, k)
    elif isinstance(value, list):
        node.setdefault("lens", []).append(len(value))
        items = node.setdefault("items", {})
        for v in value:
            merge(items, v, key)
    elif (key in SHOW_VALUES and key not in PRIVATE and value is not None and not UUID.match(str(value))
          and len(node.setdefault("samples", [])) < 6):
        if value not in node["samples"]:
            node["samples"].append(value)


def render(node, name, depth, out, parent_dicts=None):
    pad = "  " * depth
    types = sorted(node.get("types", {"?"}))
    optional = parent_dicts and node.get("seen", 0) < parent_dicts
    t = " | ".join(types)
    extra = []
    if node.get("lens"):
        lo, hi = min(node["lens"]), max(node["lens"])
        extra.append(f"length {lo}" if lo == hi else f"length {lo}-{hi}")
    if node.get("samples"):
        extra.append("e.g. " + ", ".join(repr(s) for s in node["samples"]))
    if optional:
        extra.append("sometimes")
    out.append(f"{pad}{name}: {t}" + (f"  ({'; '.join(extra)})" if extra else ""))
    for k, child in (node.get("keys") or {}).items():
        render(child, k, depth + 1, out, node.get("dicts"))
    if node.get("items"):
        render(node["items"], "[]", depth + 1, out)


def section(title, path_label, payload, err, out):
    out.append(f"## {title}\n\n`{path_label}`\n")
    if err:
        out.append(f"Not available: {err}\n")
        return
    root = {}
    merge(root, payload, "response")
    lines = []
    render(root, "response", 0, lines)
    out.append("```\n" + "\n".join(lines) + "\n```\n")


def main():
    key = os.environ.get("HENRIK_API_KEY", "").strip()
    if not key or len(sys.argv) != 3 or "#" not in sys.argv[2]:
        sys.exit(__doc__)
    region = sys.argv[1].lower()
    name, tag = (s.strip() for s in sys.argv[2].rsplit("#", 1))
    q = urllib.parse.quote
    out = [f"# HenrikDev API response structures\n\nCaptured {time.strftime('%Y-%m-%d')} for region `{region}`, platform `pc`. "
           "Field names and types only; list items are merged across every item returned.\n"]

    acct, err = get(f"/valorant/v2/account/{q(name)}/{q(tag)}", key)
    section("Account (v2)", "GET /valorant/v2/account/{name}/{tag}", acct, err, out)
    puuid = ((acct or {}).get("data") or {}).get("puuid")
    if not puuid:
        out.append("Stopped: the account lookup failed, so nothing else could be fetched.\n")
    else:
        calls = [
            ("Stored matches (v1)", "GET /valorant/v1/by-puuid/stored-matches/{region}/{puuid}?size=20",
             f"/valorant/v1/by-puuid/stored-matches/{region}/{puuid}", {"size": 20}),
            ("Recent full matches (v4)", "GET /valorant/v4/by-puuid/matches/{region}/{platform}/{puuid}?size=3",
             f"/valorant/v4/by-puuid/matches/{region}/pc/{puuid}", {"size": 3}),
            ("Current rank (v3 MMR)", "GET /valorant/v3/by-puuid/mmr/{region}/{platform}/{puuid}",
             f"/valorant/v3/by-puuid/mmr/{region}/pc/{puuid}", None),
            ("Rank / RR history (v2 MMR history)", "GET /valorant/v2/by-puuid/mmr-history/{region}/{platform}/{puuid}",
             f"/valorant/v2/by-puuid/mmr-history/{region}/pc/{puuid}", None),
            ("Stored rank / RR history (v2)", "GET /valorant/v2/by-puuid/stored-mmr-history/{region}/{platform}/{puuid}?size=20",
             f"/valorant/v2/by-puuid/stored-mmr-history/{region}/pc/{puuid}", {"size": 20}),
        ]
        match_id = None
        for title, label, path, params in calls:
            payload, err = get(path, key, params)
            section(title, label, payload, err, out)
            if title.startswith("Recent full matches") and payload:
                match_id = ((((payload.get("data") or [{}])[0]).get("metadata") or {}).get("match_id"))
            time.sleep(2.1)  # stay well inside the rate limit
        if match_id:
            payload, err = get(f"/valorant/v4/match/{region}/{q(match_id)}", key)
            section("One full match by ID (v4) — what the tracker fetches", "GET /valorant/v4/match/{region}/{match_id}",
                    payload, err, out)
    with open("henrik_shapes.md", "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print("Wrote henrik_shapes.md (" + str(sum(len(s) for s in out)) + " characters). No keys, names or IDs are in it.")


if __name__ == "__main__":
    main()
