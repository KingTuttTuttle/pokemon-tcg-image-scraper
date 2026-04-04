#!/usr/bin/env python3
"""
scrape_pokemon_images.py
------------------------
Downloads Pokémon TCG card images from asia.pokemon-card.com.

Confirmed site behaviour (inspected April 2026):
  - List page renders card links as plain <a> tags in static HTML
    (no images; they are injected by JavaScript at render time).
  - Each link follows the pattern: /{region}/card-search/detail/{id}/
  - Card image URL is directly derivable (prefix depends on region):
      hk-en → .../card-img/default{id:08d}.png
      hk    → .../card-img/hk{id:08d}.png
      tw    → .../card-img/tw{id:08d}.png
  - If that constructed URL returns an error the script falls back to
    visiting the detail page and scraping the real <img src>.
  - Pagination uses the query parameter ?pageNo=N.

Usage:
  python scrape_pokemon_images.py --url "https://asia.pokemon-card.com/hk-en/card-search/list/?expansionCodes=ME01" --output "./ME01"
  python scrape_pokemon_images.py --url "..." --output "./out" --delay-min 1.5 --delay-max 3.5 --no-autopaginate

See the USAGE GUIDE at the bottom of this file for full instructions.
"""

import argparse
import csv
import glob
import os
import re
import shutil
import time
import random
import logging
from typing import Optional, Tuple, List, Dict
from urllib.parse import urljoin, urlparse, parse_qs

# Enables arrow keys, backspace, and cursor movement inside input() prompts
try:
    import readline
except ImportError:
    pass  # readline is unavailable on Windows — input() still works, just without arrow keys

import requests
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────────────────────────────────────
# SITE SELECTORS & PATTERNS
# If the site changes its HTML structure, update the constants in this block.
# ─────────────────────────────────────────────────────────────────────────────

# Selects every anchor that links to a card detail page on the list page.
CARD_LINK_SELECTOR = 'a[href*="/card-search/detail/"]'

# Captures the numeric card ID from a detail URL like /hk-en/card-search/detail/21593/
CARD_ID_PATTERN = re.compile(r"/card-search/detail/(\d+)/")

# Selects the main card image on a detail page (fallback only).
DETAIL_IMG_SELECTOR = 'img[src*="/card-img/"]'

# Maps each region to the filename prefix used in its card image URLs.
# e.g. hk  → hk00018890.png
#      tw  → tw00018421.png
#      hk-en → default00021593.png
# If you encounter a new region not listed here, the region name itself
# is used as a fallback prefix (which is correct for most regions).
REGION_IMG_PREFIX: Dict[str, str] = {
    "hk-en": "default",
    "hk":    "hk",
    "tw":    "tw",
}

# Maps each URL region code to its matching MissingImages language folder name.
# Used to auto-select the correct folder when the same set ID exists in
# multiple language folders (e.g. SV4M appears in both Thai and Chin (t)).
# Folder name comparison is case-insensitive and ignores trailing spaces.
REGION_LANG_FOLDER: Dict[str, str] = {
    "hk":    "Chin (t)",
    "hk-en": "English",
    "tw":    "Chinese (simplified)",
    "th":    "Thai",
    "id":    "Indonesian",
    "sg":    "English",
    "my":    "English",
    "ph":    "English",
}

# ─────────────────────────────────────────────────────────────────────────────
# TIMING & RETRY SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_DELAY_MIN = 2.0   # seconds (minimum pause between downloads)
DEFAULT_DELAY_MAX = 4.0   # seconds (maximum pause between downloads)
MAX_RETRIES = 3            # number of attempts before giving up on a URL
RETRY_BACKOFF = 2.0        # seconds; doubles after each failed attempt
REQUEST_TIMEOUT = 20       # seconds to wait for a server response

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING  (prints timestamp + level + message to the terminal)
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# SESSION SETUP
# ─────────────────────────────────────────────────────────────────────────────

def create_session(referer: str) -> requests.Session:
    """
    Return a requests.Session pre-loaded with browser-like headers.
    Using a session means cookies and keep-alive connections are reused
    across all requests, which looks more like a real browser.
    """
    session = requests.Session()
    session.headers.update({
        # Pretend to be Chrome on macOS — one of the most common user agents.
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        # Tell the server we accept normal web content.
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        # Referer makes it look like we arrived from the list page.
        "Referer": referer,
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    })
    return session


# ─────────────────────────────────────────────────────────────────────────────
# POLITE DELAY
# ─────────────────────────────────────────────────────────────────────────────

def polite_delay(min_s: float = DEFAULT_DELAY_MIN, max_s: float = DEFAULT_DELAY_MAX) -> None:
    """
    Sleep for a random duration between min_s and max_s seconds.
    The randomness makes the request timing look more human.
    """
    delay = random.uniform(min_s, max_s)
    log.info(f"    ⏱  Waiting {delay:.1f}s...")
    time.sleep(delay)


# ─────────────────────────────────────────────────────────────────────────────
# HTTP FETCH WITH RETRY
# ─────────────────────────────────────────────────────────────────────────────

def fetch_with_retry(
    session: requests.Session,
    url: str,
    **kwargs,
) -> Optional[requests.Response]:
    """
    GET a URL up to MAX_RETRIES times, with exponential back-off between
    attempts.  Returns the Response on success, or None if all retries fail.

    Any extra keyword arguments (e.g. stream=True) are forwarded to session.get().
    """
    backoff = RETRY_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
            resp.raise_for_status()   # raises for 4xx / 5xx status codes
            return resp
        except requests.RequestException as exc:
            log.warning(f"    Attempt {attempt}/{MAX_RETRIES} failed for {url!r}: {exc}")
            if attempt < MAX_RETRIES:
                log.info(f"    Retrying in {backoff:.0f}s...")
                time.sleep(backoff)
                backoff *= 2   # double the wait before the next retry
    log.error(f"    Permanently failed: {url!r}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# URL HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def extract_region(url: str) -> str:
    """
    Pull the region segment out of a URL path.
    e.g. 'https://asia.pokemon-card.com/hk-en/card-search/list/' → 'hk-en'
    """
    path = urlparse(url).path          # e.g. '/hk-en/card-search/list/'
    parts = path.strip("/").split("/") # ['hk-en', 'card-search', 'list']
    return parts[0] if parts else ""


def build_image_url(base_url: str, region: str, card_id: int) -> str:
    """
    Construct the direct image URL from a card's numeric ID.

    Confirmed prefixes (April 2026):
        hk-en  →  default00021593.png
        hk     →  hk00018890.png
        tw     →  tw00018421.png
    Unknown regions fall back to using the region name as the prefix.
    The ID is always zero-padded to 8 digits.
    """
    prefix = REGION_IMG_PREFIX.get(region, region)
    parsed = urlparse(base_url)
    return f"{parsed.scheme}://{parsed.netloc}/{region}/card-img/{prefix}{card_id:08d}.png"


# ─────────────────────────────────────────────────────────────────────────────
# PAGE PARSERS
# ─────────────────────────────────────────────────────────────────────────────

def parse_card_links(html: str, base_url: str) -> List[Dict]:
    """
    Parse a list-page HTML string and return every unique card as a dict:
        {"card_id": 21593, "detail_url": "https://..."}

    Cards are returned in document order (top-left → bottom-right on the page).
    Duplicate IDs are silently dropped (shouldn't happen, but just in case).
    """
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select(CARD_LINK_SELECTOR)

    cards: List[Dict] = []
    seen_ids: set = set()

    for anchor in anchors:
        href = anchor.get("href", "")
        match = CARD_ID_PATTERN.search(href)
        if not match:
            continue

        card_id = int(match.group(1))
        if card_id in seen_ids:
            continue
        seen_ids.add(card_id)

        cards.append({
            "card_id": card_id,
            "detail_url": urljoin(base_url, href),
        })

    return cards


def find_next_page_url(html: str, current_url: str) -> Optional[str]:
    """
    Look for a pagination link pointing to the next page.

    Strategy: find the current pageNo in the query string (default 1),
    then search for an <a> tag whose href contains pageNo={current+1}.
    Returns the absolute URL of that link, or None if we're on the last page.
    """
    soup = BeautifulSoup(html, "html.parser")

    # Determine which page we are currently on.
    parsed = urlparse(current_url)
    params = parse_qs(parsed.query)
    current_page = int(params.get("pageNo", ["1"])[0])
    next_page_number = current_page + 1

    # Look for a link that explicitly references the next page number.
    pattern = re.compile(rf"pageNo={next_page_number}(&|$)")
    for anchor in soup.find_all("a", href=True):
        if pattern.search(anchor["href"]):
            return urljoin(current_url, anchor["href"])

    return None   # no next-page link found → we're on the last page


# ─────────────────────────────────────────────────────────────────────────────
# DETAIL-PAGE FALLBACK
# ─────────────────────────────────────────────────────────────────────────────

def get_image_url_from_detail(
    session: requests.Session,
    detail_url: str,
) -> Optional[str]:
    """
    Visit a card's detail page and scrape the image URL from its <img> tag.
    This is the fallback path used when the directly-constructed URL fails.
    """
    resp = fetch_with_retry(session, detail_url)
    if not resp:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    img_tag = soup.select_one(DETAIL_IMG_SELECTOR)
    if img_tag and img_tag.get("src"):
        # Make the URL absolute in case the site uses a relative path.
        return urljoin(detail_url, img_tag["src"])

    log.warning(f"    No card image found on detail page: {detail_url!r}")
    return None


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────

def download_image(
    session: requests.Session,
    img_url: str,
    output_dir: str,
    position: int,
    delay_min: float,
    delay_max: float,
) -> Optional[str]:
    """
    Download one image and save it to output_dir.

    File is named by position: 001.png, 002.png, …
    If the file already exists it is skipped (no overwrite).
    Returns the filename on success, or None on failure.
    A polite delay is applied AFTER each successful download.
    """
    # Derive file extension from the URL (default .png if nothing found).
    url_path = img_url.split("?")[0]
    ext = os.path.splitext(url_path)[-1].lower() or ".png"
    filename = f"{position:03d}{ext}"
    filepath = os.path.join(output_dir, filename)

    # Skip if already downloaded in a previous run.
    if os.path.exists(filepath):
        log.info(f"  [{position:03d}] Already exists — skipping  ({filename})")
        return filename

    # Download the image in streaming mode so we don't load it all into RAM.
    resp = fetch_with_retry(session, img_url, stream=True)
    if not resp:
        return None

    with open(filepath, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            fh.write(chunk)

    file_size_kb = os.path.getsize(filepath) / 1024
    log.info(f"  [{position:03d}] Saved {filename}  ({file_size_kb:.1f} KB)  ← {img_url}")

    # Wait before the next request (polite behaviour).
    polite_delay(delay_min, delay_max)
    return filename


# ─────────────────────────────────────────────────────────────────────────────
# CSV FILTER LOADER
# ─────────────────────────────────────────────────────────────────────────────

def load_required_positions(output_dir: str) -> Optional[set]:
    """
    Look for a CSV file in the output folder and read its assetLocalId column.
    Returns a set of integers representing which card positions to download,
    or None if no CSV is found (meaning: download everything).

    The download_log.csv written by this script is ignored automatically.
    If multiple CSVs are found the user is asked to confirm which one to use.
    """
    csv_files = [
        f for f in glob.glob(os.path.join(output_dir, "*.csv"))
        if os.path.basename(f) != "download_log.csv"
    ]

    if not csv_files:
        return None  # no filter CSV found — download all cards

    # If there's more than one CSV, list them and ask which to use
    if len(csv_files) > 1:
        print("\nMultiple CSV files found in the output folder:")
        for i, path in enumerate(csv_files, 1):
            print(f"  {i}. {os.path.basename(path)}")
        choice = input("Enter the number of the CSV to use as a filter (or press Enter to download all): ").strip()
        if not choice:
            return None
        try:
            csv_path = csv_files[int(choice) - 1]
        except (ValueError, IndexError):
            log.warning("Invalid choice — downloading all cards.")
            return None
    else:
        csv_path = csv_files[0]

    # Read the assetLocalId column
    positions = set()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            local_id = row.get("assetLocalId", "").strip()
            if local_id:
                try:
                    positions.add(int(local_id))
                except ValueError:
                    pass  # skip any non-numeric values

    if not positions:
        log.warning(f"CSV found but no valid assetLocalId values — downloading all cards.")
        return None

    log.info(f"Filter CSV: {os.path.basename(csv_path)}")
    log.info(f"Will download {len(positions)} specific card(s): "
             f"{sorted(positions)[:5]}{'…' if len(positions) > 5 else ''}")
    return positions


# ─────────────────────────────────────────────────────────────────────────────
# SINGLE-PAGE ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

def scrape_page(
    session: requests.Session,
    url: str,
    output_dir: str,
    delay_min: float,
    delay_max: float,
    csv_writer,
    start_position: int = 1,
    required_positions: Optional[set] = None,
) -> Tuple[int, Optional[str], int]:
    """
    Scrape one list page:
      1. Fetch the page HTML.
      2. Parse all card links.
      3. For each card: if required_positions is set, skip cards not in it.
         Otherwise: build image URL → download → fallback to detail page if needed.
      4. Write a row to the CSV log.
      5. Find and return the next page URL.

    Returns (next_start_position, next_page_url, failure_count).
    next_page_url is None when we have reached the last page.
    failure_count is the number of cards that could not be downloaded.

    NUMBERING RULE: position always advances by 1 for every card slot,
    whether the download succeeds, fails, or is filtered out.
    This keeps every card's number tied to its position in the set.
    """
    log.info(f"\nFetching list page: {url}")
    resp = fetch_with_retry(session, url)
    if not resp:
        log.error("Could not fetch the list page. Stopping.")
        return start_position, None, 1  # counts as a failure

    html = resp.text
    region = extract_region(url)
    cards = parse_card_links(html, url)
    log.info(f"Found {len(cards)} card(s) on this page.")

    position = start_position
    failures = 0

    for card in cards:
        card_id = card["card_id"]
        detail_url = card["detail_url"]

        # ── CSV filter: skip this card if it's not in our needed list ─────────
        if required_positions is not None and position not in required_positions:
            log.info(f"\n  Card slot #{position:03d}  |  not in filter list — skipping")
            position += 1
            continue

        # ── Fast path: construct the image URL directly from the ID ──────────
        img_url = build_image_url(url, region, card_id)
        log.info(f"\n  Card slot #{position:03d}  |  site ID {card_id}")
        log.info(f"    Trying direct URL: {img_url}")

        filename = download_image(
            session, img_url, output_dir, position, delay_min, delay_max
        )

        # ── Slow fallback: visit the detail page to find the real image URL ──
        if filename is None:
            log.warning(f"    Direct URL failed. Fetching detail page…")
            polite_delay(delay_min, delay_max)
            img_url_fallback = get_image_url_from_detail(session, detail_url)

            if img_url_fallback:
                img_url = img_url_fallback
                log.info(f"    Fallback image URL: {img_url}")
                filename = download_image(
                    session, img_url, output_dir, position, delay_min, delay_max
                )

        # ── Count failures ────────────────────────────────────────────────────
        if filename is None:
            failures += 1

        # ── Log result to CSV (one row per card slot) ────────────────────────
        if csv_writer:
            csv_writer.writerow({
                "position":       f"{position:03d}",
                "card_id":        card_id,
                "page_url":       url,
                "image_url":      img_url,
                "local_filename": filename or "FAILED",
                "status":         "ok" if filename else "failed",
            })

        # Position advances regardless of success — preserves numbering gaps.
        position += 1

    next_page_url = find_next_page_url(html, url)
    return position, next_page_url, failures


# ─────────────────────────────────────────────────────────────────────────────
# AUTO FOLDER DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_set_id(url: str) -> Optional[str]:
    """
    Pull the set ID out of a URL's expansionCodes parameter.
    e.g. '...?expansionCodes=M4' → 'M4'
    Returns None if the parameter isn't present.
    """
    params = parse_qs(urlparse(url).query)
    codes = params.get("expansionCodes", [])
    return codes[0].strip() if codes else None


def find_output_folder(set_id: str, script_dir: str, region: Optional[str] = None) -> Optional[str]:
    """
    Search for a folder named set_id inside any 'Need' subfolder under
    the MissingImages directory that sits next to this script.

    Example match:
        MissingImages/Chin (t) /Need /M4   ← returned if set_id is 'M4'

    If a region code is provided (e.g. 'th', 'tw', 'id'), it is used to
    automatically select the correct language folder when the same set ID
    exists in multiple languages — no prompt needed.

    Returns the full path if exactly one match is found.
    If multiple matches are found and the region resolves to a unique folder,
    that folder is selected automatically.
    If no match is found, returns None so the user can type the path manually.
    """
    missing_images_dir = os.path.join(script_dir, "MissingImages")
    if not os.path.isdir(missing_images_dir):
        return None

    matches = []

    # Walk one level at a time: language → Need → set folder
    for lang_entry in os.scandir(missing_images_dir):
        if not lang_entry.is_dir():
            continue
        try:
            for sub_entry in os.scandir(lang_entry.path):
                # Match any folder whose stripped name is "need" (case-insensitive)
                if sub_entry.is_dir() and sub_entry.name.strip().lower() == "need":
                    candidate = os.path.join(sub_entry.path, set_id)
                    if os.path.isdir(candidate):
                        matches.append(candidate)
        except PermissionError:
            continue

    if not matches:
        return None

    if len(matches) == 1:
        return matches[0]

    # Multiple matches — try to auto-select using the region code
    if region:
        target = REGION_LANG_FOLDER.get(region, "").strip().lower()
        if target:
            auto = [
                m for m in matches
                if os.path.basename(os.path.dirname(os.path.dirname(m))).strip().lower() == target
            ]
            if len(auto) == 1:
                log.info(f"Auto-selected '{os.path.basename(os.path.dirname(os.path.dirname(auto[0])))}'"
                         f" folder for region '{region}'.")
                return auto[0]

    # Could not auto-select — ask the user to pick
    print(f"\nFound '{set_id}' in multiple locations:")
    for i, path in enumerate(matches, 1):
        print(f"  {i}. {path}")
    choice = input("Enter the number of the folder to use: ").strip()
    try:
        return matches[int(choice) - 1]
    except (ValueError, IndexError):
        log.warning("Invalid choice — please paste the folder path manually.")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Pokémon TCG card images from asia.pokemon-card.com"
    )
    parser.add_argument(
        "--url", default=None,
        help='Card-search list page URL, e.g. "https://asia.pokemon-card.com/hk-en/card-search/list/?expansionCodes=ME01". If omitted you will be prompted interactively.',
    )
    parser.add_argument(
        "--output",
        help="Folder to save images into (created automatically if it doesn't exist). "
             "If omitted you will be prompted interactively.",
    )
    parser.add_argument(
        "--delay-min", type=float, default=DEFAULT_DELAY_MIN,
        help=f"Minimum seconds to wait between image downloads (default: {DEFAULT_DELAY_MIN})",
    )
    parser.add_argument(
        "--delay-max", type=float, default=DEFAULT_DELAY_MAX,
        help=f"Maximum seconds to wait between image downloads (default: {DEFAULT_DELAY_MAX})",
    )
    parser.add_argument(
        "--no-autopaginate", action="store_true",
        help="Only scrape the single URL provided; do not follow pagination.",
    )
    parser.add_argument(
        "--no-csv", action="store_true",
        help="Skip writing the download_log.csv file.",
    )
    args = parser.parse_args()

    # ── Validate delay range (once, before the loop) ──────────────────────────
    if args.delay_min > args.delay_max:
        log.error("--delay-min must be ≤ --delay-max. Exiting.")
        return

    script_dir = os.path.dirname(os.path.abspath(__file__))

    print("\nPokémon TCG Image Scraper  —  press Ctrl+C at any time to quit.")

    url_queue: List[str] = []  # holds URLs entered in bulk

    # ── Outer loop: keep processing URLs until none are left ──────────────────
    while True:

        # ── URL ───────────────────────────────────────────────────────────────
        url = args.url  # use CLI arg on first pass if provided, then None
        args.url = None  # clear so subsequent loops always prompt

        if not url:
            if not url_queue:
                # Queue is empty — ask the user for one or more URLs
                print("\n" + "─" * 60)
                print("Paste one or more URLs (one per line).")
                print("Press Enter on a blank line to start scraping")
                print("(or press Enter immediately to quit):")
                while True:
                    line = input("  URL: ").strip().strip("'\"")
                    if not line:
                        break
                    url_queue.append(line)

            if not url_queue:
                print("No URLs entered — goodbye!")
                break

            url = url_queue.pop(0)
            if len(url_queue) > 0:
                print(f"  ({len(url_queue)} more URL(s) queued after this one)")

        # ── Output folder ─────────────────────────────────────────────────────
        output_dir = args.output
        args.output = None  # clear so subsequent loops always auto-detect
        if not output_dir:
            set_id = extract_set_id(url)
            if set_id:
                output_dir = find_output_folder(set_id, script_dir, region=extract_region(url))
                if output_dir:
                    log.info(f"Auto-detected output folder: {output_dir}")
                else:
                    log.info(f"No existing folder found for '{set_id}' — please enter path manually.")

        if not output_dir:
            print("Paste the output folder path and press Enter:")
            output_dir = input("  Folder: ").strip().strip("'\"")
        if not output_dir:
            log.error("No output folder specified — skipping this URL.")
            continue

        os.makedirs(output_dir, exist_ok=True)
        log.info(f"Output folder: {os.path.abspath(output_dir)}")

        # ── CSV filter ────────────────────────────────────────────────────────
        required_positions = load_required_positions(output_dir)
        if required_positions is None:
            log.info("No filter CSV found — all cards will be downloaded.")

        # ── Session (fresh per scrape so headers/cookies are clean) ──────────
        session = create_session(referer=url)

        # ── CSV log ───────────────────────────────────────────────────────────
        csv_file = None
        csv_writer = None
        if not args.no_csv:
            csv_path = os.path.join(output_dir, "download_log.csv")
            csv_file = open(csv_path, "w", newline="", encoding="utf-8")
            fieldnames = ["position", "card_id", "page_url", "image_url", "local_filename", "status"]
            csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            csv_writer.writeheader()
            log.info(f"CSV log: {csv_path}")

        # ── Scrape all pages for this set ─────────────────────────────────────
        try:
            current_url = url
            position = 1
            page_num = 1
            total_failures = 0

            while current_url:
                log.info(f"\n{'─' * 60}")
                log.info(f"  PAGE {page_num}  |  starting at card #{position:03d}")
                log.info(f"{'─' * 60}")

                position, next_url, page_failures = scrape_page(
                    session=session,
                    url=current_url,
                    output_dir=output_dir,
                    delay_min=args.delay_min,
                    delay_max=args.delay_max,
                    csv_writer=csv_writer,
                    start_position=position,
                    required_positions=required_positions,
                )

                total_failures += page_failures

                if args.no_autopaginate or not next_url:
                    if not next_url:
                        log.info("\nNo further pages found — scrape complete.")
                    break

                log.info(f"\nMoving to page {page_num + 1}: {next_url}")
                polite_delay(args.delay_min, args.delay_max)
                current_url = next_url
                page_num += 1

        finally:
            if csv_file:
                csv_file.close()
            session.close()

        log.info(f"\nAll done. Images saved to: {os.path.abspath(output_dir)}")

        # ── Move to Collected if fully successful ─────────────────────────────
        move_to_collected(output_dir, total_failures)


def move_to_collected(output_dir: str, total_failures: int) -> None:
    """
    After scraping finishes, automatically move the set folder into the
    sibling 'Collected' folder — but ONLY if every card downloaded successfully
    (total_failures == 0).

    Folder structure this expects:
        Language/
          Need/
            SC2b/   ← output_dir (this is what gets moved)
          Collected/  ← destination
    """
    # If anything failed, leave the folder in Need so it's easy to retry
    if total_failures > 0:
        log.info(f"\n{total_failures} card(s) failed — folder left in Need for retry.")
        return

    # output_dir  = .../Need /SC2b
    # need_folder = .../Need /
    # lang_folder = .../Chin (t) /
    need_folder = os.path.dirname(os.path.abspath(output_dir))
    lang_folder = os.path.dirname(need_folder)
    set_name    = os.path.basename(os.path.abspath(output_dir))

    # Look for a sibling folder whose name matches "collected" (case-insensitive,
    # ignoring any trailing spaces in the folder name on disk)
    collected_folder = None
    try:
        for entry in os.scandir(lang_folder):
            if entry.is_dir() and entry.name.strip().lower() == "collected":
                collected_folder = entry.path
                break
    except PermissionError:
        log.warning("Permission error reading the language folder — skipping move.")
        return

    if not collected_folder:
        log.info("No 'Collected' folder found next to the 'Need' folder — skipping move.")
        return

    destination = os.path.join(collected_folder, set_name)

    if os.path.exists(destination):
        log.warning(f"'{set_name}' already exists in Collected — folder left in place.")
        return

    shutil.move(str(os.path.abspath(output_dir)), destination)
    log.info(f"All cards successful — moved '{set_name}' → Collected/ ✓")

    # ── Ensure Missing Reports and Collection Logs + Uploaded folders exist ──
    for auto_folder in ["Missing Reports and Collection Logs", "Uploaded"]:
        existing = None
        for entry in os.scandir(lang_folder):
            if entry.is_dir() and entry.name.strip().lower() == auto_folder.lower():
                existing = entry.path
                break
        if not existing:
            new_path = os.path.join(lang_folder, auto_folder)
            os.makedirs(new_path, exist_ok=True)
            log.info(f"Created folder: {auto_folder}/")

    # ── Move CSVs out of the collected set folder into the logs folder ────────
    logs_folder = None
    logs_folder_name = "Missing Reports and Collection Logs"
    for entry in os.scandir(lang_folder):
        if entry.is_dir() and entry.name.strip().lower() == logs_folder_name.lower():
            logs_folder = entry.path
            break

    if not logs_folder:
        log.info(f"No '{logs_folder_name}' folder found — CSVs left inside Collected/{set_name}.")
        return

    try:
        for entry in os.scandir(destination):
            if entry.is_file() and entry.name.lower().endswith(".csv"):
                new_name = f"{set_name}_{entry.name}"
                dest_path = os.path.join(logs_folder, new_name)
                counter = 1
                while os.path.exists(dest_path):
                    base, ext = os.path.splitext(new_name)
                    dest_path = os.path.join(logs_folder, f"{base}_{counter}{ext}")
                    counter += 1
                shutil.move(entry.path, dest_path)
                log.info(f"  CSV moved → {logs_folder_name}/{os.path.basename(dest_path)}")
    except PermissionError:
        log.warning("Permission error moving CSVs to logs folder — CSVs left in place.")


if __name__ == "__main__":
    main()


# =============================================================================
# USAGE GUIDE  —  Read this before using the script for the first time
# =============================================================================
#
#
# ── RECOMMENDED FOLDER STRUCTURE ────────────────────────────────────────────
#
#   This script is designed to work with the following folder layout.
#   Setting it up correctly means the script can find everything automatically
#   — you will never need to type a folder path manually.
#
#   Missing Images CSVs/               ← put the script in here
#     scrape_pokemon_images.py
#     create_set_folders.py
#     MissingImages/
#       English/
#         Need/                        ← CSVs and set folders go here
#         Collected/                   ← completed sets are moved here automatically
#         Blocked/                     ← sets you cannot currently obtain
#       Chin (t)/
#         Need/
#         Collected/
#         Blocked/
#       Korean/
#         Need/
#         Collected/
#         Blocked/
#       (one folder per language, named however you like)
#
#   IMPORTANT: The script looks for a folder called exactly "MissingImages"
#   sitting in the same folder as the script itself.  If you name it something
#   else, open this script and update this line near the top of
#   find_output_folder():
#
#       missing_images_dir = os.path.join(script_dir, "MissingImages")
#
#   Change "MissingImages" to whatever you named your folder.
#
#
# ── SETTING UP A NEW LANGUAGE FOLDER ────────────────────────────────────────
#
#   For each language you want to work with:
#
#   1. Create a folder inside MissingImages/ named after the language.
#      e.g.  MissingImages/English/
#
#   2. Inside that, create three subfolders:
#        Need/       — where you will put your CSV files and download images
#        Collected/  — where completed sets are moved automatically
#        Blocked/    — for sets you cannot currently obtain (manual use)
#
#   3. Drop your missing-image CSV files into the Need/ folder.
#      Each CSV must have at minimum these two columns:
#        setId        — the set's ID code (e.g. M4, SC2b, SV1)
#        assetLocalId — the card's number within the set (e.g. 001, 002)
#
#   4. Run create_set_folders.py to automatically create one subfolder per
#      set ID and move each CSV into its matching folder:
#
#        cd "/path/to/Missing Images CSVs"
#        python3 create_set_folders.py
#
#      After running it your Need/ folder will look like:
#        Need/
#          M4/
#            missing-images-xxxxxxx.csv
#          SC2b/
#            missing-images-xxxxxxx.csv
#          SV1/
#            missing-images-xxxxxxx.csv
#
#   5. You are now ready to run the scraper.  It will automatically find the
#      right folder, read the CSV, and move the set to Collected/ when done.
#
#
# ── STEP 1: INSTALL PYTHON ───────────────────────────────────────────────────
#
#   This script requires Python 3.  To check if it is already installed,
#   open a terminal and type:
#
#       python3 --version
#
#   You should see something like "Python 3.11.2".
#   If you see an error, download Python from https://www.python.org/downloads/
#
#   HOW TO OPEN A TERMINAL:
#     Mac:     Press Command + Space, type "Terminal", press Enter.
#     Windows: Press the Windows key, type "PowerShell", press Enter.
#
#
# ── STEP 2: INSTALL THE TWO REQUIRED LIBRARIES (one-time only) ──────────────
#
#   In the terminal, type this and press Enter:
#
#       pip3 install requests beautifulsoup4
#
#   You will see text scroll by — that is normal.
#   You only need to do this once, ever.
#
#
# ── STEP 3: NAVIGATE TO THE SCRIPT'S FOLDER ─────────────────────────────────
#
#   The script must be run from the folder it lives in.
#   In the terminal, type cd followed by the path to that folder.
#   Example (Mac):
#
#       cd "/Users/yourname/Desktop/Missing Images CSVs"
#
#   TIP: If your folder path has spaces in it, wrap the whole path in
#   double quotes, exactly as shown above.
#
#
# ── STEP 4: GET THE URL FOR THE SET YOU WANT ────────────────────────────────
#
#   The script needs a URL that ends with ?expansionCodes=XXXX
#   You get this by:
#     1. Going to  https://asia.pokemon-card.com/hk/card-search/
#        (change "hk" to your region if needed, e.g. "tw" or "hk-en")
#     2. Browsing to the set you want and clicking it.
#     3. Copying the URL from your browser address bar.
#        It will look like:
#        https://asia.pokemon-card.com/hk/card-search/list/?expansionCodes=M4
#
#   DO NOT use the URL from the filter dropdown on the list page —
#   that one stays the same no matter which set you pick and will not work.
#   Always click through from the card-search index page to get the right URL.
#
#
# ── STEP 5: RUN THE SCRIPT ───────────────────────────────────────────────────
#
#   In the terminal (after Step 3), just type:
#
#       python3 scrape_pokemon_images.py
#
#   The script will ask you two questions, one at a time:
#
#     Question 1 — Paste the URL:
#       Paste the URL you copied in Step 4 and press Enter.
#       Do NOT include any quote marks — just paste the raw URL.
#
#     Question 2 — Paste the folder path:
#       Paste the full path to the set's output folder and press Enter.
#       Example:  /Users/yourname/Desktop/Pokemon/M4
#       Do NOT include any quote marks around the path.
#       The folder will be created automatically if it does not exist yet.
#
#   The script will then run on its own, downloading all pages of that set
#   automatically.  You will see progress printed in the terminal.
#
#
# ── STEP 6: FIND YOUR DOWNLOADED IMAGES ─────────────────────────────────────
#
#   When the script finishes it will print:
#       "All done. Images saved to: /path/to/your/folder"
#
#   Open that folder in Finder (Mac) or File Explorer (Windows).
#   You will find:
#     • 001.png, 002.png, 003.png … — the downloaded card images
#     • download_log.csv            — a record of every card processed
#
#
# ── HOW FILES ARE NAMED ──────────────────────────────────────────────────────
#
#   Files are named by the card's position in the set:
#     001.png = card #1,  002.png = card #2,  etc.
#
#   If a card fails to download, its number is left as a gap and the
#   next card keeps its own correct number.  Example:
#     001.png  ✓  downloaded
#     002      ✗  failed — no file, number is skipped
#     003.png  ✓  downloaded  (still named 003, not renumbered to 002)
#
#   Numbering continues across pages:
#     Page 1 → 001 to 020  (20 cards per page)
#     Page 2 → 021 to 040
#     and so on.
#
#
# ── CSV FILTER: DOWNLOADING ONLY SPECIFIC CARDS ──────────────────────────────
#
#   If your set folder already contains a CSV file (e.g. one of your
#   missing-images CSV files), the script will automatically read it and
#   download ONLY the cards listed in the "assetLocalId" column.
#   All other cards will be skipped — their position numbers are still
#   reserved as gaps so numbering stays consistent.
#
#   If no CSV is found in the folder, the script downloads all cards.
#
#   If more than one CSV is found in the folder, the script will list them
#   and ask you to choose which one to use.
#
#
# ── THE DOWNLOAD LOG (download_log.csv) ──────────────────────────────────────
#
#   After every run a file called download_log.csv is saved in the output
#   folder.  It has these columns:
#     position       — the 3-digit number used as the image filename
#     card_id        — the site's internal ID for this card
#     page_url       — which page it was found on
#     image_url      — where the image was downloaded from
#     local_filename — the saved filename, or "FAILED" if it did not download
#     status         — "ok" or "failed"
#
#   Use this to cross-reference against your master CSV to see exactly
#   what you have and what is still missing.
#
#
# ── MOVING TO COLLECTED WHEN DONE ────────────────────────────────────────────
#
#   When the script finishes it looks for a folder called "Collected" sitting
#   next to your "Need" folder (i.e. as a sibling in the same language folder).
#   If found, it asks:
#
#       Scrape complete. Move 'SC2b' to Collected? (y/n):
#
#   Type y and press Enter to move the set folder automatically.
#   Type n and press Enter to leave it where it is.
#
#   If no "Collected" folder is found the script just skips this step silently.
#
#
# ── RESUMING AFTER AN INTERRUPTION ───────────────────────────────────────────
#
#   If the script stops early (crash, Ctrl+C, internet dropout), just run
#   it again with the same URL and folder.  It will automatically skip any
#   images already saved on disk and continue from where it left off.
#
#
# ── TERMINAL TIPS ────────────────────────────────────────────────────────────
#
#   Ctrl + C          — stop the script at any time
#   Ctrl + U          — clear the current line in the terminal
#   Up arrow key      — bring back your previous command
#   Option + ←/→      — jump one word at a time when editing a command (Mac)
#
#   When typing into a script prompt (URL / folder), do NOT use quotes.
#   Quotes are only needed when passing arguments directly on the command line.
#
#
# ── COMMON ERRORS AND FIXES ──────────────────────────────────────────────────
#
#   "ModuleNotFoundError: No module named 'requests'"
#     → Run:  pip3 install requests beautifulsoup4
#
#   "No such file or directory: scrape_pokemon_images.py"
#     → You are in the wrong folder.  Run the cd command from Step 3 first.
#
#   "python3: command not found"
#     → Python is not installed.  Download it from https://www.python.org
#
#   Images downloading but nothing happens for a few seconds between each
#     → Normal.  The script waits 2–4 seconds between downloads on purpose.
#
#   A lot of "403 Forbidden" errors
#     → The server is rate-limiting you.  Wait a few minutes and try again.
#       If it keeps happening, the image URL pattern may have changed —
#       see the KNOWN GOTCHAS section below.
#
#
# ── KNOWN GOTCHAS (asia.pokemon-card.com specific) ───────────────────────────
#
#   • The list page does not contain images in its raw HTML — they are loaded
#     by JavaScript.  The script works around this by constructing the image
#     URL directly from the card ID, which IS in the static HTML.
#     If the direct URL fails, the script visits the card detail page instead.
#
#   • Image URL prefixes by region (confirmed April 2026):
#       hk    →  hk{id:08d}.png       e.g. hk00018890.png
#       hk-en →  default{id:08d}.png  e.g. default00021593.png
#       tw    →  tw{id:08d}.png       e.g. tw00018421.png
#     If images start returning 404 errors, right-click a card image in
#     Chrome → "Copy image address" and check if the pattern has changed.
#     Update the REGION_IMG_PREFIX dictionary near the top of this file.
#
#   • Different regions use different card IDs for the same physical card.
#     Always keep each region's images in separate folders.
#
#   • Always get your URL by clicking a set from the card-search index page
#     (asia.pokemon-card.com/hk/card-search/), not from the filter dropdown
#     on the list page.  The dropdown does not update the URL.
#
# =============================================================================
