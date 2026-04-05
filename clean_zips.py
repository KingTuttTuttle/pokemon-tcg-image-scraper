#!/usr/bin/env python3
"""
clean_zips.py
-------------
Scans all zip files inside every language's Collected/ folder.
For each zip:
  1. Lists all contents and flags anything that isn't a .png
  2. Moves any CSV files found to the language's Missing Reports and
     Collection Logs/ folder (renamed with set name prefix to avoid clashes)
  3. Removes all non-PNG files from the zip (CSVs, .DS_Store, __MACOSX, etc.)

Run this any time before uploading to make sure zips are clean.

Usage:
  python3 clean_zips.py
"""

import os
import shutil
import tempfile
import zipfile

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR       = os.path.dirname(os.path.abspath(__file__))
MISSING_IMAGES   = os.path.join(SCRIPT_DIR, "MissingImages")
LOGS_FOLDER_NAME = "Missing Reports and Collection Logs"


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def find_sibling(lang_folder: str, name_lower: str):
    """Return path of a sibling folder whose stripped lower name matches."""
    try:
        for entry in os.scandir(lang_folder):
            if entry.is_dir() and entry.name.strip().lower() == name_lower:
                return entry.path
    except PermissionError:
        pass
    return None


def safe_dest_path(folder: str, filename: str) -> str:
    """Return a unique destination path, adding a counter suffix if needed."""
    dest = os.path.join(folder, filename)
    counter = 1
    while os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        dest = os.path.join(folder, f"{base}_{counter}{ext}")
        counter += 1
    return dest


def is_png(name: str) -> bool:
    return name.lower().endswith(".png")


def should_remove(name: str) -> bool:
    """Return True for anything that should not be in the final zip."""
    parts = name.replace("\\", "/").split("/")
    # Remove __MACOSX metadata folders entirely
    if "__MACOSX" in parts:
        return True
    # Remove .DS_Store and other hidden Mac files
    filename = parts[-1]
    if filename.startswith("."):
        return True
    # Remove anything that isn't a .png
    if not is_png(filename):
        return True
    return False


def clean_zip(zip_path: str, logs_folder: str, lang_name: str) -> None:
    """
    Clean a single zip file:
      - Move any CSVs inside to logs_folder
      - Remove all non-PNG entries
      - Rewrite the zip in place with only PNGs
    """
    print(f"\n  Checking: {os.path.basename(zip_path)}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        all_names = zf.namelist()

    non_png = [
        n for n in all_names
        if not n.endswith("/") and should_remove(n)
    ]
    png_entries = [
        n for n in all_names
        if not n.endswith("/") and not should_remove(n)
    ]

    if not non_png:
        print(f"    Already clean — no non-PNG files found.")
        return

    print(f"    Found {len(non_png)} non-PNG file(s) to remove:")
    for name in non_png:
        print(f"      - {name}")

    # Extract and save any CSVs to the logs folder before removing
    csv_entries = [n for n in non_png if n.lower().endswith(".csv")]
    if csv_entries and logs_folder:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for entry in csv_entries:
                parts = entry.replace("\\", "/").split("/")
                set_name  = parts[0] if len(parts) > 1 else "unknown"
                orig_name = parts[-1]
                new_name  = f"{set_name}_{orig_name}"
                dest      = safe_dest_path(logs_folder, new_name)
                with zf.open(entry) as src, open(dest, "wb") as dst:
                    dst.write(src.read())
                print(f"    CSV saved → {LOGS_FOLDER_NAME}/{os.path.basename(dest)}")

    # Rewrite the zip keeping only PNGs
    tmp_path = zip_path + ".tmp"
    try:
        with zipfile.ZipFile(zip_path, "r") as zf_in, \
             zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf_out:
            for entry in png_entries:
                zf_out.writestr(entry, zf_in.read(entry))

        os.replace(tmp_path, zip_path)
        print(f"    Done — {len(png_entries)} PNG(s) kept, {len(non_png)} file(s) removed.")

    except Exception as e:
        print(f"    ERROR rewriting zip: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not os.path.isdir(MISSING_IMAGES):
        print(f"MissingImages folder not found at: {MISSING_IMAGES}")
        return

    total_zips    = 0
    total_cleaned = 0

    for lang_entry in sorted(os.scandir(MISSING_IMAGES), key=lambda e: e.name):
        if not lang_entry.is_dir():
            continue

        lang_name = lang_entry.name.strip()

        # Scan both Collected/ and Uploaded/ for zip files
        zip_files = []
        for scan_folder_name in ["collected", "uploaded"]:
            scan_folder = find_sibling(lang_entry.path, scan_folder_name)
            if not scan_folder:
                continue
            try:
                for e in os.scandir(scan_folder):
                    if e.is_file() and e.name.lower().endswith(".zip"):
                        zip_files.append(e.path)
            except PermissionError:
                print(f"\n[{lang_name}] Permission error reading {scan_folder_name}/ — skipping.")
                continue

        if not zip_files:
            continue

        # Find or auto-create the logs folder
        logs_folder = find_sibling(lang_entry.path, LOGS_FOLDER_NAME.lower())
        if not logs_folder:
            logs_folder = os.path.join(lang_entry.path, LOGS_FOLDER_NAME)
            os.makedirs(logs_folder, exist_ok=True)
            print(f"\n[{lang_name}] Created: {LOGS_FOLDER_NAME}/")

        print(f"\n[{lang_name}] Found {len(zip_files)} zip(s) in Collected/")
        total_zips += len(zip_files)

        for zip_path in sorted(zip_files):
            clean_zip(zip_path, logs_folder, lang_name)
            total_cleaned += 1

    print(f"\n{'─' * 50}")
    print(f"Done. {total_cleaned} zip(s) processed.")


if __name__ == "__main__":
    main()
