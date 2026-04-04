#!/usr/bin/env python3
"""
create_set_folders.py
---------------------
Reads all CSV files in a folder, finds each file's setId,
creates a folder for that setId, and moves the CSV into it.

Usage:
  python3 create_set_folders.py

To use for a different language folder, update CSV_FOLDER and OUTPUT_FOLDER below.
"""

import csv
import os
import glob
import shutil

# ── CONFIGURE THESE TWO PATHS ─────────────────────────────────────────────────

# Folder containing your CSV files
CSV_FOLDER = "/Users/kingtutt/Desktop/Missing Images CSVs/MissingImages/Chin (t) /Need "

# Folder where the set folders will be created (usually the same as CSV_FOLDER)
OUTPUT_FOLDER = "/Users/kingtutt/Desktop/Missing Images CSVs/MissingImages/Chin (t) /Need "

# ─────────────────────────────────────────────────────────────────────────────

def get_set_id_from_csv(filepath):
    """Read the first data row of a CSV and return its setId, or None if not found."""
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            set_id = row.get("setId", "").strip()
            if set_id:
                return set_id
    return None


def main():
    # Find all CSV files directly in the folder (not inside subfolders)
    csv_files = glob.glob(os.path.join(CSV_FOLDER, "*.csv"))

    if not csv_files:
        print(f"No CSV files found in:\n  {CSV_FOLDER}")
        return

    print(f"Found {len(csv_files)} CSV file(s).\n")

    moved = 0
    skipped = 0

    for filepath in sorted(csv_files):
        filename = os.path.basename(filepath)
        set_id = get_set_id_from_csv(filepath)

        # Warn and skip CSVs with no setId
        if not set_id:
            print(f"  [no setId]  {filename}  ← left in place")
            skipped += 1
            continue

        # Create the set folder if it doesn't exist yet
        folder_path = os.path.join(OUTPUT_FOLDER, set_id)
        if not os.path.exists(folder_path):
            os.makedirs(folder_path)
            print(f"  [created folder]  {set_id}/")

        # Move the CSV into the set folder
        destination = os.path.join(folder_path, filename)
        if os.path.exists(destination):
            print(f"  [already there]   {set_id}/{filename}")
        else:
            shutil.move(filepath, destination)
            print(f"  [moved]           {filename}  →  {set_id}/")
            moved += 1

    print(f"\nDone.  {moved} file(s) moved,  {skipped} skipped (no setId).")
    print(f"Folders are inside:\n  {OUTPUT_FOLDER}")


if __name__ == "__main__":
    main()
