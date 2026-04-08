#!/usr/bin/env python3
"""
scrape_official_japanese.py
---------------------------
Downloads Japanese Pokémon TCG card images from the official site:
  https://www.pokemon-card.com

API endpoint (discovered April 2026):
  https://www.pokemon-card.com/card-search/resultAPI.php
  Parameters:
    pg={SetCode}          — filters results to a specific set
    page={N}              — page number (default 1)
    regulation_sidebar_form=all
    keyword=&se_ta=&illust=&sm_and_keyword=true

Images are served as JPG from:
  https://www.pokemon-card.com{cardThumbFile}

How it works:
  1. Scans MissingImages/Japanese/Need/ for all set folders
  2. Checks each set code against the API
  3. Skips sets that return 0 results (not on official site)
  4. Downloads only sets that have folders AND API data
  5. Applies CSV filter if a CSV is found in the set folder
  6. Names files 001.jpg, 002.jpg... by card position
  7. Moves completed sets to Collected/, zips, moves CSVs to logs

Usage:
  python3 scrape_official_japanese.py

  When prompted:
    - Press Enter to auto-scan ALL Japanese Need folders
    - Or type specific set codes (one per line) then blank line to start
"""

import csv
import glob
import json
import os
import re
import shutil
import time
import random
import logging
import zipfile
from typing import Optional, List, Set, Dict
from urllib.parse import urljoin

try:
    import readline
except ImportError:
    pass

import requests


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL     = "https://www.pokemon-card.com"
API_ENDPOINT = f"{BASE_URL}/card-search/resultAPI.php"
API_PARAMS   = {
    "keyword":                 "",
    "se_ta":                   "",
    "regulation_sidebar_form": "all",
    "illust":                  "",
    "sm_and_keyword":          "true",
}

JAPANESE_FOLDER_NAME = "Japanese"

DEFAULT_DELAY_MIN = 2.0
DEFAULT_DELAY_MAX = 4.0
MAX_RETRIES       = 3
RETRY_BACKOFF     = 2.0


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION
# ─────────────────────────────────────────────────────────────────────────────

def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Referer":         f"{BASE_URL}/card-search/",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def polite_delay(delay_min: float = DEFAULT_DELAY_MIN, delay_max: float = DEFAULT_DELAY_MAX) -> None:
    delay = random.uniform(delay_min, delay_max)
    log.info(f"  Sleeping for {delay:.2f} seconds...")
    time.sleep(delay)


def fetch_with_retry(
    session: requests.Session,
    url: str,
    stream: bool = False,
    params: Optional[Dict] = None,
) -> Optional[requests.Response]:
    """Fetch a URL with retry logic. Returns response or None on failure."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, params=params, stream=stream, timeout=30)
            if resp.status_code == 200:
                return resp
            log.warning(f"    HTTP {resp.status_code} on attempt {attempt}: {url}")
        except requests.RequestException as e:
            log.warning(f"    Request error on attempt {attempt}: {e}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
    log.error(f"    Failed after {MAX_RETRIES} attempts: {url}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

def fetch_set_page(session: requests.Session, set_code: str, page: int) -> Optional[Dict]:
    """
    Fetch one page of cards for a given set code from the official API.
    Returns the parsed JSON dict or None on failure.
    """
    params = dict(API_PARAMS)
    params["pg"]   = set_code
    params["page"] = page

    resp = fetch_with_retry(session, API_ENDPOINT, params=params)
    if not resp:
        return None
    try:
        return resp.json()
    except Exception as e:
        log.error(f"    Could not parse API response: {e}")
        return None


def check_set_exists(session: requests.Session, set_code: str) -> int:
    """
    Check if a set code returns any cards from the API.
    Returns the hitCnt (0 means not available on the official site).
    """
    data = fetch_set_page(session, set_code, 1)
    if not data:
        return 0
    return data.get("hitCnt", 0)


# ─────────────────────────────────────────────────────────────────────────────
# FOLDER SCANNING
# ─────────────────────────────────────────────────────────────────────────────

def find_japanese_need_folder(script_dir: str) -> Optional[str]:
    """Find the Japanese/Need/ folder inside MissingImages/."""
    missing_images = os.path.join(script_dir, "MissingImages")
    if not os.path.isdir(missing_images):
        return None
    for lang_entry in os.scandir(missing_images):
        if lang_entry.is_dir() and lang_entry.name.strip().lower() == "japanese":
            for sub_entry in os.scandir(lang_entry.path):
                if sub_entry.is_dir() and sub_entry.name.strip().lower() == "need":
                    return sub_entry.path
    return None


def get_all_set_folders(need_folder: str) -> List[str]:
    """Return all set folder names (set codes) inside the Need folder."""
    try:
        return sorted(
            e.name for e in os.scandir(need_folder)
            if e.is_dir()
        )
    except PermissionError:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# CSV FILTER
# ─────────────────────────────────────────────────────────────────────────────

def load_required_positions(output_dir: str) -> Optional[Set[int]]:
    """
    Look for a TCGdex CSV in the output folder and read its assetLocalId column.
    Returns a set of card positions to download, or None to download everything.
    download_log.csv is ignored automatically.
    """
    csv_files = [
        f for f in glob.glob(os.path.join(output_dir, "*.csv"))
        if os.path.basename(f) != "download_log.csv"
    ]
    if not csv_files:
        return None

    if len(csv_files) > 1:
        print("\nMultiple CSV files found:")
        for i, path in enumerate(csv_files, 1):
            print(f"  {i}. {os.path.basename(path)}")
        choice = input("Enter the number to use as filter (or Enter for all): ").strip()
        if not choice:
            return None
        try:
            csv_path = csv_files[int(choice) - 1]
        except (ValueError, IndexError):
            return None
    else:
        csv_path = csv_files[0]

    positions = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            local_id = row.get("assetLocalId", "").strip()
            if local_id:
                try:
                    positions.add(int(local_id))
                except ValueError:
                    pass

    if not positions:
        log.warning("CSV found but no valid assetLocalId values — downloading all.")
        return None

    log.info(f"Filter CSV: {os.path.basename(csv_path)}")
    log.info(f"Will download {len(positions)} card(s): "
             f"{sorted(positions)[:5]}{'…' if len(positions) > 5 else ''}")
    return positions


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────

def download_image(
    session: requests.Session,
    img_url: str,
    output_dir: str,
    filename: str,
    delay_min: float,
    delay_max: float,
) -> bool:
    """Download one image and save it. Returns True on success."""
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        log.info(f"  Already exists — skipping  ({filename})")
        return True

    resp = fetch_with_retry(session, img_url, stream=True)
    if not resp:
        return False

    with open(filepath, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)

    file_size_kb = os.path.getsize(filepath) / 1024
    log.info(f"  Saved {filename}  ({file_size_kb:.1f} KB)")
    polite_delay(delay_min, delay_max)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# MOVE TO COLLECTED + ZIP
# ─────────────────────────────────────────────────────────────────────────────

def move_to_collected(output_dir: str, total_failures: int) -> None:
    """
    Move completed set to Collected/, clean non-JPG files, zip, move CSVs to logs.
    Only fires if total_failures == 0.
    """
    if total_failures > 0:
        log.info(f"\n{total_failures} card(s) failed — folder left in Need for retry.")
        return

    need_folder = os.path.dirname(os.path.abspath(output_dir))
    lang_folder = os.path.dirname(need_folder)
    set_name    = os.path.basename(os.path.abspath(output_dir))

    # Auto-create Missing Reports and Uploaded folders if absent
    for auto_folder in ["Missing Reports and Collection Logs", "Uploaded"]:
        exists = any(
            e.is_dir() and e.name.strip().lower() == auto_folder.lower()
            for e in os.scandir(lang_folder)
        )
        if not exists:
            os.makedirs(os.path.join(lang_folder, auto_folder), exist_ok=True)
            log.info(f"Created folder: {auto_folder}/")

    # Find Collected folder
    collected_folder = None
    for entry in os.scandir(lang_folder):
        if entry.is_dir() and entry.name.strip().lower() == "collected":
            collected_folder = entry.path
            break

    if not collected_folder:
        log.info("No 'Collected' folder found — skipping move.")
        return

    destination = os.path.join(collected_folder, set_name)
    if os.path.exists(destination):
        log.warning(f"'{set_name}' already exists in Collected — left in place.")
        return

    shutil.move(str(os.path.abspath(output_dir)), destination)
    log.info(f"All cards successful — moved '{set_name}' → Collected/ ✓")

    # Find logs folder
    logs_folder = None
    for entry in os.scandir(lang_folder):
        if entry.is_dir() and entry.name.strip().lower() == "missing reports and collection logs":
            logs_folder = entry.path
            break

    # Move CSVs to logs
    if logs_folder:
        try:
            for entry in os.scandir(destination):
                if entry.is_file() and entry.name.lower().endswith(".csv"):
                    new_name  = f"{set_name}_{entry.name}"
                    dest_path = os.path.join(logs_folder, new_name)
                    counter   = 1
                    while os.path.exists(dest_path):
                        base, ext = os.path.splitext(new_name)
                        dest_path = os.path.join(logs_folder, f"{base}_{counter}{ext}")
                        counter  += 1
                    shutil.move(entry.path, dest_path)
                    log.info(f"  CSV moved → {os.path.basename(dest_path)}")
        except PermissionError:
            log.warning("Permission error moving CSVs.")

    # Remove any remaining non-JPG files
    try:
        for entry in os.scandir(destination):
            if entry.is_file() and not entry.name.lower().endswith(".jpg"):
                os.remove(entry.path)
                log.info(f"  Removed non-JPG: {entry.name}")
    except PermissionError:
        log.warning("Permission error cleaning files.")

    log.info(f"  Set folder ready in Collected/ — run batch_zip.py to package for upload.")


# ─────────────────────────────────────────────────────────────────────────────
# SET SCRAPER
# ─────────────────────────────────────────────────────────────────────────────

def scrape_set(
    session: requests.Session,
    set_code: str,
    output_dir: str,
    delay_min: float,
    delay_max: float,
    required_positions: Optional[Set[int]] = None,
) -> int:
    """
    Download all cards for a set from the official API.
    Returns total failure count.
    """
    total_failures = 0
    position       = 1
    page           = 1

    csv_path   = os.path.join(output_dir, "download_log.csv")
    csv_file   = open(csv_path, "w", newline="", encoding="utf-8")
    fieldnames = ["position", "card_id", "card_name", "image_url", "local_filename", "status"]
    csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
    csv_writer.writeheader()

    try:
        while True:
            log.info(f"\n{'─' * 60}")
            log.info(f"  {set_code}  |  Page {page}")
            log.info(f"{'─' * 60}")

            data = fetch_set_page(session, set_code, page)
            if not data or not data.get("cardList"):
                log.error(f"  No data returned for page {page} — stopping.")
                break

            max_page  = data.get("maxPage", 1)
            card_list = data["cardList"]

            for card in card_list:
                card_id   = card.get("cardID", "")
                thumb     = card.get("cardThumbFile", "")
                card_name = card.get("cardNameAltText", "")
                img_url   = f"{BASE_URL}{thumb}" if thumb else ""
                filename  = f"{position:03d}.jpg"

                # CSV filter
                if required_positions is not None and position not in required_positions:
                    log.info(f"\n  Card #{position:03d} ({card_name}) — not in filter, skipping")
                    position += 1
                    continue

                log.info(f"\n  Card #{position:03d}  |  ID {card_id}  |  {card_name}")

                if not img_url:
                    log.error(f"  No image URL — skipping")
                    total_failures += 1
                    csv_writer.writerow({
                        "position":       f"{position:03d}",
                        "card_id":        card_id,
                        "card_name":      card_name,
                        "image_url":      "",
                        "local_filename": "FAILED",
                        "status":         "failed",
                    })
                    position += 1
                    continue

                success = download_image(
                    session, 
                    img_url, 
                    output_dir, 
                    filename,
                    delay_min, 
                    delay_max,
                )

                if not success:
                    total_failures += 1

                csv_writer.writerow({
                    "position":       f"{position:03d}",
                    "card_id":        card_id,
                    "card_name":      card_name,
                    "image_url":      img_url,
                    "local_filename": filename if success else "FAILED",
                    "status":         "ok" if success else "failed",
                })

                position += 1

            if page >= max_page:
                log.info(f"\nAll {max_page} page(s) complete.")
                break

            page += 1
            polite_delay(delay_min, delay_max)

    finally:
        csv_file.close()

    return total_failures


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    script_dir  = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    need_folder = find_japanese_need_folder(script_dir)

    if not need_folder:
        print("Could not find MissingImages/Japanese/Need/ folder.")
        return

    print("\nPokémon TCG Official Japanese Scraper  —  press Ctrl+C to quit.")
    print(f"Japanese Need folder: {need_folder}")

    # ── Get set codes to process ──────────────────────────────────────────────
    print("\n" + "─" * 60)
    print("Press Enter to auto-scan ALL folders in Japanese/Need/")
    print("Or paste specific set codes (one per line) then blank line:")

    user_codes = []
    while True:
        line = input("  Set code: ").strip().strip("'\"").upper()
        if not line:
            break
        user_codes.append(line)

    if user_codes:
        set_codes = user_codes
        print(f"\nProcessing {len(set_codes)} specified set(s).")
    else:
        set_codes = get_all_set_folders(need_folder)
        print(f"\nFound {len(set_codes)} folder(s) in Japanese/Need/ — checking each against API...")

    # ── Check each set against the API ───────────────────────────────────────
    session = create_session()

    valid_sets   = []
    skipped_sets = []

    for code in set_codes:
        hit_cnt = check_set_exists(session, code)
        if hit_cnt > 0:
            valid_sets.append((code, hit_cnt))
            log.info(f"  ✓ {code} — {hit_cnt} card(s) found")
        else:
            skipped_sets.append(code)
            log.info(f"  ✗ {code} — not on official site, skipping")
        polite_delay(DEFAULT_DELAY_MIN, DEFAULT_DELAY_MAX)

    print(f"\n{'─' * 60}")
    print(f"Valid sets to download : {len(valid_sets)}")
    print(f"Skipped (not on site)  : {len(skipped_sets)}")
    if skipped_sets:
        print(f"  Skipped: {', '.join(skipped_sets)}")

    if not valid_sets:
        print("\nNo valid sets to download. Exiting.")
        session.close()
        return

    print(f"\nStarting downloads...")

    # ── Process each valid set ────────────────────────────────────────────────
    for set_code, hit_cnt in valid_sets:
        output_dir = os.path.join(need_folder, set_code)

        if not os.path.isdir(output_dir):
            log.warning(f"\n[{set_code}] Folder not found — skipping.")
            continue

        print(f"\n{'═' * 60}")
        print(f"  SET: {set_code}  ({hit_cnt} cards)")
        print(f"{'═' * 60}")

        os.makedirs(output_dir, exist_ok=True)

        required_positions = load_required_positions(output_dir)
        if required_positions is None:
            log.info("No filter CSV — all cards will be downloaded.")

        total_failures = scrape_set(
            session, 
            set_code, 
            output_dir, 
            DEFAULT_DELAY_MIN,
            DEFAULT_DELAY_MAX,
            required_positions,
        )

        log.info(f"\nDone. Saved to: {os.path.abspath(output_dir)}")
        move_to_collected(output_dir, total_failures)

    session.close()
    print(f"\n{'═' * 60}")
    print("All sets processed. Goodbye!")


if __name__ == "__main__":
    main()
