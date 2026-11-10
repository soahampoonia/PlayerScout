# Tuning the baseline model (baseline-v1 → baseline-v2)

## Data

Real NBA box scores from stats.nba.com (`leaguegamelog`, regular season), 2024-25
(26,306 player lines, used only as the early-season prior) and 2025-26 (26,651 lines).

A prediction is scored for every 2025-26 player game where the player already had at
least 10 games that season, using only games before it. There is no history of real
prop lines, so each game gets a stand-in line: the player's season-to-date average,
rounded down to the nearest .5. That puts the line close to the player's level, which
is the hard case, and it is the line a bettor would see for most players.

- **Tuning set:** games before Jan 15, 2026 (24,522 predictions).
- **Held-out set:** Jan 15 – Apr 12, 2026 (37,572 predictions: 12,758 points, 12,712
  rebounds, 12,102 assists). Settings were chosen on the tuning set only.

The search was run with a JavaScript port of `predictions/backtest.py`. Before it was
used, the port was checked against the Python code on the same games, and every
reported number matched to 4 decimals.

## What changed

baseline-v1 was overconfident in the over. When it said 65%, the over happened 58% of
the time, because points, rebounds and assists are skewed: the typical game sits a
little below the average one. baseline-v2 adds three things:

- **Center below the mean.** Probabilities are measured from `mean − shift × sd`, with
  shift between 0.05 and 0.10.
- **Wider spread.** The player's standard deviation is multiplied by 1.2–1.3.
- **Per-stat weights.** Points and rebounds lean on a last-20-games window. Rebounds
  and assists put more weight on the season rate.

The exact numbers are in `PARAMS` in `predictions/model.py`.

A count model (negative binomial) was also tried in place of the normal curve. It
scored worse, so it was dropped.

## Results on the held-out set

| | Brier (lower is better) | Calibration error |
|---|---|---|
| Always 50% | 0.2500 | — |
| Season-to-date over rate | 0.2494 | — |
| baseline-v1 | 0.2405 | 4.6 points |
| **baseline-v2** | **0.2371** | **0.8 points** |

By stat (Brier):

| | v1 | v2 | Change |
|---|---|---|---|
| Points | 0.2415 | 0.2399 | −0.7% |
| Rebounds | 0.2419 | 0.2375 | −1.8% |
| Assists | 0.2379 | 0.2336 | −1.8% |

Calibration error is the average gap between the predicted probability and how often
the over actually happened, across 10-point buckets, weighted by the number of
predictions in each bucket.

On these near-average lines, a Brier score close to 0.25 is expected: the outcome is
close to a coin flip. The useful results are that v2 beats both simple baselines and
v1, and that its probabilities are honest: when it says 55%, the over happens about 55%
of the time.

To re-run the backtest on your own data:

```
docker compose exec api python manage.py backtest_model --season 2025-26
```
