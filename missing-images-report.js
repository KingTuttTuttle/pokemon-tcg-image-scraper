#!/usr/bin/env node

/**
 * missing-images-report.js
 *
 * Checks every card (and set symbols/logos) across TCGDex languages and
 * reports which assets are missing (non-200 response from the image CDN).
 *
 * Usage:
 *   Create package.json by running ['{"type":"module"}' | Out-File -Encoding utf8 -FilePath package.json] 
 *   or as CMD [echo '{"type":"module"}' > package.json]
 *   EXCLUDE SQUARE BRACKETS
 * 
 *   node missing-images-report.js
 *   node missing-images-report.js --lang en
 *   node missing-images-report.js --lang en,fr,de
 *   node missing-images-report.js --set swsh3
 *   node missing-images-report.js --set swsh3,sv1
 *   node missing-images-report.js --lang en --set swsh3
 *   node missing-images-report.js --quality low --ext png
 *   node missing-images-report.js --concurrency 10 --rps 20
 *   node missing-images-report.js --output report.csv
 *
 * Flags:
 *   --lang        Comma-separated language codes, or "all" (default: all)
 *   --set         Comma-separated set IDs, or "all" (default: all)
 *   --quality     high | low  (default: high)
 *   --ext         webp | png | jpg  (default: webp)
 *   --concurrency Max parallel HEAD requests (default: 10)
 *   --rps         Max requests per second to the CDN (default: 20)
 *   --output      Output CSV filename (default: missing-images-<timestamp>.csv)
 *
 * Output CSV columns:
 *   type, language, setId, setName, assetId, assetLocalId, assetName, imageUrl, httpStatus
 *
 * "type" is one of: card | symbol | logo
 */

import { createWriteStream } from 'fs';

// ---------------------------------------------------------------------------
// CLI args
// ---------------------------------------------------------------------------
const args = process.argv.slice(2);
const getArg = (flag, def) => {
  const i = args.indexOf(flag);
  return i !== -1 && args[i + 1] ? args[i + 1] : def;
};

const QUALITY     = getArg('--quality', 'high');
const EXT         = getArg('--ext', 'webp');
const CONCURRENCY = parseInt(getArg('--concurrency', '10'), 10);
const RPS         = parseInt(getArg('--rps', '20'), 10);
const OUTPUT      = getArg('--output', `missing-images-${Date.now()}.csv`);

const LANG_ARG    = getArg('--lang', 'all');
const SET_ARG     = getArg('--set', 'all');

const ALL_LANGUAGES = ['en', 'fr', 'de', 'es', 'it', 'pt', 'zh-hans', 'zh-hant', 'ja', 'ko'];
const LANGUAGES     = LANG_ARG === 'all' ? ALL_LANGUAGES : LANG_ARG.split(',').map(s => s.trim());
const SET_FILTER    = SET_ARG === 'all' ? null : new Set(SET_ARG.split(',').map(s => s.trim().toLowerCase()));

const API_BASE = 'https://api.tcgdex.net/v2';

// ---------------------------------------------------------------------------
// Rate limiter — drains a queue at a fixed RPS so the CDN is never hammered.
// Concurrency controls parallelism; RPS caps the sustained request rate.
// ---------------------------------------------------------------------------
class RateLimiter {
  constructor(rps) {
    this.intervalMs = 1000 / rps;
    this.lastTime = 0;
    this.queue = [];
    this.draining = false;
  }

  acquire() {
    return new Promise(resolve => {
      this.queue.push(resolve);
      if (!this.draining) this._drain();
    });
  }

  async _drain() {
    this.draining = true;
    while (this.queue.length > 0) {
      const now = Date.now();
      const wait = Math.max(0, this.intervalMs - (now - this.lastTime));
      if (wait > 0) await sleep(wait);
      this.lastTime = Date.now();
      this.queue.shift()();
    }
    this.draining = false;
  }
}

const limiter = new RateLimiter(RPS);

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------
function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function httpHead(url) {
  await limiter.acquire();
  try {
    const res = await fetch(url, {
      method: 'HEAD',
      headers: { 'User-Agent': 'tcgdex-missing-images-report/1.0' },
    });
    return res.status;
  } catch {
    return 0;
  }
}

async function fetchJson(url) {
  try {
    const res = await fetch(url, {
      headers: { 'User-Agent': 'tcgdex-missing-images-report/1.0' },
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------
// Concurrency pool
// ---------------------------------------------------------------------------
async function pool(tasks, concurrency) {
  let i = 0;
  async function worker() {
    while (i < tasks.length) await tasks[i++]();
  }
  await Promise.all(Array.from({ length: Math.min(concurrency, tasks.length) }, worker));
}

// ---------------------------------------------------------------------------
// CSV
// ---------------------------------------------------------------------------
function csvRow(...fields) {
  return fields.map(f => `"${String(f ?? '').replace(/"/g, '""')}"`).join(',') + '\n';
}

// ---------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------
let totalChecked = 0;
let totalMissing = 0;

function progress(label) {
  process.stdout.write(`\r\x1b[K  ${label} | checked: ${totalChecked} | missing: ${totalMissing}`);
}

// ---------------------------------------------------------------------------
// Check a single asset, write to CSV only if missing
// ---------------------------------------------------------------------------
async function checkAsset(out, { type, lang, setId, setName, assetId, assetLocalId, assetName, imageUrl }) {
  const status = await httpHead(imageUrl);
  totalChecked++;
  if (status !== 200) {
    totalMissing++;
    out.write(csvRow(type, lang, setId, setName, assetId, assetLocalId, assetName, imageUrl, status));
  }
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function main() {
  console.log('TCGDex Missing Images Report');
  console.log(`  Languages   : ${LANGUAGES.join(', ')}`);
  console.log(`  Sets        : ${SET_FILTER ? [...SET_FILTER].join(', ') : 'all'}`);
  console.log(`  Quality     : ${QUALITY} | Extension: ${EXT}`);
  console.log(`  Concurrency : ${CONCURRENCY} | Max RPS: ${RPS}`);
  console.log(`  Output      : ${OUTPUT}\n`);

  const out = createWriteStream(OUTPUT);
  out.write(csvRow('type', 'language', 'setId', 'setName', 'assetId', 'assetLocalId', 'assetName', 'imageUrl', 'httpStatus'));

  // Symbols are language-agnostic (served from /univ/) — check each set once only.
  // Logos can vary by language — track per lang/setId pair.
  const checkedSymbols = new Set();
  const checkedLogos   = new Set();

  for (const lang of LANGUAGES) {
    process.stdout.write(`\n[${lang}] Fetching sets list...`);

    const sets = await fetchJson(`${API_BASE}/${lang}/sets`);
    if (!sets || !Array.isArray(sets)) {
      console.log(' skipped (no data)');
      continue;
    }

    const filteredSets = SET_FILTER
      ? sets.filter(s => SET_FILTER.has(s.id.toLowerCase()))
      : sets;

    console.log(` ${filteredSets.length} sets${SET_FILTER ? ` (filtered from ${sets.length})` : ''}`);

    for (const setBrief of filteredSets) {
      const setId = setBrief.id;
      process.stdout.write(`\n  [${setId}] Fetching...`);

      const setData = await fetchJson(`${API_BASE}/${lang}/sets/${setId}`);
      if (!setData) {
        console.log(' skipped (no data)');
        continue;
      }

      const setName = setData.name ?? setId;
      const cards   = Array.isArray(setData.cards) ? setData.cards : [];
      console.log(` ${cards.length} cards`);

      const tasks = [];

      // -- Symbol (once per set, language-agnostic) --
      if (!checkedSymbols.has(setId)) {
        checkedSymbols.add(setId);
        const symbolBase = setData.symbol ?? setBrief.symbol ?? null;
        if (symbolBase) {
          tasks.push(() => {
            progress(`${setId}/symbol`);
            return checkAsset(out, {
              type: 'symbol',
              lang: 'univ',
              setId,
              setName,
              assetId: `${setId}-symbol`,
              assetLocalId: 'symbol',
              assetName: `${setName} Symbol`,
              imageUrl: `${symbolBase}.${EXT}`,
            });
          });
        }
      }

      // -- Logo (once per lang/set pair) --
      const logoKey = `${lang}/${setId}`;
      if (!checkedLogos.has(logoKey)) {
        checkedLogos.add(logoKey);
        const logoBase = setData.logo ?? setBrief.logo ?? null;
        if (logoBase) {
          tasks.push(() => {
            progress(`${lang}/${setId}/logo`);
            return checkAsset(out, {
              type: 'logo',
              lang,
              setId,
              setName,
              assetId: `${setId}-logo`,
              assetLocalId: 'logo',
              assetName: `${setName} Logo`,
              imageUrl: `${logoBase}.${EXT}`,
            });
          });
        }
      }

      // -- Cards --
      for (const card of cards) {
        if (!card.image) {
          // No image field at all — record immediately without a HEAD request
          totalChecked++;
          totalMissing++;
          out.write(csvRow('card', lang, setId, setName, card.id, card.localId, card.name ?? '', '(no image field)', 'N/A'));
          continue;
        }

        const imageUrl = `${card.image}/${QUALITY}.${EXT}`;
        tasks.push(() => {
          progress(`${lang}/${setId}/${card.localId}`);
          return checkAsset(out, {
            type: 'card',
            lang,
            setId,
            setName,
            assetId: card.id,
            assetLocalId: card.localId,
            assetName: card.name ?? '',
            imageUrl,
          });
        });
      }

      await pool(tasks, CONCURRENCY);
    }
  }

  out.end();
  process.stdout.write('\n\n');
  console.log('Done!');
  console.log(`  Total assets checked : ${totalChecked}`);
  console.log(`  Missing              : ${totalMissing}`);
  console.log(`  Report saved to      : ${OUTPUT}`);
}

main().catch(err => {
  console.error('\nFatal error:', err);
  process.exit(1);
});