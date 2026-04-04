# Pokémon TCG Image Scraper & Manager

A collection of Python and Node.js scripts for automatically downloading, organising, and managing Pokémon TCG card images from [asia.pokemon-card.com](https://asia.pokemon-card.com), with integration to the [TCGdex API](https://tcgdex.net).

Built to support a card image repository across multiple Asian languages including Japanese, Korean, Chinese (Traditional & Simplified), Thai, and Indonesian.

---

## What It Does

- Scrapes card images from the official Pokémon TCG Asia website
- Automatically detects the correct output folder based on the URL
- Filters downloads using TCGdex CSV data so only missing cards are downloaded
- Organises images by language and set with sequential positional numbering (001.png, 002.png…)
- Automatically moves completed sets to a `Collected/` folder on 100% success
- Moves CSV logs out of image folders so they don't interfere with uploads
- Checks the TCGdex status page to identify sets that need attention
- Runs missing image reports across all sets in bulk

---

## Scripts

| Script | Description |
|---|---|
| `scrape_pokemon_images.py` | Main scraper. Paste one or more URLs, downloads all card images automatically |
| `check_missing_images.py` | Reads the TCGdex status page and creates folders for sets with missing images |
| `run_missing_reports.py` | Runs `missing-images-report.js` across all set folders in bulk |
| `create_set_folders.py` | Reads TCGdex CSVs and creates matching set folders in the correct language directory |
| `move_collection_csvs.py` | One-time cleanup — moves CSVs out of Collected set folders into the logs folder |
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
              ├── Uploaded/
              └── Missing Reports and Collection Logs/
```

---

## Usage

### Scraping Card Images

**Mac** (prevents sleep during long runs):
```bash
caffeinate -i python3 scrape_pokemon_images.py
```

**Linux** (prevents sleep during long runs):
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
https://asia.pokemon-card.com/[region]/card-search/list/?expansionCodes=[SetID]
```

The script will:
1. Automatically detect the correct output folder from the URL
2. Load the filter CSV if one exists in the set folder
3. Download only the missing card images with polite delays
4. Move the completed set to `Collected/` if everything downloaded successfully

### Checking for Missing Images

```bash
python3 check_missing_images.py
```

### Running Bulk Missing Image Reports

```bash
python3 run_missing_reports.py
```

### Creating Set Folders from CSVs

```bash
python3 create_set_folders.py
```

### Cleaning Up CSV Logs

```bash
python3 move_collection_csvs.py
```

---

## Supported Regions

| URL Region | Language |
|---|---|
| `/hk/` | Chinese Traditional (Hong Kong) |
| `/hk-en/` | English (Hong Kong) |
| `/tw/` | Chinese Simplified (Taiwan) |
| `/th/` | Thai |
| `/id/` | Indonesian |

---

## Notes

- The scraper uses polite delays (2–4 seconds) between downloads to avoid overloading the server
- If a run is interrupted, simply rerun the command — already downloaded images are skipped automatically
- The TCGdex API is maintained by a single developer. The bulk report scripts are rate-limited to be considerate of the server (`--concurrency 3 --rps 5`)

---

## Acknowledgements

Card data provided by [TCGdex](https://tcgdex.net).
Card images sourced from the official [Pokémon TCG Asia website](https://asia.pokemon-card.com).
