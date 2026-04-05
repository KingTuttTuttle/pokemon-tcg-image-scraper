# Pokémon TCG Image Scraper & Manager

A collection of Python and Node.js scripts for automatically downloading, organising, and managing Pokémon TCG card images from multiple official sources, with integration to the [TCGdex API](https://tcgdex.net).

Built to support a card image repository across multiple languages including Japanese, Korean, Chinese (Traditional & Simplified), Thai, and Indonesian.

---

## What It Does

- Scrapes card images from the official Pokémon TCG Asia website, the official Japanese site, and Pokellector
- Automatically detects the correct output folder based on the URL region code
- Filters downloads using TCGdex CSV data so only missing cards are downloaded
- Organises images by language and set with sequential positional numbering (001.png, 002.png…)
- Automatically moves completed sets to `Collected/` on 100% success
- Packages sets into upload-ready batch zips under 2 GB
- Cleans zip files of non-image files before upload
- Moves CSV logs out of image folders so they don't interfere with uploads

---

## Scripts

### Scrapers

| Script | Source | Description |
|---|---|---|
| `scrape_pokemon_images.py` | asia.pokemon-card.com | Main scraper. Paste one or more URLs, auto-detects language folder, downloads missing card images |
| `scrape_official_japanese.py` | pokemon-card.com | Scans Japanese Need folders, checks each set against the official API, downloads available sets as JPG |
| `scrape_pokellector_images.py` | jp.pokellector.com | Scrapes Japanese card images from Pokellector, visits each card detail page to get image URLs |

### Utilities

| Script | Description |
|---|---|
| `batch_zip.py` | Groups set folders from `Collected/` into upload-ready batch zips (max 1.9 GB each) in `Uploaded/` |
| `clean_zips.py` | Scans all zips in `Collected/` and `Uploaded/`, removes non-image files, moves CSVs to logs |
| `move_collection_csvs.py` | Moves CSVs out of `Collected/` set folders into the logs folder. Auto-creates logs and `Uploaded/` folders if missing |
| `check_missing_images.py` | Reads the TCGdex status page and creates folders for sets with missing images |
| `run_missing_reports.py` | Runs `missing-images-report.js` across all set folders in bulk |
| `create_set_folders.py` | Reads TCGdex CSVs and creates matching set folders in the correct language directory |
| `missing-images-report.js` | Node.js script that queries the TCGdex API and generates a CSV of missing images per set |

---

## Requirements

- Python 3
- Node.js

Install Python dependencies (run once):
```bash
# Mac / Linux
pip3 install requests beautifulsoup4

# Windows
pip install requests beautifulsoup4
```

Install Node.js dependencies (run once):
```bash
npm install
```

---

## Folder Structure

The scripts are designed to work with the following layout. Set it up once and everything is detected automatically.

```
Missing Images CSVs/
  ├── scrape_pokemon_images.py
  ├── scrape_official_japanese.py
  ├── scrape_pokellector_images.py
  ├── batch_zip.py
  ├── clean_zips.py
  ├── check_missing_images.py
  ├── run_missing_reports.py
  ├── create_set_folders.py
  ├── move_collection_csvs.py
  ├── missing-images-report.js
  ├── package.json
  └── MissingImages/
        └── Language Name/
              ├── Need/
              │     └── SetID/        ← place the TCGdex CSV for this set here
              ├── Collected/          ← sets move here automatically on success
              ├── Uploaded/           ← batch zips go here, ready for upload
              └── Missing Reports and Collection Logs/
```

---

## Usage

### 1. Scraping Card Images (Asia site)

**Mac** (prevents sleep during long runs):
```bash
caffeinate -i python3 "/path/to/scrape_pokemon_images.py"
```

**Linux:**
```bash
systemd-inhibit python3 scrape_pokemon_images.py
```

**Windows:**
```bash
python scrape_pokemon_images.py
```
> Tip: Disable sleep manually in Power Settings before a long run on Windows.

When prompted, paste one or more URLs (one per line) then press Enter on a blank line to start:
```
https://asia.pokemon-card.com/tw/card-search/list/?expansionCodes=SV9
https://asia.pokemon-card.com/tw/card-search/list/?expansionCodes=SV9a
```

The script will:
1. Auto-detect the correct language folder from the URL region code
2. Load the filter CSV if one exists in the set folder
3. Download only the missing card images with polite delays
4. Move completed sets to `Collected/` on success
5. Skip sets already in `Collected/` or `Uploaded/`

### 2. Scraping Official Japanese Images

```bash
caffeinate -i python3 "/path/to/scrape_official_japanese.py"
```

Press Enter to auto-scan all folders in `Japanese/Need/` — the script checks each set code against the official API and skips sets not available on the site (older vintage sets). Only sets that return results are downloaded.

### 3. Scraping Pokellector Images

```bash
caffeinate -i python3 "/path/to/scrape_pokellector_images.py"
```

Paste the Pokellector set page URL when prompted.

### 4. Packaging for Upload

Once scraping is complete, run `batch_zip.py` to package all sets in `Collected/` into upload-ready batch zips:

```bash
python3 batch_zip.py
```

Each batch zip contains set folders with images directly inside — no zip-in-zip. Batches are capped at 1.9 GB to stay safely under common 2 GB upload limits. Re-run safe — sets already included in a previous batch are skipped automatically.

### 5. Cleaning Existing Zips

If you have zips that may contain non-image files (`__MACOSX`, `.DS_Store`, CSVs, etc.):

```bash
python3 clean_zips.py
```

Scans all zips in `Collected/` and `Uploaded/` across every language, removes non-image files, and rewrites the zip cleanly.

---

## Supported Regions (Asia Site)

| URL Region | Language Folder |
|---|---|
| `/hk/` | Chin (t) |
| `/hk-en/` | English |
| `/tw/` | Chinese (simplified) |
| `/th/` | Thai |
| `/id/` | Indonesian |

---

## Notes

- Scrapers use polite delays (2–4 seconds) between downloads to avoid overloading servers
- If a run is interrupted, simply rerun — already downloaded images are skipped automatically
- The TCGdex API is maintained by a single developer. Bulk report scripts are rate-limited (`--concurrency 3 --rps 5`)
- Older Japanese sets (pre-SV era) are not available on the official Japanese site — the scraper detects and skips these automatically

---

## Acknowledgements

Card data provided by [TCGdex](https://tcgdex.net).
Card images sourced from the official [Pokémon TCG Asia website](https://asia.pokemon-card.com), the official [Japanese Pokémon Card site](https://www.pokemon-card.com), and [Pokellector](https://jp.pokellector.com).
