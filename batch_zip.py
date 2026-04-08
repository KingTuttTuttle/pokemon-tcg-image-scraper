#!/usr/bin/env python3
"""
batch_zip.py
------------
Cleanup crew for MissingImages/ language folders.

For each language it:
  1. Moves any CSVs found inside set folders in Collected/ → Missing Reports and Collection Logs/
  2. Extracts any loose .zip files found directly in Collected/ (no zip-in-zip)
  3. Packs all set folders from Collected/ into batch zips (max 1.9 GB each)
  4. Saves batch zips → {Language}/Need to Upload/  (created if absent)
  5. Deletes the source set folders from Collected/ after batching

Usage:
  python3 batch_zip.py
  caffeinate -i python3 batch_zip.py
"""

import os
import shutil
import zipfile

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSING_IMAGES  = os.path.join(SCRIPT_DIR, "MissingImages")
MAX_BATCH_BYTES = 1_900 * 1024 * 1024   # 1.9 GB


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def find_sibling(lang_folder: str, name_lower: str):
    """Return path of a sibling folder whose stripped lowercase name matches."""
    try:
        for entry in os.scandir(lang_folder):
            if entry.is_dir() and entry.name.strip().lower() == name_lower:
                return entry.path
    except PermissionError:
        pass
    return None


def move_csvs_to_logs(collected_folder: str, logs_folder: str) -> int:
    """Move any CSVs found inside set folders in Collected/ to the logs folder."""
    moved = 0
    for set_entry in os.scandir(collected_folder):
        if not set_entry.is_dir():
            continue
        set_name = set_entry.name
        for entry in os.scandir(set_entry.path):
            if not entry.is_file() or not entry.name.lower().endswith(".csv"):
                continue
            new_name  = f"{set_name}_{entry.name}"
            dest_path = os.path.join(logs_folder, new_name)
            counter   = 1
            while os.path.exists(dest_path):
                base, ext = os.path.splitext(new_name)
                dest_path = os.path.join(logs_folder, f"{base}_{counter}{ext}")
                counter  += 1
            shutil.move(entry.path, dest_path)
            print(f"    CSV → {os.path.basename(dest_path)}")
            moved += 1
    return moved


def extract_zips_in_collected(collected_folder: str) -> int:
    """
    Extract any .zip files found directly in Collected/ into set folders.
    Deletes the zip after successful extraction so there's no zip-in-zip.
    """
    extracted = 0
    for entry in os.scandir(collected_folder):
        if not entry.is_file() or not entry.name.lower().endswith(".zip"):
            continue

        set_name = os.path.splitext(entry.name)[0]
        dest_dir = os.path.join(collected_folder, set_name)

        if os.path.exists(dest_dir):
            print(f"    Skipping extract — folder already exists: {set_name}/")
            continue

        print(f"    Extracting {entry.name} → {set_name}/")
        try:
            os.makedirs(dest_dir, exist_ok=True)
            with zipfile.ZipFile(entry.path, "r") as zf:
                for member in zf.namelist():
                    # Strip top-level folder from paths like "SetName/001.jpg"
                    parts = member.split("/", 1)
                    filename = parts[1] if len(parts) == 2 and parts[0] == set_name else member
                    if not filename:
                        continue
                    out_path = os.path.join(dest_dir, filename)
                    os.makedirs(os.path.dirname(out_path), exist_ok=True)
                    with zf.open(member) as src, open(out_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
            os.remove(entry.path)
            print(f"      Zip deleted — folder ready.")
            extracted += 1
        except Exception as e:
            print(f"    ERROR extracting {entry.name}: {e}")
    return extracted


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


def next_batch_number(output_folder: str, lang_name: str) -> int:
    """Find the next unused batch number for this language."""
    n = 1
    while os.path.exists(os.path.join(output_folder, f"{lang_name}_batch_{n:03d}.zip")):
        n += 1
    return n


def write_batch(set_folders: list, batch_path: str) -> None:
    """Pack each set folder's images into the batch zip as SetName/filename."""
    with zipfile.ZipFile(batch_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for folder_path in set_folders:
            set_name = os.path.basename(folder_path)
            images   = sorted(
                (e for e in os.scandir(folder_path)
                 if e.is_file() and e.name.lower().endswith((".png", ".jpg"))),
                key=lambda e: e.name,
            )
            for img in images:
                zf.write(img.path, arcname=f"{set_name}/{img.name}")

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

        # Skip if Collected/ is empty
        has_content = any(True for _ in os.scandir(collected_folder))
        if not has_content:
            continue

        print(f"\n[{lang_name}]")

        # ── 1. Ensure logs folder exists ──────────────────────────────────────
        logs_folder = find_sibling(lang_entry.path, "missing reports and collection logs")
        if not logs_folder:
            logs_folder = os.path.join(lang_entry.path, "Missing Reports and Collection Logs")
            os.makedirs(logs_folder, exist_ok=True)
            print(f"  Created: Missing Reports and Collection Logs/")

        # ── 2. Move CSVs out of set folders ──────────────────────────────────
        csv_count = move_csvs_to_logs(collected_folder, logs_folder)
        if csv_count:
            print(f"  Moved {csv_count} CSV(s) to logs.")

        # ── 3. Extract any loose zips in Collected/ ───────────────────────────
        extracted = extract_zips_in_collected(collected_folder)
        if extracted:
            print(f"  Extracted {extracted} zip(s) into folders.")

        # ── 4. Get all set folders to batch ───────────────────────────────────
        set_folders = sorted(
            e.path for e in os.scandir(collected_folder)
            if e.is_dir()
        )
        if not set_folders:
            print(f"  Nothing to batch.")
            continue

        # ── 5. Ensure Need to Upload folder exists ────────────────────────────
        output_folder = find_sibling(lang_entry.path, "need to upload")
        if not output_folder:
            output_folder = os.path.join(lang_entry.path, "Need to Upload")
            os.makedirs(output_folder, exist_ok=True)
            print(f"  Created: Need to Upload/")

        print(f"  {len(set_folders)} set(s) to batch:")

        # ── 6. Warn about oversized sets ──────────────────────────────────────
        sizes = {p: folder_image_size(p) for p in set_folders}
        for p, sz in sizes.items():
            if sz > MAX_BATCH_BYTES:
                print(f"  WARNING: {os.path.basename(p)} is {sz / (1024**3):.2f} GB "
                      f"— will be its own batch.")

        # ── 7. Group into batches and write ───────────────────────────────────
        batch_num       = next_batch_number(output_folder, lang_name)
        current_folders = []
        current_size    = 0
        batched_folders = []

        for folder_path in set_folders:
            file_size = sizes[folder_path]

            if current_folders and current_size + file_size > MAX_BATCH_BYTES:
                batch_path = os.path.join(output_folder, f"{lang_name}_batch_{batch_num:03d}.zip")
                write_batch(current_folders, batch_path)
                batched_folders.extend(current_folders)
                total_batches  += 1
                total_sets     += len(current_folders)
                batch_num      += 1
                current_folders = []
                current_size    = 0

            current_folders.append(folder_path)
            current_size += file_size

        if current_folders:
            batch_path = os.path.join(output_folder, f"{lang_name}_batch_{batch_num:03d}.zip")
            write_batch(current_folders, batch_path)
            batched_folders.extend(current_folders)
            total_batches += 1
            total_sets    += len(current_folders)

        # ── 8. Delete source folders from Collected/ ──────────────────────────
        for folder_path in batched_folders:
            try:
                shutil.rmtree(folder_path)
            except Exception as e:
                print(f"  ERROR deleting {os.path.basename(folder_path)}: {e}")

        print(f"  Cleaned {len(batched_folders)} folder(s) from Collected/")

    print(f"\n{'─' * 50}")
    print(f"Done. {total_sets} set(s) packed into {total_batches} batch zip(s).")


if __name__ == "__main__":
    main()
