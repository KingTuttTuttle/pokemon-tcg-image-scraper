#!/usr/bin/env python3
"""
move_collection_csvs.py
-----------------------
One-time cleanup script.

For every language folder inside MissingImages/, this script:
  1. Looks inside the Collected/ subfolder for any set folders containing CSVs.
  2. Moves those CSVs into the sibling 'Missing Reports and Collection Logs' folder.
  3. Renames each CSV to '{SetName}_{original_filename}' to avoid clashes.

Run this once to tidy up CSVs that are already sitting in Collected set folders.
Going forward, the scraper handles this automatically after each successful scrape.

Usage:
  python3 move_collection_csvs.py
"""

import os
import shutil

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR       = os.path.dirname(os.path.abspath(__file__))
MISSING_IMAGES   = os.path.join(SCRIPT_DIR, "MissingImages")
LOGS_FOLDER_NAME = "Missing Reports and Collection Logs"
UPLOADED_FOLDER_NAME = "Uploaded"

# These folders will be created inside every language folder if missing
AUTO_CREATE_FOLDERS = [LOGS_FOLDER_NAME, UPLOADED_FOLDER_NAME]


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def find_sibling(lang_folder: str, name_lower: str):
    """Return the path of a sibling folder whose stripped lower name matches."""
    try:
        for entry in os.scandir(lang_folder):
            if entry.is_dir() and entry.name.strip().lower() == name_lower:
                return entry.path
    except PermissionError:
        pass
    return None


def move_csvs_from_set_folder(set_folder: str, logs_folder: str, set_name: str) -> int:
    """
    Move all CSVs found in set_folder into logs_folder.
    Files are renamed to '{set_name}_{original_name}' to avoid name clashes.
    Returns the number of files moved.
    """
    moved = 0
    try:
        for entry in os.scandir(set_folder):
            if entry.is_file() and entry.name.lower().endswith(".csv"):
                new_name = f"{set_name}_{entry.name}"
                destination = os.path.join(logs_folder, new_name)

                # If a file with that name already exists, add a counter suffix
                counter = 1
                while os.path.exists(destination):
                    base, ext = os.path.splitext(new_name)
                    destination = os.path.join(logs_folder, f"{base}_{counter}{ext}")
                    counter += 1

                shutil.move(entry.path, destination)
                print(f"    Moved: {entry.name}  →  {os.path.basename(destination)}")
                moved += 1
    except PermissionError as e:
        print(f"    Permission error: {e}")
    return moved


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not os.path.isdir(MISSING_IMAGES):
        print(f"MissingImages folder not found at: {MISSING_IMAGES}")
        return

    total_moved = 0

    for lang_entry in sorted(os.scandir(MISSING_IMAGES), key=lambda e: e.name):
        if not lang_entry.is_dir():
            continue

        lang_name = lang_entry.name.strip()

        # Auto-create Missing Reports and Collection Logs + Uploaded if absent
        for folder_name in AUTO_CREATE_FOLDERS:
            if not find_sibling(lang_entry.path, folder_name.lower()):
                new_path = os.path.join(lang_entry.path, folder_name)
                os.makedirs(new_path, exist_ok=True)
                print(f"\n[{lang_name}] Created: {folder_name}/")

        collected_folder = find_sibling(lang_entry.path, "collected")
        if not collected_folder:
            continue  # no Collected folder for this language yet

        logs_folder = find_sibling(lang_entry.path, LOGS_FOLDER_NAME.lower())
        if not logs_folder:
            continue  # shouldn't happen after auto-create above, but safety check

        # Scan each set folder inside Collected/
        lang_moved = 0
        try:
            set_entries = sorted(os.scandir(collected_folder), key=lambda e: e.name)
        except PermissionError:
            print(f"\n[{lang_name}] Permission error reading Collected folder — skipping.")
            continue

        for set_entry in set_entries:
            if not set_entry.is_dir():
                continue
            set_name = set_entry.name.strip()

            # Check if this set folder actually contains any CSVs
            has_csvs = any(
                f.is_file() and f.name.lower().endswith(".csv")
                for f in os.scandir(set_entry.path)
            )
            if not has_csvs:
                continue

            print(f"\n[{lang_name}] {set_name}")
            moved = move_csvs_from_set_folder(set_entry.path, logs_folder, set_name)
            lang_moved += moved

        if lang_moved > 0:
            print(f"  → {lang_moved} CSV(s) moved for {lang_name}")
            total_moved += lang_moved

    print(f"\n{'─' * 50}")
    print(f"Done. {total_moved} CSV(s) moved in total.")


if __name__ == "__main__":
    main()
