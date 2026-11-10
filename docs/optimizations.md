# Measured optimizations (Nov 9, 2026)

Each change was measured on real data. The methods are below so the numbers can be
checked or re-run.

## 1. Model accuracy: baseline-v1 → baseline-v2

On 37,572 held-out predictions from real 2025-26 games:

- **Brier score:** 0.2405 → 0.2371 (−1.4%).
- **Calibration error:** 4.6 → 0.8 points (−82%).
- **Against the player's season-to-date over rate (0.2494):** 4.9% lower Brier.

Full method in [model-tuning.md](model-tuning.md).

## 2. Analysis cache: reuse until a price moves 3 points

Before, the cache key held each market price rounded to the cent, so any 1-cent move
started a new LLM call. Now a stored analysis is reused until a market it used moves 3
points or more.

**How it was measured:** minute-by-minute Polymarket price history (CLOB
`prices-history`) for 137 prop lines (points, rebounds, assists) of 52 players in four
games on Nov 9, 2026, over the 8 hours before tip-off. The test replays one analysis
request per player at a fixed interval.

| Request every | Reuse, exact cent (before) | Reuse, 3-point band (now) | LLM calls saved |
|---|---|---|---|
| 15 min | 21% | 64% | 54% (1,125 → 517) |
| 30 min | 13% | 52% | 45% (621 → 344) |
| 60 min | 8% | 40% | 35% (332 → 215) |

When an analysis was reused, the largest price drift since it was written averaged
1.3–1.4 points.

## 3. LLM input tokens per analysis

The package now sends the same facts with less repetition:

- the player's form once instead of once per game;
- the last 10 games as a table instead of repeated keys;
- empty fields dropped and floats rounded to 3 decimals;
- per-minute rates left out, since they follow from the averages and minutes.

**How it was measured:** request tokens (instructions + schema + package) for 40
players, counted with the o200k_base tokenizer. That is OpenAI's current public
encoding, used as a stand-in because GPT-6's tokenizer is not public.

| | Package | Whole request |
|---|---|---|
| Before | 2,545 | 3,103 |
| After | 1,390 | 1,969 |
| Change | −45% | −37% |

## 4. Polymarket discovery

Discovery now skips games that have already tipped off, and writes only markets whose
matching or state changed. Prices live in the price cache, not the market table.

**How it was measured:** the real Gamma API, two runs 10 minutes apart on a game
night (Nov 9, 2026, 10:15 pm ET).

| Per run | Before | After |
|---|---|---|
| Data downloaded (uncompressed) | 17.3 MB | 2.9 MB (−84%) |
| Data over the wire (gzip) | 1.67 MB | 0.28 MB (−83%) |
| Market rows written | 2,602 | 0 (nothing had changed) |

At 9:30 pm ET the download saving was 73% (17.3 → 4.7 MB). Before any game starts, the
download saving is small. Most of the saving comes once games tip off.
