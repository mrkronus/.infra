"""
update-toc.py
-------------
Step 1 of 2 in the WoW addon release workflow.

WHAT IT DOES:
  For each addon in config.yaml:
    1. git pull (to make sure we're on the latest commit)
    2. Read the addon's .toc file
    3. Check whether the version and interface numbers are already current
    4. If not: bump the addon increment (e.g. 12.0.5.8 -> 12.0.5.9)
    5. Update ## Version: and ## Interface: in the .toc file
    6. Prepend a new entry to CHANGELOG.md
    7. git add, git commit, git push

WHAT TO UPDATE EACH PATCH:
  Open config.yaml and change:
    - game_version: to the new patch number (e.g. "12.1.0")
    - interface_versions: to the interface numbers Blizzard published
      Find these at: https://wowpedia.fandom.com/wiki/TOC_format

SAFE TO RE-RUN:
  If an addon is already on the current game version and interface,
  it is skipped automatically. Running this twice won't double-bump anything.

DRY RUN:
  config.yaml has a dry_run setting. When true, all changes are previewed
  in the terminal but nothing is written or committed. Always test with
  dry_run: true first.

AFTER THIS SCRIPT:
  Run push-release.py (or push-release.bat) to create and push git tags,
  which triggers the release pipeline on CurseForge / Wago / etc.

COMMON ERRORS:
  "git pull failed"
    -> There's likely a merge conflict or uncommitted change in that repo.
    -> cd into the addon folder and run 'git status' to see what's going on.
    -> Resolve it manually, then re-run this script.

  "No .toc file found"
    -> The folder name in config.yaml doesn't match the actual folder name.
    -> Check capitalization -- it's case-sensitive on Linux/Mac.

  "Unexpected version format"
    -> The ## Version: field in the .toc isn't in MAJOR.MINOR.PATCH.INCREMENT format.
    -> Edit the .toc manually to fix the version, then re-run.
"""

import os
import sys
from datetime import date

# ---------------------------------------------------------------------------
# Import shared helpers from common.py (in the same tools/ folder).
# If this fails, make sure common.py is in the same directory as this file.
# ---------------------------------------------------------------------------
try:
    from common import (
        load_config, BASE_DIR,
        header, section, diff_line, added_line, info, warn, error, dry_run_notice,
        RESET, GREEN, CYAN, YELLOW, BOLD,
        find_toc, parse_toc, get_meta, set_meta, detect_old_format,
        interface_list_str, parse_version, build_version_string,
        is_git_repo, run_git, pull_addon,
    )
except ImportError:
    print("\n  ERROR: Could not import common.py.")
    print("  Make sure common.py is in the same folder as this script.")
    raise SystemExit(1)


# ============================================================
# Changelog helpers
# ============================================================

CHANGELOG_FILENAME = "CHANGELOG.md"

def build_changelog_entry(version_str, release_prefix, template, interface_versions):
    """
    Build a single changelog entry to prepend to CHANGELOG.md.

    Output format (matches existing changelog style exactly):
        ### Version 12.0.5.9 release
        [blank line]
        - Bumping TOC for latest patch (120005)
        [blank line]
        [blank line]

    The two trailing blank lines become the separator between this entry
    and the previous top entry when prepended to the existing file.
    """
    latest_interface = max(interface_versions)
    note = template.replace("{interface}", str(latest_interface))
    lines = [
        f"### Version {version_str} {release_prefix}",
        "",
        f"- {note}",
        "",
        "",
    ]
    return "\n".join(lines)

def prepend_changelog(changelog_path, entry):
    """
    Return the full new contents of CHANGELOG.md with 'entry' added at the top.
    If CHANGELOG.md doesn't exist yet, the entry becomes the entire file.
    """
    existing = ""
    if os.path.exists(changelog_path):
        with open(changelog_path, "r", encoding="utf-8") as f:
            existing = f.read()
    return entry + existing


# ============================================================
# Git: commit and push the TOC + changelog changes
# ============================================================

def commit_and_push(addon_path, toc_path, changelog_path, version_str, cfg, dry_run):
    """
    Stage the updated .toc and CHANGELOG.md, commit them, and push to origin.

    The commit message is built from config.yaml's commit_message_template.
    Supported placeholders:
      {version}      -> the new full version string, e.g. 12.0.5.9
      {game_version} -> the game version from config, e.g. 12.0.5

    Returns True on success, False if any git step fails.
    """
    section("Committing and pushing:")

    # Build the commit message from the template in config.yaml
    commit_msg = (
        cfg["commit_message_template"]
        .replace("{version}", version_str)
        .replace("{game_version}", cfg["game_version"])
    )

    # Stage the two files we changed
    ok = run_git(["add", toc_path, changelog_path], cwd=addon_path, dry_run=dry_run, label="git add")
    if not ok:
        error("git add failed. This is unusual -- check that the file paths are correct.")
        return False

    # Commit with the generated message
    ok = run_git(["commit", "-m", commit_msg], cwd=addon_path, dry_run=dry_run, label="git commit")
    if not ok:
        error(
            "git commit failed. Possible causes:\n"
            "    - No changes were staged (maybe the files weren't actually modified?)\n"
            "    - git user.name / user.email not configured in this repo.\n"
            "      Fix: git config user.name 'Your Name' && git config user.email 'you@example.com'"
        )
        return False

    # Push the commit to the configured remote and branch
    ok = run_git(
        ["push", cfg["git_remote"], cfg["git_branch"]],
        cwd=addon_path, dry_run=dry_run, label="git push"
    )
    if not ok:
        error(
            f"git push failed. Possible causes:\n"
            f"    - Auth issue: SSH key not loaded, or HTTPS credentials expired.\n"
            f"    - Remote branch '{cfg['git_branch']}' doesn't exist yet.\n"
            f"      Fix: git push --set-upstream {cfg['git_remote']} {cfg['git_branch']}\n"
            f"    - Remote has changes you don't have locally (run git pull first)."
        )
        return False

    return True


# ============================================================
# Core: process one addon
# ============================================================

def process_addon(addon_name, addon_path, cfg, dry_run):
    """
    Run the full TOC update workflow for a single addon.

    Returns:
      True         -- successfully updated, committed, and pushed
      "skipped"    -- already up to date, nothing to do
      "pull_failed"-- git pull failed, skipped to avoid modifying stale files
      False        -- something else went wrong (see error output)
    """
    header(f"Addon: {addon_name}")

    # Make sure this folder is actually a git repo before touching anything
    if not is_git_repo(addon_path):
        error(
            f"'{addon_name}' is not a git repository (no .git folder found).\n"
            "    If this is a new addon, initialize it first:\n"
            "      cd into the folder, then: git init && git remote add origin <url>"
        )
        return False

    # Find the .toc file in this addon's folder
    toc_path = find_toc(addon_path)
    if not toc_path:
        error(
            f"No .toc file found in '{addon_path}'.\n"
            "    Check that the folder name in config.yaml matches the actual folder name exactly.\n"
            "    Folder names are case-sensitive on Linux and Mac."
        )
        return False

    info(f"TOC: {os.path.basename(toc_path)}")

    # Pull before reading anything so we're working on the latest commit.
    # If this fails, we skip the addon rather than risk overwriting someone
    # else's changes or committing on top of a diverged history.
    if not pull_addon(addon_path, dry_run):
        error(
            "Skipping this addon to avoid modifying files on a stale commit.\n"
            f"    Fix: cd into '{addon_name}', run 'git status' and resolve any issues,\n"
            "    then re-run this script."
        )
        return "pull_failed"

    # Parse the .toc into a list of (kind, key, value, raw_line) tuples
    entries = parse_toc(toc_path)

    # Check whether this addon uses the legacy X-Nominal-Version field
    old_format = detect_old_format(entries)
    if old_format:
        info("Format: legacy (X-Nominal-Version present)")
    else:
        info("Format: current")

    # Read the current Version and Interface values from the .toc
    current_version_str   = get_meta(entries, "Version")
    current_interface_str = get_meta(entries, "Interface")

    if not current_version_str:
        error(
            "No '## Version:' field found in the .toc file.\n"
            "    Add a line like '## Version: 12.0.5.1' to the .toc and re-run."
        )
        return False

    # Parse the version into its four components so we can bump the last one
    try:
        gma, gmi, gpa, addon_inc = parse_version(current_version_str)
    except ValueError as e:
        error(str(e))
        return False

    # Build the new Interface string from config
    new_interface_str = interface_list_str(cfg["interface_versions"])
    latest_interface  = str(max(cfg["interface_versions"]))

    # Check whether this addon is already current.
    # "Current" means the game version part of the Version field matches config,
    # AND the highest interface number from config is already in the Interface field.
    version_game_part     = ".".join(current_version_str.strip().split(".")[:3])
    current_interfaces    = [i.strip() for i in (current_interface_str or "").split(",")]
    already_on_game_version = version_game_part == cfg["game_version"]
    already_has_interface   = latest_interface in current_interfaces

    if already_on_game_version and already_has_interface:
        info(f"  ## Version:   {current_version_str}")
        info(f"  ## Interface: {current_interface_str}")
        info(f"\n  {GREEN}Already up to date, nothing to do.{RESET}")
        return "skipped"

    # Compute new values
    new_addon_inc    = addon_inc + 1
    new_version_str  = build_version_string(cfg["game_version"], new_addon_inc)

    # ---- Show the diff ----
    section("TOC Changes:")

    version_changed   = current_version_str != new_version_str
    interface_changed = current_interface_str != new_interface_str

    if version_changed:
        diff_line("## Version:", current_version_str, new_version_str)
    else:
        info(f"  ## Version: {current_version_str}  (no change)")

    if interface_changed:
        diff_line("## Interface:", current_interface_str or "(none)", new_interface_str)
    else:
        info(f"  ## Interface: {current_interface_str}  (no change)")

    if old_format:
        current_nominal = get_meta(entries, "X-Nominal-Version")
        new_nominal = str(new_addon_inc)
        if current_nominal != new_nominal:
            diff_line("## X-Nominal-Version:", current_nominal or "(none)", new_nominal)

    # ---- Show the changelog entry ----
    changelog_path = os.path.join(addon_path, CHANGELOG_FILENAME)
    entry = build_changelog_entry(
        new_version_str,
        cfg["release_prefix"],
        cfg["changelog_entry_template"],
        cfg["interface_versions"]
    )

    if os.path.exists(changelog_path):
        section("CHANGELOG.md (prepending):")
        for line in entry.strip().splitlines():
            added_line(line)
    else:
        info("No CHANGELOG.md found -- skipping changelog update for this addon.")

    # ---- Write files or dry-run ----
    if not dry_run:
        # Update the in-memory parsed entries
        set_meta(entries, "Version", new_version_str)
        set_meta(entries, "Interface", new_interface_str)
        if old_format:
            set_meta(entries, "X-Nominal-Version", str(new_addon_inc))

        # Reconstruct the .toc content from the (possibly modified) entries
        # Each entry's raw line is joined with newlines to preserve file structure
        new_toc_content = "\n".join(raw for _, _, _, raw in entries)
        with open(toc_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_toc_content)

        # Only update CHANGELOG.md if it already exists.
        # Addons without one are internal/unpublished and don't need it.
        if os.path.exists(changelog_path):
            new_changelog = prepend_changelog(changelog_path, entry)
            with open(changelog_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_changelog)

        section("Written:")
        info(f"  {toc_path}")
        if os.path.exists(changelog_path):
            info(f"  {changelog_path}")

        # Commit and push the changes
        ok = commit_and_push(addon_path, toc_path, changelog_path, new_version_str, cfg, dry_run)
        if not ok:
            return False
    else:
        section("Files (dry run, not written):")
        dry_run_notice(f"would write: {toc_path}")
        if os.path.exists(changelog_path):
            dry_run_notice(f"would write: {changelog_path}")
        else:
            info("No CHANGELOG.md found -- would skip changelog update.")

        section("Git (dry run, not executed):")
        dry_run_notice(f"would run: git add <files>")
        dry_run_notice(f"would run: git commit -m \"{cfg['commit_message_template'].replace('{version}', new_version_str).replace('{game_version}', cfg['game_version'])}\"")
        dry_run_notice(f"would run: git push {cfg['git_remote']} {cfg['git_branch']}")

    return True


# ============================================================
# Entry point
# ============================================================

def main():
    cfg     = load_config()
    dry_run = cfg.get("dry_run", True)

    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  WoW Addon TOC Updater{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}")

    if dry_run:
        print(f"\n  {YELLOW}{BOLD}*** DRY RUN MODE -- no files will be written or committed ***{RESET}")
        print(f"  {YELLOW}To apply changes, set dry_run: false in config.yaml{RESET}")
    else:
        print(f"\n  {GREEN}{BOLD}*** LIVE MODE -- files will be modified and committed ***{RESET}")

    print(f"\n  Game version : {cfg['game_version']}")
    print(f"  Interfaces   : {interface_list_str(cfg['interface_versions'])}")
    print(f"  Branch       : {cfg['git_branch']}")
    print(f"  Addons       : {', '.join(cfg['addons'])}")

    results = {}

    for addon_name in cfg["addons"]:
        addon_path = os.path.join(BASE_DIR, addon_name)
        if not os.path.isdir(addon_path):
            header(f"Addon: {addon_name}")
            error(
                f"Folder not found: {addon_path}\n"
                "    Check that the name in config.yaml matches the actual folder name exactly.\n"
                "    Folder names are case-sensitive on Linux and Mac."
            )
            results[addon_name] = False
            continue
        results[addon_name] = process_addon(addon_name, addon_path, cfg, dry_run)

    # ---- Summary ----
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  Summary{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}\n")

    ok_count           = sum(1 for v in results.values() if v is True)
    skipped_count      = sum(1 for v in results.values() if v == "skipped")
    pull_failed_count  = sum(1 for v in results.values() if v == "pull_failed")
    fail_count         = sum(1 for v in results.values() if v is False)

    for name, result in results.items():
        if result is True:
            status = f"{GREEN}OK{RESET}"
        elif result == "skipped":
            status = f"{CYAN}SKIPPED (already up to date){RESET}"
        elif result == "pull_failed":
            status = f"{YELLOW}SKIPPED (git pull failed -- resolve manually then re-run){RESET}"
        else:
            status = f"{RED}FAILED (see errors above){RESET}"
        print(f"  {name:<30} {status}")

    print(f"\n  {ok_count} updated, {skipped_count} skipped, {pull_failed_count} pull failed, {fail_count} failed")

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