#!/usr/bin/env python3
"""
scrape_pokellector_images.py
----------------------------
Downloads Japanese Pokémon TCG card images from jp.pokellector.com.
Also downloads the set logo as logo.png.

Site structure (inspected April 2026):
  - Sets list:  https://jp.pokellector.com/sets
  - Set page:   https://jp.pokellector.com/[Set-Name-Expansion]/
  - Card link:  /[Set-Name-Expansion]/[Pokemon-Name]-Card-[N]
  - Card image: https://den-cards.pokellector.com/[id]/[Name].[SetCode].[N].[id].png
  - Set logo:   https://den-media.pokellector.com/logos/[Set-Name].symbol.[id].png

All cards are listed on a single page — no pagination.
Every card requires visiting its detail page to retrieve the image URL.
The TCGdex set code is extracted from the first card image URL and used
to auto-detect the correct output folder under MissingImages/Japanese/.
"""

import csv
import glob
import os
import re
import shutil
import time
import random
import logging
from typing import Optional, List, Dict, Set
from urllib.parse import urljoin
from PIL import Image
from io import BytesIO

# Enables arrow keys and backspace inside input() prompts
try:
    import readline
except ImportError:
    pass  # Not available on Windows — input() still works fine

import requests
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────────────────────────────────────
# SELECTORS & PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

# Matches card detail links like /Wild-Force-Expansion/Roselia-Card-1
CARD_LINK_PATTERN = re.compile(r"-Card-(\d+)/?$")

# Selects the card image on a detail page
DETAIL_IMG_SELECTOR = 'img[src*="den-cards.pokellector.com"]'

# Selects the set logo on a set page
LOGO_IMG_SELECTOR = 'img[src*="den-media.pokellector.com/logos/"]'

# Extracts the TCGdex set code from a card image filename
# e.g. den-cards.pokellector.com/361/Cacnea.SV1S.1.46220.png → SV1S
SET_CODE_PATTERN = re.compile(r"\.([A-Za-z0-9]+)\.\d+\.\d+\.\w+$")


# ─────────────────────────────────────────────────────────────────────────────
# TIMING & RETRY SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_DELAY_MIN = 2.0   # seconds between downloads
DEFAULT_DELAY_MAX = 4.0
MAX_RETRIES       = 3
RETRY_BACKOFF     = 2.0   # seconds added per retry attempt


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION
# ─────────────────────────────────────────────────────────────────────────────

def create_session(referer: str = "https://jp.pokellector.com/") -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        "Referer":         referer,
    })
    return session


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def polite_delay(delay_min: float, delay_max: float) -> None:
    time.sleep(random.uniform(delay_min, delay_max))


def fetch_with_retry(
    session: requests.Session,
    url: str,
    stream: bool = False,
) -> Optional[requests.Response]:
    """Fetch a URL with retry logic. Returns the response or None on failure."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, stream=stream, timeout=30)
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
# PARSING
# ─────────────────────────────────────────────────────────────────────────────

def parse_card_links(html: str, base_url: str) -> List[Dict]:
    """
    Parse all card links from a set page.
    Returns a list of dicts sorted by card number:
      [{"card_number": 1, "detail_url": "https://..."}, ...]
    """
    soup  = BeautifulSoup(html, "html.parser")
    cards = []
    seen  = set()

    for a in soup.find_all("a", href=True):
        href  = a["href"]
        match = CARD_LINK_PATTERN.search(href)
        if match:
            card_number = int(match.group(1))
            detail_url  = urljoin(base_url, href)
            if detail_url not in seen:
                seen.add(detail_url)
                cards.append({"card_number": card_number, "detail_url": detail_url})

    cards.sort(key=lambda c: c["card_number"])
    return cards


def get_image_url_from_detail(session: requests.Session, detail_url: str) -> Optional[str]:
    """Visit a card detail page and return the card image URL."""
    resp = fetch_with_retry(session, detail_url)
    if not resp:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    img  = soup.select_one(DETAIL_IMG_SELECTOR)
    if img and img.get("src"):
        return img["src"]
    log.warning(f"    No card image found on: {detail_url}")
    return None


def get_logo_url(html: str) -> Optional[str]:
    """Extract the set logo URL from the set page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    img  = soup.select_one(LOGO_IMG_SELECTOR)
    return img["src"] if img and img.get("src") else None


def extract_set_code(img_url: str) -> Optional[str]:
    """
    Extract the TCGdex set code from a card image URL.
    e.g. '.../Cacnea.SV1S.1.46220.png' → 'SV1S'
    """
    match = SET_CODE_PATTERN.search(img_url)
    return match.group(1) if match else None


# ─────────────────────────────────────────────────────────────────────────────
# CSV FILTER
# ─────────────────────────────────────────────────────────────────────────────

def load_required_positions(output_dir: str) -> Optional[Set[int]]:
    """
    Look for a TCGdex CSV in the output folder and read its assetLocalId column.
    Returns a set of card numbers to download, or None to download everything.
    download_log.csv is ignored automatically.
    """
    csv_files = [
        f for f in glob.glob(os.path.join(output_dir, "*.csv"))
        if os.path.basename(f) != "download_log.csv"
    ]
    if not csv_files:
        return None

    if len(csv_files) > 1:
        print("\nMultiple CSV files found in the output folder:")
        for i, path in enumerate(csv_files, 1):
            print(f"  {i}. {os.path.basename(path)}")
        choice = input("Enter the number to use as a filter (or Enter for all): ").strip()
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
        log.warning("CSV found but no valid assetLocalId values — downloading all cards.")
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
    """Download PNG, convert to JPG, save. Returns True on success."""
    filepath = os.path.join(output_dir, filename)

    if os.path.exists(filepath):
        log.info(f"  Already exists — skipping  ({filename})")
        return True

    resp = fetch_with_retry(session, img_url, stream=True)
    if not resp:
        return False

    img = Image.open(BytesIO(resp.content)).convert("RGB")
    img.save(filepath, "JPEG", quality=95, optimize=True)

    file_size_kb = os.path.getsize(filepath) / 1024
    log.info(f"  Saved {filename}  ({file_size_kb:.1f} KB)  ← {img_url}")
    polite_delay(delay_min, delay_max)
    return True


# ─────────────────────────────────────────────────────────────────────────────
# FOLDER DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def find_output_folder(set_code: str, script_dir: str) -> Optional[str]:
    """
    Search for a folder named set_code inside any 'Need' subfolder under
    MissingImages/. Prefers the Japanese language folder automatically.
    Falls back to asking the user if multiple non-Japanese matches are found.
    """
    missing_images_dir = os.path.join(script_dir, "MissingImages")
    if not os.path.isdir(missing_images_dir):
        return None

    matches          = []
    japanese_match   = None

    for lang_entry in os.scandir(missing_images_dir):
        if not lang_entry.is_dir():
            continue
        try:
            for sub_entry in os.scandir(lang_entry.path):
                if sub_entry.is_dir() and sub_entry.name.strip().lower() == "need":
                    candidate = os.path.join(sub_entry.path, set_code)
                    if os.path.isdir(candidate):
                        matches.append(candidate)
                        if lang_entry.name.strip().lower() == "japanese":
                            japanese_match = candidate
        except PermissionError:
            continue

    if not matches:
        return None
    if japanese_match:
        log.info(f"Auto-selected Japanese folder.")
        return japanese_match
    if len(matches) == 1:
        return matches[0]

    print(f"\nFound '{set_code}' in multiple locations:")
    for i, path in enumerate(matches, 1):
        print(f"  {i}. {path}")
    choice = input("Enter the number of the folder to use: ").strip()
    try:
        return matches[int(choice) - 1]
    except (ValueError, IndexError):
        log.warning("Invalid choice — please enter the folder path manually.")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MOVE TO COLLECTED
# ─────────────────────────────────────────────────────────────────────────────

def move_to_collected(output_dir: str, total_failures: int) -> None:
    """
    Move the completed set folder to Collected/ and CSVs to the logs folder.
    Only fires if total_failures == 0.

    Also auto-creates 'Missing Reports and Collection Logs' and 'Uploaded'
    folders inside the language folder if they don't already exist.
    """
    if total_failures > 0:
        log.info(f"\n{total_failures} card(s) failed — folder left in Need for retry.")
        return

    need_folder = os.path.dirname(os.path.abspath(output_dir))
    lang_folder = os.path.dirname(need_folder)
    set_name    = os.path.basename(os.path.abspath(output_dir))

    # Auto-create Missing Reports folder if absent
    for auto_folder in ["Missing Reports and Collection Logs"]:
        exists = any(
            e.is_dir() and e.name.strip().lower() == auto_folder.lower()
            for e in os.scandir(lang_folder)
        )
        if not exists:
            os.makedirs(os.path.join(lang_folder, auto_folder), exist_ok=True)
            log.info(f"Created folder: {auto_folder}/")

    # Find and move to Collected
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

    # Move CSVs to logs folder
    logs_folder = None
    for entry in os.scandir(lang_folder):
        if entry.is_dir() and entry.name.strip().lower() == "missing reports and collection logs":
            logs_folder = entry.path
            break

    if logs_folder:
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

    # ── Remove any remaining non-PNG files ────────────────────────────────────
    try:
        for entry in os.scandir(destination):
            if entry.is_file() and not entry.name.lower().endswith(".jpg"):
                os.remove(entry.path)
                log.info(f"  Removed non-PNG: {entry.name}")
    except PermissionError:
        log.warning("Permission error cleaning non-PNG files.")

    log.info(f"  Set folder ready in Collected/ — run batch_zip.py to package for upload.")


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    print("\nPokémon TCG Pokellector Scraper  —  press Ctrl+C at any time to quit.")
    print("Paste set page URLs from jp.pokellector.com")
    print("e.g. https://jp.pokellector.com/Wild-Force-Expansion/")

    url_queue: List[str] = []

    while True:

        # ── Fill queue if empty ───────────────────────────────────────────────
        if not url_queue:
            print("\n" + "─" * 60)
            print("Paste one or more set URLs (one per line).")
            print("Press Enter on a blank line to start (or Enter immediately to quit):")
            while True:
                line = input("  URL: ").strip().strip("'\"")
                if not line:
                    break
                url_queue.append(line)
            if not url_queue:
                print("No URLs entered — goodbye!")
                break

        url = url_queue.pop(0)
        if url_queue:
            print(f"  ({len(url_queue)} more URL(s) queued after this one)")

        # ── Fetch set page ────────────────────────────────────────────────────
        session = create_session(referer=url)
        log.info(f"\nFetching set page: {url}")
        resp = fetch_with_retry(session, url)
        if not resp:
            log.error("Could not fetch set page — skipping.")
            session.close()
            continue

        html  = resp.text
        cards = parse_card_links(html, url)
        log.info(f"Found {len(cards)} card(s).")

        if not cards:
            log.error("No card links found — check the URL and try again.")
            session.close()
            continue

        # ── Detect set code from first card's detail page ─────────────────────
        log.info("Detecting set code from first card...")
        polite_delay(1.0, 2.0)
        first_img_url = get_image_url_from_detail(session, cards[0]["detail_url"])
        set_code      = extract_set_code(first_img_url) if first_img_url else None

        if set_code:
            log.info(f"Detected set code: {set_code}")
        else:
            log.warning("Could not detect set code automatically.")

        # ── Find output folder ────────────────────────────────────────────────
        output_dir = None
        if set_code:
            output_dir = find_output_folder(set_code, script_dir)
            if output_dir:
                log.info(f"Output folder: {output_dir}")
            else:
                log.info(f"No existing folder found for '{set_code}'.")

        if not output_dir:
            print("Paste the output folder path and press Enter:")
            output_dir = input("  Folder: ").strip().strip("'\"")
        if not output_dir:
            log.error("No output folder specified — skipping.")
            session.close()
            continue

        os.makedirs(output_dir, exist_ok=True)

        # ── CSV filter ────────────────────────────────────────────────────────
        required_positions = load_required_positions(output_dir)
        if required_positions is None:
            log.info("No filter CSV found — all cards will be downloaded.")

        # ── Download set logo ─────────────────────────────────────────────────
        logo_url = get_logo_url(html)
        if logo_url:
            log.info(f"\nDownloading set logo...")
            download_image(session, logo_url, output_dir, "logo.jpg", 1.0, 2.0)
        else:
            log.warning("No set logo found on this page.")

        # ── CSV log ───────────────────────────────────────────────────────────
        csv_path   = os.path.join(output_dir, "download_log.csv")
        csv_file   = open(csv_path, "w", newline="", encoding="utf-8")
        fieldnames = ["card_number", "detail_url", "image_url", "local_filename", "status"]
        csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        csv_writer.writeheader()

        # ── Download all cards ────────────────────────────────────────────────
        total_failures = 0

        try:
            for card in cards:
                card_number = card["card_number"]
                detail_url  = card["detail_url"]
                filename    = f"{card_number:03d}.jpg"

                # CSV filter: skip cards not in our list
                if required_positions is not None and card_number not in required_positions:
                    log.info(f"\n  Card #{card_number:03d} — not in filter list, skipping")
                    continue

                # Skip if already downloaded
                if os.path.exists(os.path.join(output_dir, filename)):
                    log.info(f"\n  Card #{card_number:03d} — already exists, skipping")
                    csv_writer.writerow({
                        "card_number":    card_number,
                        "detail_url":     detail_url,
                        "image_url":      "",
                        "local_filename": filename,
                        "status":         "skipped",
                    })
                    continue

                log.info(f"\n  Card #{card_number:03d}  |  {detail_url}")

                # Visit detail page to get image URL
                # (Skip for card #1 if we already visited it for set code detection
                #  and it succeeded — reuse that URL)
                if card_number == cards[0]["card_number"] and first_img_url:
                    img_url = first_img_url
                else:
                    polite_delay(1.0, 2.0)
                    img_url = get_image_url_from_detail(session, detail_url)

                if not img_url:
                    log.error(f"  No image URL found — skipping card #{card_number}")
                    total_failures += 1
                    csv_writer.writerow({
                        "card_number":    card_number,
                        "detail_url":     detail_url,
                        "image_url":      "",
                        "local_filename": "FAILED",
                        "status":         "failed",
                    })
                    continue

                log.info(f"    Image: {img_url}")
                success = download_image(
                    session, img_url, output_dir, filename,
                    DEFAULT_DELAY_MIN, DEFAULT_DELAY_MAX
                )

                if not success:
                    total_failures += 1

                csv_writer.writerow({
                    "card_number":    card_number,
                    "detail_url":     detail_url,
                    "image_url":      img_url or "",
                    "local_filename": filename if success else "FAILED",
                    "status":         "ok" if success else "failed",
                })

        finally:
            csv_file.close()
            session.close()

        log.info(f"\nAll done. Images saved to: {os.path.abspath(output_dir)}")

        # ── Move to Collected if fully successful ─────────────────────────────
        move_to_collected(output_dir, total_failures)


if __name__ == "__main__":
    main()
