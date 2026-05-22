"""
common.py
---------
Shared code used by both update-toc.py and push-release.py.

Contains:
  - Config loading and validation (reads config.yaml)
  - Terminal color/logging helpers
  - TOC file parsing and editing
  - Git helper functions

You should never need to run this file directly.
"""

import os
import re
import subprocess

# ============================================================
# PyYAML import with a helpful error if it's not installed
# ============================================================

try:
    import yaml
except ImportError:
    print("\n  ERROR: PyYAML is not installed.")
    print("  Fix: run this command in your terminal:")
    print("      pip install pyyaml")
    print()
    raise SystemExit(1)


# ============================================================
# Paths
# ============================================================

# SCRIPT_DIR = the tools/ folder where this file lives
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# CONFIG_PATH = tools/config.yaml
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.yaml")

# BASE_DIR = the parent folder that contains all your addon repos
# e.g. if tools/ is at C:\WoW\tools\, BASE_DIR is C:\WoW\
BASE_DIR = os.path.dirname(SCRIPT_DIR)


# ============================================================
# Config loading and validation
# ============================================================

# These keys must exist in config.yaml or the script will refuse to run.
# This prevents cryptic KeyError crashes halfway through processing addons.
REQUIRED_CONFIG_KEYS = [
    "dry_run",
    "game_version",
    "interface_versions",
    "git_branch",
    "git_remote",
    "release_prefix",
    "commit_message_template",
    "changelog_entry_template",
    "addons",
]

def load_config():
    """
    Read config.yaml and return its contents as a Python dictionary.
    Also validates that all required keys are present.

    If the file is missing or malformed, prints a helpful error and exits.
    """
    if not os.path.exists(CONFIG_PATH):
        print(f"\n  ERROR: config.yaml not found at: {CONFIG_PATH}")
        print("  Make sure config.yaml is in the same folder as this script.")
        raise SystemExit(1)

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            cfg = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"\n  ERROR: config.yaml has a syntax error:")
            print(f"  {e}")
            print("  Check your indentation and make sure all strings are quoted if they contain special characters.")
            raise SystemExit(1)

    # Validate all required keys are present
    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in cfg]
    if missing:
        print(f"\n  ERROR: config.yaml is missing required keys: {', '.join(missing)}")
        print("  Compare your config.yaml against the template to find what's missing.")
        raise SystemExit(1)

    # Validate addons list is not empty
    if not cfg["addons"]:
        print("\n  ERROR: config.yaml 'addons' list is empty.")
        print("  Add at least one addon folder name under the 'addons' key.")
        raise SystemExit(1)

    # Validate interface_versions is a list with at least one entry
    if not isinstance(cfg["interface_versions"], list) or len(cfg["interface_versions"]) == 0:
        print("\n  ERROR: config.yaml 'interface_versions' must be a list with at least one number.")
        print("  Example:  interface_versions:\n              - 120005")
        raise SystemExit(1)

    return cfg


# ============================================================
# Terminal color codes
# ============================================================

# These are ANSI escape codes that colorize terminal output.
# They only affect how text looks -- they're invisible to the scripts' logic.
RESET  = "\033[0m"
RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"


# ============================================================
# Logging helpers
# ============================================================
# Each function prints a consistently formatted line at a specific
# visual "level": header > section > info/success/warn/error.

def header(text):
    """Big cyan banner. Used once per addon to separate output."""
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")

def section(text):
    """Bold sub-heading within an addon's output block."""
    print(f"\n  {BOLD}{text}{RESET}")

def diff_line(label, old, new):
    """Shows a before/after change for a TOC field."""
    print(f"    {DIM}{label:<24}{RESET}  old: {RED}{old}{RESET}")
    print(f"    {' ' * 24}  new: {GREEN}{new}{RESET}")

def added_line(text):
    """Green '+' prefix, used for new changelog lines."""
    print(f"    {GREEN}+ {text}{RESET}")

def info(text):
    """Plain indented info line."""
    print(f"    {text}")

def success(text):
    """Green success confirmation."""
    print(f"    {GREEN}{text}{RESET}")

def warn(text):
    """Yellow warning. Script continues but something is worth noting."""
    print(f"  {YELLOW}WARNING: {text}{RESET}")

def error(text):
    """Red error. Usually means this addon will be skipped."""
    print(f"  {RED}ERROR: {text}{RESET}")

def dry_run_notice(text):
    """Yellow [DRY RUN] prefix. Shown instead of actually doing something."""
    print(f"  {YELLOW}[DRY RUN] {text}{RESET}")


# ============================================================
# TOC file helpers
# ============================================================

def find_toc(addon_path):
    """
    Search the addon folder for a .toc file and return its full path.
    Returns None if no .toc file is found.

    Note: if an addon has multiple .toc files (e.g. for different client
    flavors like _Mainline.toc and _Wrath.toc), this returns the first one
    found. Directory listing order is not guaranteed, so you may want to
    update this logic if you have flavor-split addons.
    """
    for f in os.listdir(addon_path):
        if f.endswith(".toc"):
            return os.path.join(addon_path, f)
    return None

def parse_toc(toc_path):
    """
    Read a .toc file and return its contents as a list of tuples.

    Each tuple is one of:
      ("meta", key, value, original_line)  -- for ## Key: Value lines
      ("raw",  None, None, original_line)  -- for everything else (comments, file list, blank lines)

    We keep the raw line for every entry so we can reconstruct the file
    exactly, only changing the lines we actually want to update.
    """
    entries = []
    with open(toc_path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.rstrip("\n")
            # Match lines like:  ## Version: 12.0.5.8
            m = re.match(r"^##\s*([\w\-]+):\s*(.*)$", raw)
            if m:
                entries.append(("meta", m.group(1), m.group(2), raw))
            else:
                entries.append(("raw", None, None, raw))
    return entries

def get_meta(entries, key):
    """
    Find the value of a metadata key in a parsed TOC entry list.
    Returns None if the key doesn't exist.

    Example: get_meta(entries, "Version")  ->  "12.0.5.8"
    """
    for kind, k, v, _ in entries:
        if kind == "meta" and k == key:
            return v
    return None

def set_meta(entries, key, new_value):
    """
    Update the value of a metadata key in a parsed TOC entry list (in place).
    Also rebuilds the raw line so the file writes back correctly.
    Returns True if the key was found and updated, False if it wasn't found.
    """
    for i, (kind, k, v, raw) in enumerate(entries):
        if kind == "meta" and k == key:
            entries[i] = ("meta", k, new_value, f"## {k}: {new_value}")
            return True
    return False

def detect_old_format(entries):
    """
    Some older addons use X-Nominal-Version as a separate field.
    Returns True if this legacy field is present, so we know to update it too.
    """
    return get_meta(entries, "X-Nominal-Version") is not None

def interface_list_str(versions):
    """
    Convert a list of interface numbers to a comma-separated string.
    Example: [120000, 120001, 120005]  ->  "120000, 120001, 120005"
    """
    return ", ".join(str(v) for v in versions)


# ============================================================
# Version helpers
# ============================================================

def parse_version(version_str):
    """
    Split a version string into its four integer parts.
    WoW addon versions follow the format:  MAJOR.MINOR.PATCH.INCREMENT
    where MAJOR.MINOR.PATCH matches the game version and INCREMENT is
    a number we control and bump by 1 each time we update for a patch.

    Example: "12.0.5.8"  ->  (12, 0, 5, 8)

    Raises ValueError if the string doesn't have exactly 4 parts.
    """
    parts = version_str.strip().split(".")
    if len(parts) != 4:
        raise ValueError(
            f"Version '{version_str}' doesn't match expected format MAJOR.MINOR.PATCH.INCREMENT "
            f"(e.g. 12.0.5.8). Check the ## Version: field in the .toc file."
        )
    return tuple(int(p) for p in parts)

def build_version_string(game_version_str, addon_increment):
    """
    Combine the game version and our increment into a full version string.
    Example: game_version="12.0.5", addon_increment=9  ->  "12.0.5.9"
    """
    return f"{game_version_str}.{addon_increment}"


# ============================================================
# Git helpers
# ============================================================

def is_git_repo(path):
    """
    Check whether a folder is a git repository.
    Returns True if a .git folder exists inside it.

    This prevents confusing git error output when a folder exists
    but was never initialized as a repo.
    """
    return os.path.isdir(os.path.join(path, ".git"))

def run_git(args, cwd, dry_run, label):
    """
    Run a git command in the given directory.

    args  -- list of git arguments, e.g. ["pull"] or ["tag", "release-12.0.5.9"]
    cwd   -- the directory to run the command in (the addon repo folder)
    dry_run -- if True, print what would happen but don't actually run it
    label -- a short description used in success/error messages

    Returns True if the command succeeded (or if dry_run), False on failure.
    """
    cmd = ["git"] + args
    info(f"git {' '.join(args)}")

    if dry_run:
        dry_run_notice(f"would run: {' '.join(cmd)}")
        return True

    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)

    if result.returncode != 0:
        error(f"{label} failed with exit code {result.returncode}:")
        # Print the actual git error output so you know exactly what went wrong
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines():
                print(f"    {RED}{line}{RESET}")
        return False

    # Print any non-empty git output (e.g. "Already up to date.")
    if result.stdout.strip():
        info(result.stdout.strip())

    success(f"{label} OK")
    return True

def pull_addon(cwd, dry_run):
    """
    Run 'git pull' in the addon repo to make sure we're working on the latest commit.
    Always done before reading the TOC or creating tags, so we don't
    overwrite someone else's changes or tag a stale commit.

    Returns True on success, False on failure.
    Common failure causes:
      - Uncommitted local changes that conflict with remote
      - Network/auth issues (SSH key not loaded, HTTPS credentials expired)
      - Merge conflicts from a previous interrupted run
    """
    section("Pulling latest:")
    return run_git(["pull"], cwd=cwd, dry_run=dry_run, label="git pull")

def tag_exists(tag, cwd):
    """
    Check whether a git tag already exists in the local repo.
    Returns True if it does. Used to skip re-tagging an already-released version.
    """
    result = subprocess.run(
        ["git", "tag", "-l", tag],
        cwd=cwd, capture_output=True, text=True
    )
    return tag in result.stdout.splitlines()
