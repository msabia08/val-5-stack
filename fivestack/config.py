"""Filesystem layout and config.json loading / validation."""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the repository root
WEB_DIR = os.path.join(ROOT, "web")
DATA_DIR = os.path.join(ROOT, "data")
TOOLS_DIR = os.path.join(ROOT, "tools")
CONFIG_PATH = os.path.join(ROOT, "config.json")
EXAMPLE_PATH = os.path.join(ROOT, "config.example.json")
BETTOR_NAMES_PATH = os.path.join(ROOT, "bettor_names.json")

PLACEHOLDER_IDS = {"friend1#tag1", "friend2#tag2", "friend3#tag3", "friend4#tag4", "yourname#tag"}


def load_bettor_names(path=BETTOR_NAMES_PATH):
    """Squad nickname -> bettor account name, for members who bet under another name. Keys are lower-cased."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            names = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"Ignoring {os.path.basename(path)}: {e}")
        return {}
    return {str(k).strip().lower(): str(v).strip() for k, v in names.items() if str(k).strip() and str(v).strip()}


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
    # `members` is optional: it only seeds an empty squad; the Squad tab manages it from then on.
    if (cfg.get("region") or "").lower() not in {"na", "eu", "ap", "kr", "latam", "br"}:
        problems.append("region must be one of na, eu, ap, kr, latam, br.")
    return problems


def mask(key):
    key = key or ""
    if len(key) < 10:
        return "set" if key else "not set"
    return key[:5] + "..." + key[-3:]
