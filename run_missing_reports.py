#!/usr/bin/env python3
"""
run_missing_reports.py
----------------------
Scans all language folders inside MissingImages/, finds every set subfolder
inside each language's Need/ folder, and runs missing-images-report.js for
each one — saving the output CSV directly into the set folder.

Usage:
    python3 run_missing_reports.py

Re-running is safe — set folders that already contain a CSV are skipped
automatically (unless you pass --force to redo them all).

    python3 run_missing_reports.py --force
"""

import argparse
import glob
import os
import subprocess
import sys
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# Root folder containing all language subfolders
MISSING_IMAGES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "MissingImages"
)

# Path to the JS report script
JS_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "MissingImages", "missing-images-report.js"
)

# The name of the subfolder inside each language folder where sets live
NEED_FOLDER_NAME = "need"

# Maps language folder name (lowercase, stripped) → TCGdex language code
# Languages not listed here are skipped — the TCGdex API doesn't support them yet
LANGUAGE_CODE_MAP = {
    "english":               "en",
    "french":                "fr",
    "german":                "de",
    "spanish":               "es",
    "italian":               "it",
    "portuguese (brazil)":   "pt",
    "chinese (simplified)":  "zh-hans",
    "chinese (traditionnal)":"zh-hant",
    "chin (t)":              "zh-hant",   # legacy folder name
    "japanese":              "ja",
    "korean":                "ko",
    "thai":                  "th",
    "indonesian":            "id",
}

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def find_need_folder(lang_folder: str) -> Optional[str]:
    """Find the Need/ subfolder inside a language folder (case-insensitive)."""
    try:
        for entry in os.scandir(lang_folder):
            if entry.is_dir() and entry.name.strip().lower() == NEED_FOLDER_NAME:
                return entry.path
    except (FileNotFoundError, PermissionError):
        pass
    return None


def folder_has_csv(folder: str) -> bool:
    """Return True if the folder already contains a CSV (other than download_log.csv)."""
    for path in glob.glob(os.path.join(folder, "*.csv")):
        if os.path.basename(path) != "download_log.csv":
            return True
    return False


def run_report(lang_code: str, set_id: str, output_path: str) -> bool:
    """
    Run missing-images-report.js for a single language + set combination.
    Saves the CSV to output_path.
    Returns True on success, False on failure.
    """
    cmd = [
        "node", JS_SCRIPT,
        "--lang",        lang_code,
        "--set",         set_id,
        "--output",      output_path,
        "--concurrency", "3",    # only 3 parallel requests at a time (default is 10)
        "--rps",         "5",    # max 5 requests per second (default is 20)
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=os.path.dirname(JS_SCRIPT),   # run from the MissingImages folder
            capture_output=True,
            text=True,
            timeout=300,                        # 5 min max per set
        )
        if result.returncode != 0:
            print(f"    ERROR: {result.stderr.strip()}")
            return False
        return True
    except FileNotFoundError:
        print("    ERROR: 'node' not found — is Node.js installed?")
        print("    Download it from https://nodejs.org")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("    ERROR: timed out after 5 minutes")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run missing-images-report.js for every set folder in MissingImages/"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-run even for set folders that already contain a CSV"
    )
    args = parser.parse_args()

    if not os.path.isfile(JS_SCRIPT):
        print(f"ERROR: JS script not found at:\n  {JS_SCRIPT}")
        sys.exit(1)

    total_run      = 0
    total_skipped  = 0
    total_failed   = 0
    unsupported    = []

    # ── Scan every language folder ────────────────────────────────────────────
    try:
        lang_entries = sorted(os.scandir(MISSING_IMAGES_DIR), key=lambda e: e.name)
    except FileNotFoundError:
        print(f"ERROR: MissingImages folder not found:\n  {MISSING_IMAGES_DIR}")
        sys.exit(1)

    for lang_entry in lang_entries:
        if not lang_entry.is_dir():
            continue

        lang_name = lang_entry.name.strip()
        lang_code = LANGUAGE_CODE_MAP.get(lang_name.lower())

        if not lang_code:
            unsupported.append(lang_name)
            continue

        need_folder = find_need_folder(lang_entry.path)
        if not need_folder:
            continue

        # ── Scan every set folder inside Need/ ────────────────────────────────
        try:
            set_entries = sorted(os.scandir(need_folder), key=lambda e: e.name)
        except PermissionError:
            continue

        set_folders = [e for e in set_entries if e.is_dir()]
        if not set_folders:
            continue

        print(f"\n{'─' * 60}")
        print(f"  {lang_name}  [{lang_code}]  —  {len(set_folders)} set folder(s)")
        print(f"{'─' * 60}")

        for set_entry in set_folders:
            set_id     = set_entry.name.strip()
            set_folder = set_entry.path

            # Skip if a CSV already exists and --force not passed
            if not args.force and folder_has_csv(set_folder):
                print(f"  {set_id:<20}  [already has CSV — skipping]")
                total_skipped += 1
                continue

            # Name the output file using the set ID for easy identification
            output_filename = f"missing-images-{set_id}.csv"
            output_path     = os.path.join(set_folder, output_filename)

            print(f"  {set_id:<20}  running report...", end="", flush=True)
            success = run_report(lang_code, set_id, output_path)

            if success:
                # Check if the file was actually created and has content
                if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                    size_kb = os.path.getsize(output_path) / 1024
                    print(f"  ✓  saved {output_filename} ({size_kb:.1f} KB)")
                    total_run += 1
                else:
                    print(f"  ✓  done (no missing images found — no CSV written)")
                    total_run += 1
            else:
                total_failed += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─' * 60}")
    print(f"  Reports generated : {total_run}")
    print(f"  Skipped           : {total_skipped}  (already had a CSV)")
    print(f"  Failed            : {total_failed}")

    if unsupported:
        print(f"\n  Languages skipped (not yet in TCGdex API):")
        for lang in sorted(unsupported):
            print(f"    • {lang}")

    print()


if __name__ == "__main__":
    main()
