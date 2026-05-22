"""
push-release.py
---------------
Step 2 of 2 in the WoW addon release workflow.

WHAT IT DOES:
  For each addon in config.yaml:
    1. Read the current ## Version: from the .toc file
    2. Build a release tag from config's release_prefix + version
       e.g.  release-12.0.5.9
    3. Check if that tag already exists (skips if so)
    4. git tag <tag>
    5. git push origin <tag>

  Pushing the tag triggers the release pipeline on CurseForge, Wago,
  or wherever your .pkgmeta / GitHub Actions are configured to deploy from.

RUN ORDER:
  Always run update-toc.py FIRST.
  This script reads the version from the .toc that update-toc.py just wrote
  and committed. If you run this before update-toc.py, the tag will point
  at the old version.

SAFE TO RE-RUN:
  If a tag already exists for the current version, it's skipped.
  Re-running after a partial failure will only process the addons that weren't
  tagged yet.

DRY RUN:
  Controlled by dry_run in config.yaml. When true, shows what would happen
  without creating or pushing any tags.

COMMON ERRORS:
  "git pull failed"
    -> Uncommitted local changes or a merge conflict in the repo.
    -> cd into the addon folder, run 'git status', resolve manually, re-run.

  "git tag failed"
    -> The tag probably already exists locally but wasn't caught by tag_exists().
    -> Run: git tag -l "release-*" inside the addon folder to inspect.
    -> To delete a bad local tag: git tag -d <tagname>

  "git push failed"
    -> Auth issue (SSH key not loaded, HTTPS token expired), or the remote
       doesn't have the commit this tag points to yet.
    -> Make sure update-toc.py's push succeeded for this addon first.
    -> Check: git log --oneline -3  to confirm the commit is there.
"""

import os
import sys

# ---------------------------------------------------------------------------
# Import shared helpers from common.py (in the same tools/ folder).
# If this fails, make sure common.py is in the same directory as this file.
# ---------------------------------------------------------------------------
try:
    from common import (
        load_config, BASE_DIR,
        header, section, info, success, warn, error, dry_run_notice,
        RESET, RED, GREEN, CYAN, YELLOW, BOLD,
        find_toc,
        is_git_repo, run_git, pull_addon, tag_exists,
        interface_list_str,
    )
except ImportError as e:
    print(f"\n  ERROR: Could not import common.py: {e}")
    print("  Make sure common.py is in the same folder as this script.")
    raise SystemExit(1)


# ============================================================
# get_toc_meta: lightweight single-key reader
# ============================================================

def get_toc_meta(toc_path, key):
    """
    Read a single metadata value directly from a .toc file without
    parsing the whole thing. Used here because we only need ## Version:.

    Returns the value as a string, or None if the key isn't found.
    """
    import re
    with open(toc_path, "r", encoding="utf-8") as f:
        for line in f:
            m = re.match(rf"^##\s*{re.escape(key)}:\s*(.*)$", line)
            if m:
                return m.group(1).strip()
    return None


# ============================================================
# Core: process one addon
# ============================================================

def process_addon(addon_name, addon_path, cfg, dry_run):
    """
    Tag and push the release for a single addon.

    Returns:
      True         -- tag created and pushed successfully
      "skipped"    -- tag already exists, nothing to do
      "pull_failed"-- git pull failed, skipped to avoid tagging wrong commit
      False        -- something else went wrong (see error output)
    """
    header(f"Addon: {addon_name}")

    # Make sure this folder is actually a git repo
    if not is_git_repo(addon_path):
        error(
            f"'{addon_name}' is not a git repository (no .git folder found).\n"
            "    Run update-toc.py first, which requires repos to already be initialized."
        )
        return False

    # Find the .toc file
    toc_path = find_toc(addon_path)
    if not toc_path:
        error(
            f"No .toc file found in '{addon_path}'.\n"
            "    Check that the folder name in config.yaml is correct."
        )
        return False

    info(f"TOC: {os.path.basename(toc_path)}")

    # Pull to make sure we're tagging the latest commit (the one update-toc.py pushed)
    # If pull fails, skip rather than risk tagging the wrong commit.
    if not pull_addon(addon_path, dry_run):
        error(
            "Skipping to avoid tagging a stale or diverged commit.\n"
            f"    Fix: cd into '{addon_name}', run 'git status', resolve any issues,\n"
            "    then re-run this script."
        )
        return "pull_failed"

    # Read the current version from the .toc file
    version = get_toc_meta(toc_path, "Version")
    if not version:
        error(
            "No '## Version:' field found in the .toc file.\n"
            "    Make sure update-toc.py ran successfully for this addon first."
        )
        return False

    # Build the full tag name, e.g. "release-12.0.5.9"
    tag    = f"{cfg['release_prefix']}-{version}"
    remote = cfg["git_remote"]

    info(f"Version  : {version}")
    info(f"Tag      : {tag}")
    info(f"Remote   : {remote}")

    # Skip if this tag already exists -- the release was already pushed
    if not dry_run and tag_exists(tag, addon_path):
        info(f"\n  {CYAN}Tag '{tag}' already exists. Already released, nothing to do.{RESET}")
        return "skipped"

    section("Git Operations:")

    # Create the local tag
    ok = run_git(["tag", tag], cwd=addon_path, dry_run=dry_run, label="git tag")
    if not ok:
        error(
            f"Failed to create tag '{tag}'.\n"
            f"    If it already exists locally: git tag -d {tag}\n"
            "    Then re-run this script."
        )
        return False

    # Push the tag to the remote
    ok = run_git(["push", remote, tag], cwd=addon_path, dry_run=dry_run, label="git push tag")
    if not ok:
        # Clean up the local tag so we're not left in an inconsistent state
        # (local tag exists but remote doesn't have it)
        if not dry_run:
            warn(
                f"Push failed. Removing local tag '{tag}' to keep local and remote in sync.\n"
                "    Once you've fixed the push issue (auth, network, etc.), re-run this script."
            )
            run_git(["tag", "-d", tag], cwd=addon_path, dry_run=False, label="git tag -d (cleanup)")
        return False

    return True


# ============================================================
# Entry point
# ============================================================

def main():
    cfg     = load_config()
    dry_run = cfg.get("dry_run", True)

    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  WoW Addon Release Pusher{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")

    if dry_run:
        print(f"\n  {YELLOW}{BOLD}*** DRY RUN MODE -- no tags will be created or pushed ***{RESET}")
        print(f"  {YELLOW}To apply changes, set dry_run: false in config.yaml{RESET}")
    else:
        print(f"\n  {GREEN}{BOLD}*** LIVE MODE -- tags will be created and pushed ***{RESET}")

    print(f"\n  Release prefix : {cfg['release_prefix']}")
    print(f"  Remote         : {cfg['git_remote']}")
    print(f"  Addons         : {', '.join(cfg['addons'])}")

    results = {}

    for addon_name in cfg["addons"]:
        addon_path = os.path.join(BASE_DIR, addon_name)
        if not os.path.isdir(addon_path):
            header(f"Addon: {addon_name}")
            error(
                f"Folder not found: {addon_path}\n"
                "    Check that the name in config.yaml matches the actual folder name exactly."
            )
            results[addon_name] = False
            continue
        results[addon_name] = process_addon(addon_name, addon_path, cfg, dry_run)

    # ---- Summary ----
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  Summary{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}\n")

    ok_count          = sum(1 for v in results.values() if v is True)
    skipped_count     = sum(1 for v in results.values() if v == "skipped")
    pull_failed_count = sum(1 for v in results.values() if v == "pull_failed")
    fail_count        = sum(1 for v in results.values() if v is False)

    for name, result in results.items():
        if result is True:
            status = f"{GREEN}OK{RESET}"
        elif result == "skipped":
            status = f"{CYAN}SKIPPED (tag already exists){RESET}"
        elif result == "pull_failed":
            status = f"{YELLOW}SKIPPED (git pull failed -- resolve manually then re-run){RESET}"
        else:
            status = f"{RED}FAILED (see errors above){RESET}"
        print(f"  {name:<30} {status}")

    print(f"\n  {ok_count} pushed, {skipped_count} skipped, {pull_failed_count} pull failed, {fail_count} failed")

    if dry_run:
        print(f"\n  {YELLOW}{BOLD}Dry run complete.")
        print(f"  Set dry_run: false in config.yaml and re-run to apply changes.{RESET}")

    if fail_count > 0 or pull_failed_count > 0:
        print(f"\n  {YELLOW}Some addons had issues. Check the errors above.")
        print(f"  Addons that succeeded are safe -- re-running will skip them.")
        print(f"  Fix the issue in the affected repo(s) and re-run this script.{RESET}")

    print()


if __name__ == "__main__":
    main()
