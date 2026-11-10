# Player Scout

NBA player analysis that puts three opinions side by side: a stats model built on
NBA box scores, live crowd prices from Polymarket, and an LLM (GPT-6 Luna) that reads
both and explains where they agree and why they differ.

![Player Scout: live scores, Polymarket lines, model vs market, and an analysis](docs/demo.gif)

<sub>Recorded on sample data (made-up games and prices) with the offline demo provider in place of the LLM.</sub>

## What it does

- **Live games.** Today's games with scores, quarter and clock pushed over a WebSocket
  as they change, and each team's Polymarket win probability with a 24-hour trend line.
- **Search** for any player or team from the bar at the top: letter matching, case and
  accents ignored.
- **Game pages** with the moneyline, main spread and total, and every player's main
  points / rebounds / assists line with the chance of going over.
- **Player pages** with season averages, a last-10-games chart, and a model-vs-market
  table: the baseline model's chance of going over each Polymarket line, the market's,
  and the gap between them.
- **Analysis.** One click sends the player's recent games, the model's inputs and both
  sets of probabilities to GPT-6 Luna, which returns a summary, an outlook, and its own
  probability for each line with reasons. Answers are checked against a strict schema,
  cached until the inputs change, and kept in the **History** tab.

## Stack

Django 5 + Django REST Framework, Channels (WebSocket), Celery + beat, PostgreSQL 16,
Redis, React 19 + TypeScript (Vite, TanStack Query), Docker Compose. Data from
[`nba_api`](https://github.com/swar/nba_api) (stats.nba.com and the NBA live feed) and
Polymarket's public Gamma and CLOB APIs. LLM through the OpenAI Responses API.

## Run it

You need Docker Desktop (or Docker Engine with the Compose plugin) and, for the
analysis, an OpenAI API key.

```
cp .env.example .env        # then put your key in OPENAI_API_KEY
docker compose up --build
```

Then open http://localhost:5173.

On first start the API container applies the migrations, loads the 30 teams and every
player name from data bundled with `nba_api` (no network needed), and queues the first
sync. The worker then pulls rosters, the schedule, team stats and two seasons of box
scores from stats.nba.com. The home screen shows the progress and fills in when it is
done. Search works as soon as the page loads.

After that the data refreshes by itself every night (Eastern time): box scores 4:00,
team stats 4:30, rosters 6:00, schedule 6:10.

## Live updates

While games are on, `live_scoreboard` reads the NBA's live feed every 15 seconds
(only from 10 minutes before the first tip-off until the last game ends; it makes no
requests otherwise) and updates each game's score, period and clock. About 15 minutes
after a game goes final, that season's box scores are synced again.

Changes reach the browser over one WebSocket (`/ws/live`, Django Channels on Redis):
scores as they change, and Polymarket prices when they move a point or more. Scores
light up briefly when they change. Each Live card also shows the home team's win
probability over the last day as a small line. If the socket drops, the pages poll the
API until it reconnects.

## The baseline model

For each player and upcoming game, `predictions/features.py` gathers his last 5, last 10,
last 20 and season averages, per-minute rates, usage, minutes trend, rest days, home/away, and
the opponent's defensive rating, pace and allowed rebounds/assists. The inputs are
stored as a `feature_snapshot`, reused until something changes. `predictions/model.py`
(baseline-v2) turns them into a projected mean and spread per stat, and a chance of
going over any line. The rules are written out at the top of that file.

baseline-v2 was tuned on real 2025-26 box scores and checked on games it was not tuned
on (37,572 predictions, Jan 15 – Apr 12, 2026). It has a Brier score of 0.2371, against
0.2405 for baseline-v1 and 0.2494 for the player's season-to-date over rate, and a
calibration error of 0.8 points, against 4.6. The method and the numbers are in
[docs/model-tuning.md](docs/model-tuning.md).

The Player page shows the projection for each of the next 3 games and, for each
Polymarket line, the market's chance, the model's chance and the edge (model minus
market, in points). Edges of 5 points or more on markets that are not thin are
highlighted. Injuries are not in the model yet (there is no injury source).

To see how honest the model's probabilities are on real games, replay it over last
season using only the games before each one:

```
docker compose exec api python manage.py backtest_model --season 2025-26
```

It prints Brier scores (lower is better; always saying 50% scores 0.250) next to a
simple baseline, and a calibration table.

## Analysis (GPT-6 Luna)

Put your OpenAI key in `.env` as `OPENAI_API_KEY` and restart with `docker compose up -d`.
`.env` is git-ignored: never put the key anywhere else.

Press **Analyze** on a player's page (or next to a player on a game page). The worker
builds one JSON package for his next 3 games: his last 10 games, the model's inputs and,
for points, rebounds and assists in each game, a target line. That is Polymarket's main
line, or the model's own line where there is no market. It sends the package to
`gpt-6-luna` through the Responses API with a strict JSON schema. The answer is checked:
only lines that were asked about, probabilities between 0 and 1, real game ids. It is
stored and pushed to the page, usually within a minute. The prompt is in
`predictions/analysis.py`.

- **Cache:** asking again returns the stored analysis while its inputs are the same
  (same games, lines and model inputs) and no market price it used has moved 3 points
  or more. **Refresh** forces a new one. Replayed on real Polymarket price history for
  52 players, with an analysis asked for every 30 minutes before tip-off, this reuses
  52% of requests, against 13% when prices had to match to the cent.
- **History** lists every analysis, newest first, with a per-player filter.
- **Cost** is shown under each analysis, from the token counts and the list prices in
  settings (`LLM_PRICE_*`). The request is about 2,000 input tokens: the package is
  compact JSON (form sent once, the game log as a table, empty fields dropped), 37%
  smaller than the first version. Output and reasoning tokens depend on the effort
  setting.
- `LLM_PROVIDER=fake` gives canned answers, labelled as fake, without a key.

## Polymarket

Market data is public, so there is nothing to sign up for.

- **Discovery** (`discover_markets`, every 10 minutes, and once after the first NBA
  sync) reads Polymarket's open NBA events that have not tipped off yet, up to 8 days
  ahead. Games already under way are skipped: the stream keeps their prices, and on a
  game night they are most of the download. It keeps
  the moneyline, spreads, totals, and player points / rebounds / assists markets. Each
  is tied to our game (Eastern date + both teams) and, for props, to a player on one
  of the two rosters by name.
- **Prices** come from the `market_stream` service, which follows every matched
  market for games in the next 36 hours over Polymarket's WebSocket. If it is not
  running, `poll_midpoints` fetches prices over REST every minute instead. Markets
  further out get the prices discovery reads.
- The **probability** shown is the order-book midpoint, or the last trade when the
  spread is wider than 10 cents (the rule Polymarket's own site uses).
- A **thin** market (spread over 5 cents, or under $100 traded in 24 hours) is shown
  greyed out with a label. Both numbers are settings (`MARKET_THIN_SPREAD`,
  `MARKET_THIN_MIN_VOLUME_24H`); most player props are thin by volume.
- **History**: a snapshot is saved when a price moves 1 point, and otherwise every 10
  minutes (the design said every 60 seconds; with about 2,000 markets that would be
  nearly 3 million rows a day). Snapshots older than 30 days are deleted nightly,
  except ones an analysis refers to.

**Names that do not match.** A prop whose player name does not clearly match a
roster player (for example a nickname with a shared last name) is kept out of the
app. To see and fix those, create an admin login once:

```
docker compose exec api python manage.py createsuperuser
```

then open http://localhost:8000/admin/markets/market/?match_method__exact=unmatched,
open the market, and set its player. That market is then marked manual and discovery
leaves it alone. For a name Polymarket always spells differently, add the spelling to
the player's `name_aliases` instead.

## If the NBA sync fails

stats.nba.com refuses many VPN, cloud and data-centre addresses. From a home
connection it normally works. If the home screen reports a failed sync:

1. Turn off any VPN and run the sync again (below).
2. Or copy `.env.example` to `.env`, set `NBA_PROXY` to an HTTP proxy the NBA accepts,
   and restart with `docker compose up -d`.

## Commands

```
# Run a sync now, in the foreground: reference | schedule | team_stats | game_logs | all
docker compose exec api python manage.py sync all

# Find Polymarket markets now instead of waiting for the 10-minute tick
docker compose exec api python manage.py shell -c "from markets.discovery import discover_markets; print(discover_markets())"

# Watch the price stream
docker compose logs -f market_stream

# Backend tests (uses a separate test database)
docker compose exec api pytest

# Frontend tests and type-check
docker compose exec frontend npm test
docker compose exec frontend npm run typecheck

# Database shell
docker compose exec db psql -U scout playerscout

# What each sync did, and when
curl http://localhost:8000/api/status

# Stop. Add -v to also delete the database.
docker compose down
```

Code changes reload by themselves in the API and the frontend. The worker does not
reload: after changing task code, run `docker compose restart worker`.

## Layout

```
docker-compose.yml
.env.example       settings; copy to .env (git-ignored) and add your OpenAI key
docs/demo.gif      the README animation
docs/model-tuning.md  how baseline-v2 was tuned and tested
docs/optimizations.md measured gains: model accuracy, cache reuse, tokens, discovery
backend/
  config/            settings, Celery app, ASGI and URL routing
  nba/               teams, players, games, box scores, team stats
    ingest/          client.py (the only file that talks to the NBA) and one module per job
    api.py           the REST endpoints
    textnorm.py      search-name normalisation (mirrored in frontend/src/search.ts)
    tests/
  markets/           Polymarket: polymarket.py (the only file that talks to it),
                     discovery.py (find + match), stream.py (WebSocket), pricing.py
  predictions/       features.py, model.py (baseline-v2), backtest, llm.py (the LLM adapter),
                     analysis.py (prompt, schema, run_analysis), api.py (analyses)
  ops/               watchlist and the sync run log
  realtime/          the /ws/live WebSocket and push helpers
frontend/
  src/
    pages/           Live, Game, Player, Team, History
    components/      search box, game card, chart, analysis card, live socket bridge
    search.ts        search matching, done in the browser
```

## API

| Endpoint | Returns |
|---|---|
| `GET /api/games?date=YYYY-MM-DD` | Games on a date (Eastern time; default today) with Polymarket win probability, plus the previous and next game days |
| `GET /api/games/{id}` | One game with both teams' players (box lines once played, otherwise rosters with season averages and main prop lines), plus moneyline, main spread and main total |
| `GET /api/players/{id}` | Player, season averages, last 10 games, next 3 games |
| `GET /api/players/{id}/markets` | The player's next 3 games: model projection per stat, and every Polymarket prop line with market chance, model chance and edge, main line flagged |
| `GET /api/teams/{id}` | Team, record, team numbers, roster, recent and upcoming games |
| `GET /api/search-index` | Every team and active player, loaded once by the browser |
| `POST /api/players/{id}/analyses` | Body `{"force": false}`. 200 with a stored analysis when the inputs are unchanged, or 202 with a new one being written. 503 without an LLM key |
| `GET /api/analyses/{id}` | One analysis: summary, outlook, and per game and stat the line, model %, market %, analyst %, confidence and reasons |
| `GET /api/analyses?player_id=&before=&limit=` | History, newest first; `before` is the last item's `created_at` |
| `GET /api/status` | Row counts, the last run of each sync job, the price stream's heartbeat, and the live scoreboard's last poll |
| `WS /ws/live` | Pushes: `analysis.done` when an analysis finishes or fails; `game.update` (score, period, clock, status) to everyone; `market.update` for moneylines to everyone, and for a game's or player's markets to pages that subscribed with `{"subscribe": {"games": [...], "players": [...]}}` |

## Settings

Every setting has a default, so `.env` is optional. See `.env.example` for the list:
ports, database credentials, the season override, how many seasons of box scores to
keep, and the NBA proxy.

## Roadmap

| Step | What | State |
|---|---|---|
| 1 | Docker Compose stack: Postgres, Redis, Django, Celery worker, Celery beat, React | Done |
| 2 | NBA ingestion, search, Player and Team pages, the full 14-table schema | Done |
| 3 | Polymarket discovery, matching, price stream (`market_stream` service) | Done |
| 4 | Live scoreboard over WebSocket | Done |
| 5 | Features and baseline model | Done |
| 6 | LLM adapter, analysis, cache, History tab | Done |
| 7 | Locking, grading, track record | Next |
| 8 | Stale detection, watchlist, auto-refresh | |

The design notes for every step are in the "Player Scout v2 — System Design" doc.
