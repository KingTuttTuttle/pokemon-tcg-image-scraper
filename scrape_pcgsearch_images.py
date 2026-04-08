#!/usr/bin/env python3
"""
scrape_pcgsearch_images.py
--------------------------
Downloads Japanese Pokémon TCG card images from pcg-search.com.

Auto-detects which sets need images by scanning the Japanese/Need folder,
then downloads images directly using confirmed URL patterns — no detail-page
visit required.

Image URL pattern:
  https://pcg-search.com/img/{folder}/{prefix}{card:03d}.png
  e.g.  https://pcg-search.com/img/1st/1stgym1001.png   (PMCG5, card 001)
        https://pcg-search.com/img/e/e1001.png           (E1, card 001)
        https://pcg-search.com/img/pcg/pcg1001.png       (PCG1, card 001)

Confirmed sets (verified April 2026 against pcg-search.com):
  PMCG era  → folder: 1st  → PMCG1(1st1), PMCG3(1st3), PMCG4(1st4),
                              PMCG5(1stgym1), PMCG6(1stgym2)
  e era     → folder: e    → E1–E5 (e1..e5)
  PCG era   → folder: pcg  → PCG1–PCG9 (pcg1..pcg9)

Workflow:
  1. Scan Need folder → detect which configured sets are present
  2. Read missing-images-{SET}.csv → get card local IDs to download
  3. Build image URL, download PNG, convert to JPG (quality 95)
  4. Save as zero-padded filename: 001.jpg, 002.jpg, …
  5. Write download_log_{SET}.csv alongside images
  6. On 0 failures: move log CSV → Missing Reports and Collection Logs/
                    zip images  → Collected/{SET}.zip
                    remove Need/{SET}/ folder

Usage:
  python3 scrape_pcgsearch_images.py
  caffeinate -i python3 scrape_pcgsearch_images.py
"""

import csv
import os
import shutil
import time
import random
import logging
from io import BytesIO
from typing import Optional

import requests
from PIL import Image

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL      = "https://pcg-search.com"
DELAY_MIN     = 2.0    # seconds between downloads (be polite)
DELAY_MAX     = 4.0
MAX_RETRIES   = 3
RETRY_BACKOFF = 2.0    # extra seconds added per retry attempt

# ─────────────────────────────────────────────────────────────────────────────
# SET CONFIGURATION
# Maps TCGdex set code → (img_folder, url_prefix, japanese_set_name)
#
# Image URL: {BASE_URL}/img/{folder}/{prefix}{card_number:03d}.png
# ─────────────────────────────────────────────────────────────────────────────

SET_CONFIG: dict[str, tuple[str, str, str]] = {
    # ── PMCG era ─────────────────────────────────────────────────────────────
    "PMCG1": ("1st", "1st1",    "拡張パック"),
    "PMCG3": ("1st", "1st3",    "化石の秘密"),
    "PMCG4": ("1st", "1st4",    "ロケット団"),
    "PMCG5": ("1st", "1stgym1", "リーダーズスタジアム"),
    "PMCG6": ("1st", "1stgym2", "闇からの挑戦"),
    # ── e era ────────────────────────────────────────────────────────────────
    "E1":    ("e",   "e1",      "基本拡張パック"),
    "E2":    ("e",   "e2",      "地図にない町"),
    "E3":    ("e",   "e3",      "海からの風"),
    "E4":    ("e",   "e4",      "裂けた大地"),
    "E5":    ("e",   "e5",      "神秘なる山"),
    # ── PCG era ──────────────────────────────────────────────────────────────
    "PCG1":  ("pcg", "pcg1",   "伝説の飛翔"),
    "PCG2":  ("pcg", "pcg2",   "蒼空の激突"),
    "PCG3":  ("pcg", "pcg3",   "ロケット団の逆襲"),
    "PCG4":  ("pcg", "pcg4",   "金の空、銀の海"),
    "PCG5":  ("pcg", "pcg5",   "まぼろしの森"),
    "PCG6":  ("pcg", "pcg6",   "ホロンの研究塔"),
    "PCG7":  ("pcg", "pcg7",   "ホロンの幻影"),
    "PCG8":  ("pcg", "pcg8",   "きせきの結晶"),
    "PCG9":  ("pcg", "pcg9",   "さいはての攻防"),
}

# ─────────────────────────────────────────────────────────────────────────────
# SESSION
# ─────────────────────────────────────────────────────────────────────────────

def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://pcg-search.com/",
        "Accept":  "image/avif,image/webp,image/png,image/jpeg,*/*",
    })
    return session


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def polite_delay(min_s: float = DELAY_MIN, max_s: float = DELAY_MAX) -> None:
    time.sleep(random.uniform(min_s, max_s))


def fetch_with_retry(
    session: requests.Session,
    url: str,
    stream: bool = True,
) -> Optional[requests.Response]:
    """GET with retry/backoff. Returns None on 404 or all retries exhausted."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(url, timeout=(10, 30), stream=stream)
            if r.status_code == 404:
                log.warning(f"    404 Not Found — {url}")
                return None
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            log.warning(f"    Attempt {attempt}/{MAX_RETRIES} failed: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    log.error(f"    All {MAX_RETRIES} attempts failed — {url}")
    return None


def download_card(
    session: requests.Session,
    img_url: str,
    save_path: str,
) -> bool:
    """
    Download a PNG from pcg-search.com, convert to JPG, save.
    Returns True on success, False on failure.
    """
    if os.path.exists(save_path):
        log.info(f"    Already exists — skipping ({os.path.basename(save_path)})")
        return True

    resp = fetch_with_retry(session, img_url)
    if not resp:
        return False

    try:
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        img.save(save_path, "JPEG", quality=95, optimize=True)
        size_kb = os.path.getsize(save_path) / 1024
        log.info(f"    Saved {os.path.basename(save_path)}  ({size_kb:.1f} KB)")
        polite_delay()
        return True
    except Exception as e:
        log.error(f"    Image processing error: {e}")
        if os.path.exists(save_path):
            os.remove(save_path)   # remove partial file
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CSV READING
# ─────────────────────────────────────────────────────────────────────────────

def read_card_ids_from_csv(csv_path: str) -> list[str]:
    """
    Read assetLocalId values from a missing-images CSV.
    Returns a list of zero-padded card ID strings, e.g. ['001', '002', ...].
    """
    ids: list[str] = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            local_id = row.get("assetLocalId", "").strip().strip('"')
            if local_id:
                ids.append(local_id)
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# FOLDER HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def find_japanese_folder(script_dir: str) -> Optional[str]:
    """Locate MissingImages/Japanese/ relative to the project root."""
    candidate = os.path.join(script_dir, "MissingImages", "Japanese")
    return candidate if os.path.isdir(candidate) else None


def find_need_folder(japanese_folder: str) -> Optional[str]:
    """Locate the Need subfolder (handles trailing space quirk)."""
    for name in ("Need ", "Need"):
        candidate = os.path.join(japanese_folder, name)
        if os.path.isdir(candidate):
            return candidate
    return None


def detect_sets(need_folder: str) -> list[str]:
    """
    Return set codes present in Need folder that have a SET_CONFIG entry,
    sorted alphabetically.
    """
    found = []
    for entry in os.scandir(need_folder):
        if entry.is_dir() and entry.name in SET_CONFIG:
            found.append(entry.name)
    return sorted(found)


# ─────────────────────────────────────────────────────────────────────────────
# POST-DOWNLOAD: ZIP + MOVE TO COLLECTED
# ─────────────────────────────────────────────────────────────────────────────

def move_to_collected(
    set_dir: str,
    set_code: str,
    japanese_folder: str,
    total_failures: int,
) -> None:
    """
    On 0 failures:
      - Move the download log CSV → Missing Reports and Collection Logs/
      - Move the set folder       → Collected/{set_code}/
    """
    if total_failures > 0:
        log.info(f"\n  {total_failures} card(s) failed — folder left in Need for retry.")
        return

    # ── Directories ──────────────────────────────────────────────────────────
    collected_dir = os.path.join(japanese_folder, "Collected")
    logs_dir      = os.path.join(japanese_folder, "Missing Reports and Collection Logs")
    os.makedirs(collected_dir, exist_ok=True)
    os.makedirs(logs_dir,      exist_ok=True)

    # ── Move log CSV to logs folder ───────────────────────────────────────────
    log_csv = os.path.join(set_dir, f"download_log_{set_code}.csv")
    if os.path.exists(log_csv):
        dest = os.path.join(logs_dir, f"download_log_{set_code}.csv")
        shutil.move(log_csv, dest)
        log.info(f"  Log CSV moved → Missing Reports and Collection Logs/")

    # ── Move folder to Collected ──────────────────────────────────────────────
    destination = os.path.join(collected_dir, set_code)
    if os.path.exists(destination):
        log.warning(f"  '{set_code}' already exists in Collected — folder left in place.")
        return
    shutil.move(str(os.path.abspath(set_dir)), destination)
    log.info(f"  Moved '{set_code}' → Collected/ ✓  (run batch_zip.py to package for upload)")


# ─────────────────────────────────────────────────────────────────────────────
# PER-SET PROCESSING
# ─────────────────────────────────────────────────────────────────────────────

def process_set(
    session:         requests.Session,
    set_code:        str,
    need_folder:     str,
    japanese_folder: str,
) -> None:
    folder, prefix, set_name_jp = SET_CONFIG[set_code]
    set_dir  = os.path.join(need_folder, set_code)
    csv_path = os.path.join(set_dir, f"missing-images-{set_code}.csv")

    if not os.path.exists(csv_path):
        log.warning(f"  No CSV found at {csv_path} — skipping {set_code}.")
        return

    card_ids = read_card_ids_from_csv(csv_path)
    if not card_ids:
        log.warning(f"  CSV is empty for {set_code} — skipping.")
        return

    print(f"\n{'─'*60}")
    print(f"Set:     {set_code}  ({set_name_jp})")
    print(f"Cards:   {len(card_ids)}")
    print(f"Pattern: {BASE_URL}/img/{folder}/{prefix}NNN.png")
    print(f"{'─'*60}")

    # ── Prepare download log ──────────────────────────────────────────────────
    log_path   = os.path.join(set_dir, f"download_log_{set_code}.csv")
    fieldnames = ["position", "card_id", "image_url", "local_filename", "status"]

    total_failures = 0

    with open(log_path, "w", newline="", encoding="utf-8") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=fieldnames)
        writer.writeheader()

        for local_id in card_ids:
            try:
                card_num = int(local_id)
            except ValueError:
                log.warning(f"  Bad card ID '{local_id}' — skipping.")
                continue

            img_url   = f"{BASE_URL}/img/{folder}/{prefix}{card_num:03d}.png"
            filename  = f"{card_num:03d}.jpg"
            save_path = os.path.join(set_dir, filename)

            log.info(f"\n  Card {local_id}  |  {img_url}")

            success = download_card(session, img_url, save_path)
            status  = "downloaded" if success else "FAILED"
            if not success:
                total_failures += 1

            writer.writerow({
                "position":       card_num,
                "card_id":        f"{set_code}-{local_id}",
                "image_url":      img_url,
                "local_filename": filename,
                "status":         status,
            })

    downloaded = len(card_ids) - total_failures
    log.info(f"\n  Result: {downloaded}/{len(card_ids)} downloaded, "
             f"{total_failures} failed.")

    move_to_collected(set_dir, set_code, japanese_folder, total_failures)


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    # Project root is one level above the Scripts/ folder
    script_dir       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    japanese_folder  = find_japanese_folder(script_dir)

    if not japanese_folder:
        log.error("Cannot find MissingImages/Japanese/ — check your folder structure.")
        return

    need_folder = find_need_folder(japanese_folder)
    if not need_folder:
        log.error("Cannot find Need folder inside Japanese/.")
        return

    # ── Auto-detect sets ──────────────────────────────────────────────────────
    sets_to_process = detect_sets(need_folder)

    if not sets_to_process:
        print("\nNo matching sets found in Need folder.")
        print(f"Configured sets: {', '.join(SET_CONFIG.keys())}")
        return

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  PCG Search Scraper  —  pcg-search.com")
    print("═" * 60)
    print(f"\n  Found {len(sets_to_process)} set(s) to process:\n")

    total_cards = 0
    for s in sets_to_process:
        _, _, name = SET_CONFIG[s]
        csv_path   = os.path.join(need_folder, s, f"missing-images-{s}.csv")
        count      = (sum(1 for _ in open(csv_path, encoding="utf-8")) - 1
                      if os.path.exists(csv_path) else 0)
        total_cards += count
        print(f"    {s:<8}  {name:<24}  ({count} cards)")

    print(f"\n  Total cards to download: {total_cards}")
    print(f"  Estimated time: {total_cards * 3 // 60}–{total_cards * 4 // 60} minutes")
    print("\n  Press Enter to start, or Ctrl+C to cancel...")

    try:
        input()
    except KeyboardInterrupt:
        print("\nCancelled.")
        return

    # ── Run ───────────────────────────────────────────────────────────────────
    session = create_session()
    try:
        for set_code in sets_to_process:
            process_set(session, set_code, need_folder, japanese_folder)
    except KeyboardInterrupt:
        log.info("\n\nInterrupted by user — progress saved.")
    finally:
        session.close()

    print("\n" + "═" * 60)
    print("  All sets processed.")
    print("═" * 60)


if __name__ == "__main__":
    main()


# ─────────────────────────────────────────────────────────────────────────────
# USAGE
# ─────────────────────────────────────────────────────────────────────────────
#
#   cd "/Users/kingtutt/Desktop/Missing Images CSVs/Scripts"
#   python3 scrape_pcgsearch_images.py
#
#   Or with caffeinate to prevent sleep:
#   caffeinate -i python3 scrape_pcgsearch_images.py
#
# ─────────────────────────────────────────────────────────────────────────────
# ADDING NEW SETS
# ─────────────────────────────────────────────────────────────────────────────
#
# If pcg-search.com adds more sets you need, add them to SET_CONFIG:
#
#   "SETCODE": ("folder", "prefix", "日本語名"),
#
# Then put a matching Need/{SETCODE}/missing-images-{SETCODE}.csv in place
# and re-run the script.
#
# To find the folder/prefix for a new set:
#   1. Open a card page on pcg-search.com
#   2. Right-click the card image → Copy Image Address
#   3. URL is: pcg-search.com/img/{folder}/{prefix}{NNN}.png
#
# ─────────────────────────────────────────────────────────────────────────────
