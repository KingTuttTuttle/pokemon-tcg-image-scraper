#!/usr/bin/env python3
"""
check_missing_images.py
-----------------------
Reads the TCGdex status page (https://api.tcgdex.net/status),
finds every set where:
  - cards   = 100%  (card data is complete for that language)
  - images  < 100%  (images are still missing)

For each match it:
  1. Creates a folder inside the language's Need/ folder named after the set ID
  2. Moves any matching CSV from the top of Need/ into that folder
  3. Writes a full report CSV at the end

Languages are processed in the order they appear on the status page.
Chinese (Traditional) is skipped — those folders are already managed separately.

Usage:
    python3 check_missing_images.py

See the USAGE GUIDE at the bottom of this file for setup instructions.
"""

import csv
import glob
import os
import re
import shutil
import requests
from bs4 import BeautifulSoup
from typing import Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION  —  only change things in this block
# ─────────────────────────────────────────────────────────────────────────────

# URL of the TCGdex status page
STATUS_URL = "https://api.tcgdex.net/status"

# Root folder containing all your language subfolders.
# This assumes check_missing_images.py sits in the same folder as MissingImages/.
# If that is not the case, replace the path below with the full path.
MISSING_IMAGES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "MissingImages"
)

# Languages to skip entirely.
# Chinese (traditionnal) uses a typo in the API — both spellings are listed here.
SKIP_LANGUAGES = {
    "chinese (traditionnal)",
    "chinese (traditional)",
}

# The name of the subfolder inside each language folder where sets live.
# Matched case-insensitively so "Need", "need", "Need " all work.
NEED_FOLDER_NAME = "need"

# ─────────────────────────────────────────────────────────────────────────────
# HTTP HEADERS  —  browser-like so the request is not blocked
# ─────────────────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 20


# ─────────────────────────────────────────────────────────────────────────────
# FOLDER HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def find_language_folder(lang_name: str) -> Optional[str]:
    """
    Find the folder inside MISSING_IMAGES_DIR whose name matches lang_name.
    Matching is case-insensitive and ignores trailing spaces.
    Returns the full path or None if not found.
    """
    try:
        for entry in os.scandir(MISSING_IMAGES_DIR):
            if entry.is_dir() and entry.name.strip().lower() == lang_name.strip().lower():
                return entry.path
    except (FileNotFoundError, PermissionError):
        pass
    return None


def find_need_folder(lang_folder: str) -> Optional[str]:
    """
    Find the Need/ subfolder inside a language folder.
    Matching is case-insensitive and ignores trailing spaces.
    """
    try:
        for entry in os.scandir(lang_folder):
            if entry.is_dir() and entry.name.strip().lower() == NEED_FOLDER_NAME:
                return entry.path
    except (FileNotFoundError, PermissionError):
        pass
    return None


def find_csv_for_set(need_folder: str, set_id: str) -> Optional[str]:
    """
    Search the top level of need_folder for a CSV file whose setId column
    matches set_id.  Returns the file path if found, else None.
    Only looks at the first data row of each CSV to determine its set.
    """
    for csv_path in glob.glob(os.path.join(need_folder, "*.csv")):
        try:
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("setId", "").strip() == set_id:
                        return csv_path
                    break  # only need the first row to identify the set
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# STATUS PAGE PARSER
# ─────────────────────────────────────────────────────────────────────────────

def extract_set_id(cell_text: str) -> Optional[str]:
    """
    Pull the set ID from a cell like 'Base Set (base1)'.
    The ID is always the last thing in parentheses.
    Returns None if no parentheses are found.
    """
    match = re.search(r'\(([^)]+)\)\s*$', cell_text.strip())
    return match.group(1).strip() if match else None


def parse_percentage(text: str) -> float:
    """
    Parse a string like '100.00% (102)' and return 100.0.
    Returns 0.0 if nothing can be parsed.
    """
    match = re.match(r'([\d.]+)%', text.strip())
    return float(match.group(1)) if match else 0.0


def fetch_and_parse_status() -> Dict[str, List[dict]]:
    """
    Fetch the TCGdex status page and parse the per-set table into a dictionary:

        {
            "Chinese (simplified)": [
                {
                    "set_name":  "Base Set",
                    "set_id":    "base1",
                    "cards_pct": 100.0,
                    "images_pct": 87.5
                },
                ...
            ],
            ...
        }

    The page has two tables:
      Table 0 — overall summary (ignored)
      Table 1 — per-set breakdown with 560+ rows (this is what we parse)

    Table 1 structure (repeats for each series section):
      Series header row  : <th colspan="35"><h2>Sun & Moon</h2></th>
      Language header row: <th rowspan="2">Set Name</th>
                           <th colspan="2">Chinese (simplified)</th> ...
      Type header row    : <th>Cards</th><th>Images</th> ...
      Data rows          : <td>Set Name (setId)<br/>N cards</td>
                           <td class="...">100.00%<br/>(102)</td> ...
                           <td class="na"></td>  ← not applicable for this language

    Only includes sets where cards = 100% AND images < 100%.
    Languages in SKIP_LANGUAGES are excluded.
    """
    print(f"Fetching {STATUS_URL} ...")
    resp = requests.get(STATUS_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # The page has 2 tables — we need the second one (index 1)
    tables = soup.find_all("table")
    if len(tables) < 2:
        raise ValueError(
            f"Expected 2 tables on the page but found {len(tables)}. "
            "The page structure may have changed."
        )
    table = tables[1]

    all_rows = table.find_all("tr")

    # ── Build column mapping from the first language header row ───────────────
    # That row has: <th rowspan="2">Set Name</th>
    #               <th colspan="2">Chinese (simplified)</th> ...
    # "Set Name" occupies col 0; each language spans 2 columns (Cards + Images).

    col_to_lang: Dict[int, str] = {}
    languages_in_order: List[str] = []
    col_to_type: Dict[int, str] = {}

    lang_row_found = False
    for row in all_rows:
        ths = row.find_all("th")
        # The language header row is identified by having th elements with colspan="2"
        if not lang_row_found and any(th.get("colspan") == "2" for th in ths):
            col_idx = 0
            for th in ths:
                colspan = int(th.get("colspan", 1))
                text    = th.get_text(strip=True)
                if colspan == 2:
                    col_to_lang[col_idx]     = text
                    col_to_lang[col_idx + 1] = text
                    if text.lower() not in SKIP_LANGUAGES:
                        languages_in_order.append(text)
                col_idx += colspan
            lang_row_found = True
            continue

        # The type row immediately follows the language header row (Cards/Images labels)
        if lang_row_found and not col_to_type and ths:
            # "Set Name" has rowspan=2 so it doesn't appear in this row.
            # The first <th> here corresponds to col 1 in the overall table.
            col_idx = 1
            for th in ths:
                text = th.get_text(strip=True).lower()
                col_to_type[col_idx] = "images" if "image" in text else "cards"
                col_idx += 1
            break  # column mapping is complete — no need to read more header rows

    if not languages_in_order or not col_to_type:
        raise ValueError(
            "Could not parse column headers from the per-set table. "
            "The page structure may have changed."
        )

    # ── Parse data rows ───────────────────────────────────────────────────────
    # Data rows have <td> elements and no <th> elements.
    results: Dict[str, List[dict]] = {lang: [] for lang in languages_in_order}

    for row in all_rows:
        # Skip any row that contains a <th> (series headers, language headers, type headers)
        if row.find("th"):
            continue

        cells = row.find_all("td")
        if not cells:
            continue

        # First cell is the set name, which also contains the card count after a <br>
        # e.g.  "横空出世 苍 (CSM1bC)\n151 cards"
        # We only want the part before the card count.
        raw_name = cells[0].get_text(separator="\n", strip=True)
        set_name = raw_name.split("\n")[0].strip()
        set_name = re.sub(r'\s*\d+\s*cards\s*$', '', set_name).strip()

        set_id = extract_set_id(set_name)
        if not set_id:
            continue

        # Collect cards% and images% per language
        lang_stats: Dict[str, Dict[str, float]] = {}

        for c_idx, cell in enumerate(cells[1:], start=1):
            # Skip N/A cells — this language doesn't have this set at all
            if "na" in cell.get("class", []):
                continue

            lang     = col_to_lang.get(c_idx)
            col_type = col_to_type.get(c_idx, "cards")

            if not lang or lang.lower() in SKIP_LANGUAGES:
                continue

            if lang not in lang_stats:
                lang_stats[lang] = {"cards": 0.0, "images": 0.0}

            lang_stats[lang][col_type] = parse_percentage(cell.get_text(strip=True))

        # Flag sets where cards are 100% but images are not yet complete
        for lang, stats in lang_stats.items():
            if stats["cards"] >= 100.0 and stats["images"] < 100.0:
                if lang in results:
                    results[lang].append({
                        "set_name":   set_name,
                        "set_id":     set_id,
                        "cards_pct":  stats["cards"],
                        "images_pct": stats["images"],
                    })

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:

    # ── Fetch and parse status data ───────────────────────────────────────────
    try:
        missing_by_lang = fetch_and_parse_status()
    except Exception as exc:
        print(f"\nERROR: {exc}")
        return

    total_sets  = sum(len(v) for v in missing_by_lang.values())
    print(f"Found {total_sets} set(s) across all languages with complete cards but missing images.\n")

    report_rows = []

    # ── Process each language in order ───────────────────────────────────────
    for lang, sets in missing_by_lang.items():

        print(f"{'─' * 60}")
        print(f"  {lang}  ({len(sets)} set(s))")
        print(f"{'─' * 60}")

        if not sets:
            print("  Nothing to do.\n")
            continue

        # Find the language folder on disk
        lang_folder = find_language_folder(lang)
        if not lang_folder:
            print(f"  [SKIPPED] No folder found for '{lang}' in MissingImages/\n")
            for s in sets:
                report_rows.append({
                    "language":   lang,
                    "set_id":     s["set_id"],
                    "set_name":   s["set_name"],
                    "cards_pct":  f"{s['cards_pct']:.1f}%",
                    "images_pct": f"{s['images_pct']:.1f}%",
                    "status":     "skipped — no language folder",
                })
            continue

        # Find the Need/ subfolder
        need_folder = find_need_folder(lang_folder)
        if not need_folder:
            print(f"  [SKIPPED] No 'Need' folder found inside '{lang_folder}'\n")
            for s in sets:
                report_rows.append({
                    "language":   lang,
                    "set_id":     s["set_id"],
                    "set_name":   s["set_name"],
                    "cards_pct":  f"{s['cards_pct']:.1f}%",
                    "images_pct": f"{s['images_pct']:.1f}%",
                    "status":     "skipped — no Need folder",
                })
            continue

        # ── Create set folders ────────────────────────────────────────────────
        for s in sets:
            set_id     = s["set_id"]
            set_folder = os.path.join(need_folder, set_id)
            csv_note   = ""

            if os.path.exists(set_folder):
                folder_status = "already exists"
            else:
                os.makedirs(set_folder)
                folder_status = "created"

                # Move matching CSV into the new folder if one exists
                csv_path = find_csv_for_set(need_folder, set_id)
                if csv_path:
                    dest = os.path.join(set_folder, os.path.basename(csv_path))
                    shutil.move(csv_path, dest)
                    csv_note = " + CSV moved"

            status_label = folder_status + csv_note
            print(
                f"  {set_id:<15}  "
                f"cards={s['cards_pct']:.0f}%  "
                f"images={s['images_pct']:.1f}%  "
                f"[{status_label}]"
            )
            report_rows.append({
                "language":   lang,
                "set_id":     set_id,
                "set_name":   s["set_name"],
                "cards_pct":  f"{s['cards_pct']:.1f}%",
                "images_pct": f"{s['images_pct']:.1f}%",
                "status":     status_label,
            })

        print()

    # ── Write report CSV ──────────────────────────────────────────────────────
    report_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "missing_images_report.csv"
    )
    with open(report_path, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["language", "set_id", "set_name", "cards_pct", "images_pct", "status"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(report_rows)

    print(f"{'─' * 60}")
    print(f"Report saved → {report_path}")
    print(f"Total sets processed: {len(report_rows)}")


if __name__ == "__main__":
    main()


# =============================================================================
# USAGE GUIDE
# =============================================================================
#
# ── WHAT THIS SCRIPT DOES ────────────────────────────────────────────────────
#
#   1. Fetches https://api.tcgdex.net/status
#   2. Reads every language column (except Chinese Traditional, which is
#      already handled separately)
#   3. Finds sets where cards = 100% but images < 100%
#   4. Creates a folder for each such set inside the matching language's
#      Need/ folder:
#          MissingImages/English/Need/base1/
#   5. If a CSV file exists in Need/ for that set, moves it into the
#      new folder automatically
#   6. Writes missing_images_report.csv in the same folder as this script
#
#
# ── HOW TO RUN ───────────────────────────────────────────────────────────────
#
#   1. Open a terminal and navigate to the script folder:
#          cd "/Users/yourname/Desktop/Missing Images CSVs"
#
#   2. Run:
#          python3 check_missing_images.py
#
#   That's it. You can re-run it at any time as percentages change —
#   it will skip folders that already exist and only create new ones.
#
#
# ── REQUIRED FOLDER STRUCTURE ────────────────────────────────────────────────
#
#   This script must be in the same folder as MissingImages/:
#
#       Missing Images CSVs/
#         check_missing_images.py   ← this file
#         MissingImages/
#           English/
#             Need/
#             Collected/
#           Chinese (simplified)/
#             Need/
#             Collected/
#           ...
#
#   Each language folder name must match (case-insensitively) the language
#   column name shown on the TCGdex status page.
#   e.g. "English" matches "English", "ENGLISH", "English " (trailing space ok)
#
#
# ── SKIPPING A LANGUAGE ──────────────────────────────────────────────────────
#
#   Languages listed in SKIP_LANGUAGES at the top of the file are ignored
#   completely.  By default, Chinese (Traditional) is skipped.
#   Add or remove entries as needed:
#
#       SKIP_LANGUAGES = {
#           "chinese (traditionnal)",
#           "japanese",             ← add this to also skip Japanese
#       }
#
#
# ── THE REPORT CSV ───────────────────────────────────────────────────────────
#
#   After every run, missing_images_report.csv is saved in the same folder
#   as this script.  Columns:
#       language    — the language column from the status page
#       set_id      — the set ID used as the folder name (e.g. base1)
#       set_name    — the full set name from the status page
#       cards_pct   — card data completion percentage
#       images_pct  — image completion percentage
#       status      — "created", "already exists", "created + CSV moved",
#                     or "skipped — reason"
#
#
# ── IF THE PAGE STRUCTURE CHANGES ────────────────────────────────────────────
#
#   If the script stops finding sets correctly, the status page HTML may
#   have changed.  The parser looks for:
#     • A <table> element on the page
#     • A two-row header: row 1 has language names with colspan=2,
#       row 2 has "Cards" and "Images" labels
#     • Data rows with set names like "Base Set (base1)" in the first cell
#
#   If the structure changes, update the fetch_and_parse_status() function.
#
# =============================================================================
