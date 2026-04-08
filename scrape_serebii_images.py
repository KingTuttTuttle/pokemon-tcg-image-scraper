#!/usr/bin/env python3
"""
scrape_serebii_images.py
------------------------
Downloads Japanese Pokémon TCG card images from Serebii:
  https://www.serebii.net/card/japanese.shtml

Full-size image URL pattern:
  https://www.serebii.net/card/{slug}/{N}.jpg
  where N is 1-based (no zero-padding in the URL)
  saved locally as 001.jpg, 002.jpg...

How it works:
  1. Paste one or more Serebii set page URLs
  2. Extracts the set slug from the URL
  3. Auto-matches to a Japanese/Need/ folder via TCGdex API
  4. Applies CSV filter if one is found in the set folder
  5. Downloads full-size images with polite delays
  6. Moves completed set to Collected/ on success

Usage:
  caffeinate -i python3 scrape_serebii_images.py
"""

import csv
import glob
import os
import re
import shutil
import time
import random
import logging
from typing import Optional, List, Set, Dict
from urllib.parse import urlparse

try:
    import readline
except ImportError:
    pass

import requests
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

SEREBII_BASE         = "https://www.serebii.net"
JAPANESE_FOLDER_NAME = "Japanese"

# ─────────────────────────────────────────────────────────────────────────────
# SEREBII SLUG → TCGDEX SET ID (folder name in Japanese/Need/)
# Built by cross-referencing Serebii English names with TCGdex Japanese set IDs
# ─────────────────────────────────────────────────────────────────────────────

SEREBII_TO_SET_ID: Dict[str, str] = {
    # ── Vintage ───────────────────────────────────────────────────────────────
    "vs":                                   "VS1",
    # ── HeartGold / SoulSilver ────────────────────────────────────────────────
    "heartgoldcollection":                  "L1a",
    "soulsilvercollection":                 "L1b",
    "revivinglegends":                      "L2",
    "lostlink":                             "LL",
    "bigsummitclash":                       "L3",
    # ── XY era ───────────────────────────────────────────────────────────────
    "collectionx":                          "XY1a",
    "collectiony":                          "XY1b",
    "wildblaze":                            "XY2",
    "risingfist":                           "XY3",
    "phantomgate":                          "XY4",
    "gaiavolcano":                          "XY5a",
    "tidalstorm":                           "XY5a",
    "teammagmavsteamaquadoublecrisis":      "CP1",
    "emeraldbreak":                         "XY6",
    "bandedring":                           "XY7",
    "legendaryshinycollection":             "CP2",
    "blueshock":                            "XY8a",
    "redflash":                             "XY8b",
    "rageofthetornheavens":                 "XY9",
    "pokekyuncollection":                   "CP3",
    "awakeningpsychicchampion":             "XY10",
    "premiumchampionpackexmbreak":          "CP4",
    "crueltraitor":                         "XY11a",
    "feverburstfighter":                    "XY11a",
    "20thanniversary":                      "CP6",
    # ── Sun & Moon era ───────────────────────────────────────────────────────
    "collectionsun":                        "SM1S",
    "collectionmoon":                       "SM1M",
    "sunmoonstrengtheningpack":             "SM1+",
    "islandsawaityou":                      "SM2K",
    "alolanmoonlight":                      "SM2L",
    "beyondanewchallenge":                  "sm2+",
    "light-devouringdarkness":              "SM3N",
    "didyouseethefightingrainbow":          "SM3H",
    "shininglegend":                        "SM3+",
    "ultradimensionalbeast":                "SM4A",
    "awakeninghero":                        "SM4S",
    "gxbattleboost":                        "SM4+",
    "ultramoon":                            "SM5M",
    "ultrasun":                             "SM5S",
    "ultraforces":                          "SM5+",
    "forbiddenlightjp":                     "SM6",
    "dragonstorm":                          "SM6a",
    "championroad":                         "SM6b",
    "charismaofthewreckedsky":              "SM7",
    "thunderclapspark":                     "SM7a",
    "fairyrise":                            "SM7b",
    "explosiveimpact":                      "SM8",
    "darkorder":                            "SM8a",
    "gxultrashiny":                         "SM8b",
    "tagbolt":                              "SM9",
    "nightunison":                          "SM9a",
    "fullmetalwall":                        "SM9b",
    "doubleblaze":                          "SM10",
    "ggend":                                "sn10a",
    "skylegend":                            "SM10b",
    "greatdetectivepikachu":                "SMP2",
    "miracletwin":                          "sn11",
    "remixbout":                            "SM11a",
    "dreamleague":                          "SM11b",
    "altergenesis":                         "SM12",
    "tagallstars":                          "SM12a",
    # ── Sword & Shield era ───────────────────────────────────────────────────
    "sword":                                "S1W",
    "shield":                               "S1H",
    "vmaxrising":                           "S1a",
    "rebelliousclash":                      "S2",
    "explosivewalker":                      "S2a",
    "infinityzone":                         "S3",
    "legendarybeat":                        "S3a",
    "astonishingvolttackle":                "S4",
    "shinystarv":                           "S4a",
    "singlestrikemaster":                   "S5I",
    "rapidstrikemaster":                    "S5R",
    "matchlessfighter":                     "S5a",
    "eeveeheroes":                          "S6a",
    "jetblackpoltergeist":                  "S6K",
    "silverlance":                          "S6H",
    "skyscrapingperfect":                   "S7D",
    "blueskystream":                        "S7R",
    "fusionarts":                           "S8",
    "25thanniversarycollection":            "S8a",
    "vmaxclimax":                           "S8b",
    "starbirth":                            "S9",
    "battleregion":                         "S9a",
    "spacejuggler":                         "S10P",
    "timegazer":                            "S10D",
    "pokemongoxpokemoncardgame":            "S10b",
    "darkphantasma":                        "S10a",
    "lostabyss":                            "S11",
    "incandescentarcana":                   "S11a",
    "paradigmtrigger":                      "S12",
    "vstaruniverse":                        "S12a",
    # ── Scarlet & Violet era ─────────────────────────────────────────────────
    "scarletex":                            "SV1S",
    "violetex":                             "SV1V",
    "tripletbeat":                          "SV1a",
    "pokemoncard151":                       "SV2a",
    "snowhazard":                           "SV2P",
    "clayburst":                            "SV2D",
    "ruleroftheblackflame":                 "SV3",
    "ragingsurf":                           "SV3a",
    "ancientroar":                          "SV4K",
    "futureflash":                          "SV4M",
    "wildforce":                            "SV5K",
    "cyberjudge":                           "SV5M",
    "crimsonhaze":                          "SV5a",
    "maskofchange":                         "SV6",
    "nightwanderer":                        "SV6a",
    "stellarmiracle":                       "SV7",
    "paradisedragona":                      "SV7a",
    "superelectricbreaker":                 "SV8",
    "terastalfestivalex":                   "SV8a",
    "battlepartners":                       "SV9",
    "hotairarena":                          "SV9a",
    "gloryofteamrocket":                    "SV10",
    "blackbolt-jp":                         "SV11B",
    "whiteflare-jp":                        "SV11W",
    # ── MC / MEGA era ────────────────────────────────────────────────────────
    "megasymphonia":                        "M1S",
    "megabrave":                            "M2",
    "nihilzero":                            "M3",
    "ninjaspinner":                         "M4",
    "infernox":                             "M5",
    "megadreamex":                          "M6",
}

DEFAULT_DELAY_MIN = 2.0
DEFAULT_DELAY_MAX = 4.0
MAX_RETRIES       = 3
RETRY_BACKOFF     = 2.0
REQUEST_TIMEOUT   = 30


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
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer":         SEREBII_BASE,
    })
    return session


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def polite_delay() -> None:
    delay = random.uniform(DEFAULT_DELAY_MIN, DEFAULT_DELAY_MAX)
    log.info(f"    Waiting {delay:.1f}s...")
    time.sleep(delay)


def fetch_with_retry(
    session: requests.Session,
    url: str,
    stream: bool = False,
) -> Optional[requests.Response]:
    backoff = RETRY_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, stream=stream)
            if resp.status_code == 200:
                return resp
            log.warning(f"    HTTP {resp.status_code} on attempt {attempt}: {url}")
        except requests.RequestException as e:
            log.warning(f"    Request error on attempt {attempt}: {e}")
        if attempt < MAX_RETRIES:
            time.sleep(backoff * attempt)
    log.error(f"    Failed after {MAX_RETRIES} attempts: {url}")
    return None


def normalize_slug(name: str) -> str:
    """Normalize an English set name to a Serebii slug.
    e.g. 'Skyscraping Perfect' → 'skyscrapingperfect'
    """
    return re.sub(r'[^a-z0-9-]', '', name.lower().replace(' ', ''))


def extract_slug(url: str) -> Optional[str]:
    """Extract the set slug from a Serebii URL.
    e.g. 'https://www.serebii.net/card/skyscrapingperfect' → 'skyscrapingperfect'
    """
    path  = urlparse(url).path.strip("/")
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "card":
        return parts[1]
    return None




# ─────────────────────────────────────────────────────────────────────────────
# FOLDER DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def find_japanese_need_folder(script_dir: str) -> Optional[str]:
    missing_images = os.path.join(script_dir, "MissingImages")
    if not os.path.isdir(missing_images):
        return None
    for lang_entry in os.scandir(missing_images):
        if lang_entry.is_dir() and lang_entry.name.strip().lower() == JAPANESE_FOLDER_NAME.lower():
            for sub in os.scandir(lang_entry.path):
                if sub.is_dir() and sub.name.strip().lower() == "need":
                    return sub.path
    return None


def find_folder_for_slug(slug: str, script_dir: str) -> Optional[str]:
    """Find the Japanese/Need/{set_id} folder matching a Serebii slug."""
    set_id = SEREBII_TO_SET_ID.get(slug)

    if not set_id:
        log.info(f"  '{slug}' not in mapping — skipping.")
        return None

    need_folder = find_japanese_need_folder(script_dir)
    if not need_folder:
        log.warning("  Japanese/Need/ folder not found.")
        return None

    candidate = os.path.join(need_folder, set_id)
    if os.path.isdir(candidate):
        log.info(f"  Matched '{slug}' → {set_id}")
        return candidate

    # Auto-create the folder so the set can be downloaded
    os.makedirs(candidate, exist_ok=True)
    log.info(f"  Auto-created folder: {candidate}")
    return candidate


def find_in_done_folders(slug: str, script_dir: str) -> Optional[str]:
    """Check if the set is already in Collected/ or Uploaded/."""
    set_id = SEREBII_TO_SET_ID.get(slug)
    if not set_id:
        return None

    missing_images = os.path.join(script_dir, "MissingImages")
    for lang_entry in os.scandir(missing_images):
        if not lang_entry.is_dir():
            continue
        if lang_entry.name.strip().lower() != JAPANESE_FOLDER_NAME.lower():
            continue
        for sub in os.scandir(lang_entry.path):
            if not sub.is_dir():
                continue
            if sub.name.strip().lower() in ("collected", "uploaded"):
                try:
                    for item in os.scandir(sub.path):
                        if os.path.splitext(item.name)[0] == set_id:
                            return item.path
                except PermissionError:
                    pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# SET PAGE PARSER
# ─────────────────────────────────────────────────────────────────────────────

def get_card_numbers(session: requests.Session, slug: str) -> List[int]:
    """Fetch the Serebii set page and return a sorted list of card numbers.

    Parses the shtml links (e.g. /card/slug/001.shtml → 1) which is the
    most reliable way to get every card number, including secret rares
    that go beyond the official set size.
    """
    url  = f"{SEREBII_BASE}/card/{slug}"
    resp = fetch_with_retry(session, url)
    if not resp:
        return []

    soup    = BeautifulSoup(resp.text, "html.parser")
    numbers = set()

    # Method 1: parse shtml links  e.g. /card/skyscrapingperfect/001.shtml
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        m = re.match(rf'/card/{re.escape(slug)}/(\d+)\.shtml', href)
        if m:
            numbers.add(int(m.group(1)))

    # Method 2 (fallback): parse "N / TOTAL" text anywhere in table cells
    if not numbers:
        for td in soup.find_all("td"):
            text = td.get_text(strip=True)
            for m in re.finditer(r'(\d+)\s*/\s*\d+', text):
                numbers.add(int(m.group(1)))

    return sorted(numbers)


# ─────────────────────────────────────────────────────────────────────────────
# CSV FILTER
# ─────────────────────────────────────────────────────────────────────────────

def load_required_positions(output_dir: str) -> Optional[Set[int]]:
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
) -> bool:
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
    polite_delay()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# MOVE TO COLLECTED
# ─────────────────────────────────────────────────────────────────────────────

def move_to_collected(output_dir: str, total_failures: int) -> None:
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

    # Move CSVs to logs
    logs_folder = None
    for entry in os.scandir(lang_folder):
        if entry.is_dir() and entry.name.strip().lower() == "missing reports and collection logs":
            logs_folder = entry.path
            break

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
# SCRAPE SET
# ─────────────────────────────────────────────────────────────────────────────

def scrape_set(
    session: requests.Session,
    slug: str,
    output_dir: str,
    required_positions: Optional[Set[int]],
) -> int:
    card_numbers = get_card_numbers(session, slug)
    if not card_numbers:
        log.error(f"  Could not determine card list for '{slug}' — skipping.")
        return 1

    log.info(f"  {len(card_numbers)} card(s) found  (#{card_numbers[0]}–#{card_numbers[-1]})")

    total_failures = 0
    csv_path       = os.path.join(output_dir, "download_log.csv")
    csv_file       = open(csv_path, "w", newline="", encoding="utf-8")
    fieldnames     = ["position", "image_url", "local_filename", "status"]
    csv_writer     = csv.DictWriter(csv_file, fieldnames=fieldnames)
    csv_writer.writeheader()

    try:
        for position in card_numbers:
            filename = f"{position:03d}.jpg"
            img_url  = f"{SEREBII_BASE}/card/{slug}/{position}.jpg"

            if required_positions is not None and position not in required_positions:
                log.info(f"\n  Card #{position:03d} — not in filter, skipping")
                continue

            log.info(f"\n  Card #{position:03d}  |  {img_url}")

            success = download_image(session, img_url, output_dir, filename)
            if not success:
                total_failures += 1

            csv_writer.writerow({
                "position":       f"{position:03d}",
                "image_url":      img_url,
                "local_filename": filename if success else "FAILED",
                "status":         "ok" if success else "failed",
            })
    finally:
        csv_file.close()

    return total_failures


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    session    = create_session()

    print("\nPokémon TCG Serebii Scraper  —  press Ctrl+C at any time to quit.")
    print("Paste Serebii set page URLs.")
    print("e.g. https://www.serebii.net/card/skyscrapingperfect")

    url_queue: List[str] = []

    while True:
        if not url_queue:
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
        if url_queue:
            print(f"  ({len(url_queue)} more URL(s) queued after this one)")

        slug = extract_slug(url)
        if not slug:
            log.error(f"Could not extract set slug from: {url}")
            continue

        log.info(f"\nSet: {slug}")

        # Check if already collected
        already_done = find_in_done_folders(slug, script_dir)
        if already_done:
            log.info(f"'{slug}' already collected at: {already_done} — skipping.")
            continue

        # Find output folder
        output_dir = find_folder_for_slug(slug, script_dir)
        if not output_dir:
            continue

        os.makedirs(output_dir, exist_ok=True)
        log.info(f"Output folder: {os.path.abspath(output_dir)}")

        required_positions = load_required_positions(output_dir)
        if required_positions is None:
            log.info("No filter CSV — all cards will be downloaded.")

        print(f"\n{'═' * 60}")
        print(f"  SET: {slug}  |  folder: {os.path.basename(output_dir)}")
        print(f"{'═' * 60}")

        total_failures = scrape_set(session, slug, output_dir, required_positions)
        log.info(f"\nDone. Saved to: {os.path.abspath(output_dir)}")
        move_to_collected(output_dir, total_failures)

    session.close()
    print(f"\n{'═' * 60}")
    print("All sets processed. Goodbye!")


if __name__ == "__main__":
    main()
