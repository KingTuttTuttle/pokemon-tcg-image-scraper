#!/usr/bin/env python3
"""
batch_zip.py
------------
Groups set folders from each language's Collected/ folder into upload-ready
batch zips, each safely under the 2 GB server limit (capped at 1.9 GB).

Structure inside each batch zip:
  SetCode/
    001.png
    002.png
    ...
  AnotherSet/
    001.png
    ...

Output batch zips are written to:
  MissingImages/{Language}/Uploaded/

Named like:
  Chinese (simplified)_batch_001.zip
  Chinese (simplified)_batch_002.zip
  ...

Already-batched sets are tracked by reading existing batch zips in Uploaded/
so re-running the script only processes new sets added since the last run.

Usage:
  python3 batch_zip.py
"""

import os
import zipfile

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSING_IMAGES  = os.path.join(SCRIPT_DIR, "MissingImages")
MAX_BATCH_BYTES = 1_900 * 1024 * 1024   # 1.9 GB — headroom under 2 GB limit


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


def already_batched_sets(uploaded_folder: str) -> set:
    """
    Read all existing batch zips in Uploaded/ and return the set of
    set folder names already included (e.g. {'SV7', 'SV8'}).
    Detected by reading top-level folder names inside each batch zip.
    """
    seen = set()
    if not os.path.isdir(uploaded_folder):
        return seen
    for entry in os.scandir(uploaded_folder):
        if not entry.is_file() or not entry.name.lower().endswith(".zip"):
            continue
        try:
            with zipfile.ZipFile(entry.path, "r") as zf:
                for name in zf.namelist():
                    top = name.split("/")[0]
                    if top:
                        seen.add(top)
        except Exception:
            pass
    return seen


def next_batch_number(uploaded_folder: str, lang_name: str) -> int:
    """Find the next unused batch number for this language."""
    n = 1
    while os.path.exists(os.path.join(uploaded_folder, f"{lang_name}_batch_{n:03d}.zip")):
        n += 1
    return n


def folder_image_size(folder_path: str) -> int:
    """Return the total size of all image files in a folder."""
    total = 0
    try:
        for entry in os.scandir(folder_path):
            if entry.is_file() and entry.name.lower().endswith((".png", ".jpg")):
                total += entry.stat().st_size
    except PermissionError:
        pass
    return total


def write_batch(set_folders: list, batch_path: str) -> None:
    """
    Pack each set folder's images into the batch zip as SetName/filename.
    No zip-in-zip — images go directly into set-named folders.
    """
    with zipfile.ZipFile(batch_path, "w", compression=zipfile.ZIP_DEFLATED) as batch_zf:
        for folder_path in set_folders:
            set_name = os.path.basename(folder_path)
            images = sorted(
                (e for e in os.scandir(folder_path)
                 if e.is_file() and e.name.lower().endswith((".png", ".jpg"))),
                key=lambda e: e.name,
            )
            for img in images:
                batch_zf.write(img.path, arcname=f"{set_name}/{img.name}")

    size_mb = os.path.getsize(batch_path) / (1024 * 1024)
    names   = ", ".join(os.path.basename(p) for p in set_folders)
    print(f"    → {os.path.basename(batch_path)}  ({size_mb:.1f} MB)  [{names}]")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if not os.path.isdir(MISSING_IMAGES):
        print(f"MissingImages folder not found at: {MISSING_IMAGES}")
        return

    total_batches = 0
    total_sets    = 0

    for lang_entry in sorted(os.scandir(MISSING_IMAGES), key=lambda e: e.name):
        if not lang_entry.is_dir():
            continue

        lang_name        = lang_entry.name.strip()
        collected_folder = find_sibling(lang_entry.path, "collected")
        if not collected_folder:
            continue

        # All set folders in this language's Collected/ folder
        all_set_folders = sorted(
            e.path for e in os.scandir(collected_folder)
            if e.is_dir()
        )
        if not all_set_folders:
            continue

        # Find or create Uploaded/ folder
        uploaded_folder = find_sibling(lang_entry.path, "uploaded")
        if not uploaded_folder:
            uploaded_folder = os.path.join(lang_entry.path, "Uploaded")
            os.makedirs(uploaded_folder, exist_ok=True)
            print(f"\n[{lang_name}] Created Uploaded/ folder.")

        # Filter out sets already included in a previous batch
        done       = already_batched_sets(uploaded_folder)
        new_folders = [
            p for p in all_set_folders
            if os.path.basename(p) not in done
        ]
        skip_count = len(all_set_folders) - len(new_folders)

        if not new_folders:
            print(f"\n[{lang_name}] All {len(all_set_folders)} set(s) already batched — nothing to do.")
            continue

        print(f"\n[{lang_name}] {len(new_folders)} new set(s) to batch"
              + (f"  ({skip_count} already batched)" if skip_count else "") + ":")

        # Size estimates based on actual image file sizes
        sizes = {p: folder_image_size(p) for p in new_folders}

        for p, sz in sizes.items():
            if sz > MAX_BATCH_BYTES:
                print(f"  WARNING: {os.path.basename(p)} is {sz / (1024**3):.2f} GB "
                      f"— larger than the 2 GB limit. It will go in its own batch.")

        # ── Group into batches ────────────────────────────────────────────────
        batch_num      = next_batch_number(uploaded_folder, lang_name)
        current_folders = []
        current_size   = 0

        for folder_path in new_folders:
            file_size = sizes[folder_path]

            if current_folders and current_size + file_size > MAX_BATCH_BYTES:
                batch_path = os.path.join(uploaded_folder, f"{lang_name}_batch_{batch_num:03d}.zip")
                write_batch(current_folders, batch_path)
                total_batches  += 1
                total_sets     += len(current_folders)
                batch_num      += 1
                current_folders = []
                current_size   = 0

            current_folders.append(folder_path)
            current_size += file_size

        # Flush final batch
        if current_folders:
            batch_path = os.path.join(uploaded_folder, f"{lang_name}_batch_{batch_num:03d}.zip")
            write_batch(current_folders, batch_path)
            total_batches += 1
            total_sets    += len(current_folders)

    print(f"\n{'─' * 50}")
    print(f"Done. {total_sets} set(s) packed into {total_batches} batch zip(s).")


if __name__ == "__main__":
    main()
