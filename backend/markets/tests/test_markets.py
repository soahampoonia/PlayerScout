import asyncio
import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from markets import discovery, polymarket, pricing, tasks
from markets.discovery import apply_events, match_player, sync_teams
from markets.models import Market, MarketSnapshot
from markets.pricing import latest_prices, record_prices
from markets.stream import MarketStream, PriceCollector, wanted_markets
from nba.ingest.reference import seed_static
from nba.models import Game, Player, Team
from ops.models import IngestionRun

pytestmark = pytest.mark.django_db

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "gamma_events.json").read_text())
LUKA, JALEN, NAW, KESSLER, GIANNIS = 1629029, 1630552, 1629638, 1631117, 203507
ML, SPREAD_25, TOTAL_235, LUKA_PTS, NAW_PTS, KESSLER_REB, LUKA_AST = (
    "6428643", "6890446", "6890449", "6892670", "6892707", "6892744", "6892816",
)  # fmt: skip
NOW = datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc).timestamp()  # 4 pm Eastern, three hours before tip-off


def event(name="open"):
    return copy.deepcopy(FIXTURES[name])


@pytest.fixture
def slate(db, monkeypatch):
    """LAL @ ATL tonight (Nov 9, 7 pm ET) and MIA @ SAC last night, with the fixture's players on the rosters."""
    seed_static()
    t = {abbr: Team.objects.get(abbreviation=abbr) for abbr in ("LAL", "ATL", "MIA", "SAC")}
    Player.objects.filter(pk=LUKA).update(team=t["LAL"], is_active=True)
    Player.objects.filter(pk__in=[JALEN, NAW, KESSLER]).update(team=t["ATL"], is_active=True)
    Player.objects.filter(pk=GIANNIS).update(team=t["MIA"], is_active=True)
    tonight = Game.objects.create(
        id="0022600150", season="2026-27", game_date=date(2026, 11, 9), tipoff_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
        home_team=t["ATL"], away_team=t["LAL"],
    )  # fmt: skip
    last_night = Game.objects.create(
        id="0022600140", season="2026-27", game_date=date(2026, 11, 8), tipoff_at=datetime(2026, 11, 9, 2, tzinfo=timezone.utc),
        home_team=t["SAC"], away_team=t["MIA"], status="final", home_score=101, away_score=110,
    )  # fmt: skip
    for module in ("nba.api", "nba.seasons"):
        monkeypatch.setattr(f"{module}.today_et", lambda: date(2026, 11, 9))
    monkeypatch.setattr("markets.pricing.time.time", lambda: NOW)
    return {"tonight": tonight, "last_night": last_night, "teams": t}


# ---- Matching ----------------------------------------------------------------


def test_sync_teams_maps_polymarket_ids_by_abbreviation(slate):
    assert sync_teams(FIXTURES["teams_sample"]) == 4  # the All-Star "crs" team is ignored
    assert Team.objects.get(abbreviation="LAL").polymarket_team_id == "100515"
    assert sync_teams(FIXTURES["teams_sample"]) == 0


def test_open_event_markets_are_stored_and_matched(slate):
    written, seen = apply_events([event()])
    assert seen == {"1284256"}
    assert written == 11  # 1 moneyline, 2 spreads, 2 totals, 2 points, 2 rebounds, 2 assists; 1H spread and threes skipped
    assert not Market.objects.filter(id__in=["6904394", "6898012"]).exists()

    lal = slate["teams"]["LAL"]
    ml = Market.objects.get(pk=ML)
    assert (ml.market_type, ml.game_id, ml.team_id, ml.match_method, float(ml.match_confidence)) == (
        "moneyline", "0022600150", lal.id, "team_date", 1.0,
    )  # fmt: skip
    assert ml.yes_token_id.startswith("670958930") and ml.no_token_id.startswith("171192187")
    assert ml.raw["sportsMarketType"] == "moneyline" and ml.event_slug == "nba-lal-atl-2026-11-09"

    spread = Market.objects.get(pk=SPREAD_25)
    assert (spread.team_id, float(spread.line)) == (lal.id, -2.5)
    total = Market.objects.get(pk=TOTAL_235)
    assert (total.team_id, float(total.line), total.stat) == (None, 235.5, None)

    luka = Market.objects.get(pk=LUKA_PTS)
    assert (luka.player_id, luka.team_id, luka.stat, float(luka.line), luka.match_method, float(luka.match_confidence)) == (
        LUKA, lal.id, "pts", 34.5, "roster_name", 1.0,
    )  # fmt: skip
    assert Market.objects.get(pk=NAW_PTS).player_id == NAW  # hyphenated name
    assert Market.objects.get(pk=KESSLER_REB).stat == "reb"
    assert Market.objects.get(pk=LUKA_AST).stat == "ast"

    # Running discovery again: a price change writes nothing (prices live in the cache),
    # a change in the market itself writes just that market.
    again = event()
    again["markets"][0]["bestBid"], again["markets"][0]["bestAsk"] = 0.5, 0.52
    assert apply_events([again])[0] == 0
    assert latest_prices([ML])[ML]["prob"] == 0.475  # the stream's fresher price is kept
    again["markets"][1]["closed"] = True
    assert apply_events([again])[0] == 1
    assert Market.objects.get(pk=SPREAD_25).closed is True
    assert Market.objects.count() == 11


def test_gamma_prices_seed_the_latest_price(slate):
    apply_events([event()])
    prices = latest_prices([ML, KESSLER_REB, LUKA_PTS, NAW_PTS])
    assert prices[ML]["prob"] == 0.475 and prices[ML]["is_thin"] is False
    # 10 cents or wider: the last trade stands in for the midpoint, and the market is thin.
    assert prices[KESSLER_REB]["prob"] == 0.55 and prices[KESSLER_REB]["is_thin"] is True
    assert prices[LUKA_PTS]["prob"] == 0.565
    assert prices[NAW_PTS]["is_thin"] is True  # $9 traded in 24 h
    assert MarketSnapshot.objects.count() == 11


def test_game_matching_tolerates_swapped_sides_and_a_day_off(slate):
    swapped = event()
    for team in swapped["teams"]:
        team["ordering"] = "home" if team["ordering"] == "away" else "away"
    apply_events([swapped])
    assert float(Market.objects.get(pk=ML).match_confidence) == 0.9

    shifted = event()
    shifted["eventDate"] = "2026-11-10"
    apply_events([shifted])
    assert float(Market.objects.get(pk=ML).match_confidence) == 0.8

    unknown = event()
    unknown["eventDate"] = "2026-12-25"
    apply_events([unknown])
    ml, luka = Market.objects.get(pk=ML), Market.objects.get(pk=LUKA_PTS)
    assert (ml.game_id, ml.match_method, luka.player_id, luka.match_method) == (None, "unmatched", None, "unmatched")


def test_side_events_are_ignored(slate):
    side = event()
    side["slug"] += "-exact-margin"
    assert apply_events([side]) == (0, set())


def test_player_not_on_either_roster_is_matched_with_lower_confidence(slate):
    Player.objects.filter(pk=LUKA).update(team=slate["teams"]["MIA"])  # roster sync has not caught up with a trade
    apply_events([event()])
    luka = Market.objects.get(pk=LUKA_PTS)
    assert luka.player_id == LUKA and float(luka.match_confidence) == 0.8


def test_manual_matches_survive_rediscovery(slate):
    apply_events([event()])
    Market.objects.filter(pk=LUKA_PTS).update(player_id=JALEN, match_method="manual", match_confidence=1)
    apply_events([event()])
    luka = Market.objects.get(pk=LUKA_PTS)
    assert (luka.player_id, luka.match_method) == (JALEN, "manual")


def test_match_player_rules():
    def player(pk, full, aliases=()):
        from nba.textnorm import search_key

        first, last = full.split(" ", 1)
        return Player(id=pk, first_name=first, last_name=last, full_name=full, search_name=search_key(full), name_aliases=list(aliases))

    roster = [
        player(1, "Herbert Jones"), player(2, "Jabari Smith Jr."), player(3, "Day'Ron Sharpe"),
        player(4, "Moritz Wagner", aliases=["Moe Wagner"]), player(5, "Franz Wagner"), player(6, "Luka Dončić"),
        player(7, "Tre Jones"),
    ]  # fmt: skip
    assert match_player("Luka Doncic", roster) == (roster[5], 1.0)
    assert match_player("Moe Wagner", roster) == (roster[3], 1.0)  # alias
    assert match_player("Jabari Smith", roster) == (roster[1], 0.95)  # suffix dropped
    assert match_player("Herb Jones", roster) == (roster[0], 0.85)  # nickname
    assert match_player("Emanuel Sharpe", roster) == (roster[2], 0.7)  # last name only: kept out of the app
    assert match_player("Wagner", roster) == (None, 0.0)  # two Wagners
    assert match_player("Nobody Atall", roster) == (None, 0.0)


def test_closed_event_records_the_result(slate):
    apply_events([event("closed")])
    ml, giannis = Market.objects.get(pk="6428639"), Market.objects.get(pk="6829655")
    assert (ml.closed, ml.active, ml.resolved_outcome, ml.game_id) == (True, False, "yes", "0022600140")
    assert (giannis.resolved_outcome, giannis.player_id) == ("no", GIANNIS)
    assert MarketSnapshot.objects.count() == 0  # closed markets are not priced


# ---- Prices ------------------------------------------------------------------


@pytest.fixture
def market(slate):
    apply_events([event()])
    cache.clear()
    MarketSnapshot.objects.all().delete()
    return Market.objects.get(pk=ML)


def test_snapshots_on_a_one_point_move_or_after_the_heartbeat(market, settings):
    settings.MARKET_SNAPSHOT_HEARTBEAT = 600
    t = NOW
    assert record_prices([{"market_id": ML, "bid": 0.47, "ask": 0.48}], now=t) == []
    assert MarketSnapshot.objects.count() == 1
    record_prices([{"market_id": ML, "bid": 0.475, "ask": 0.48}], now=t + 5)  # 0.25 points: cache only
    assert MarketSnapshot.objects.count() == 1
    assert latest_prices([ML])[ML]["prob"] == 0.4775
    assert record_prices([{"market_id": ML, "bid": 0.48, "ask": 0.49}], now=t + 10) == [ML]  # 1 point
    assert MarketSnapshot.objects.count() == 2
    record_prices([{"market_id": ML, "last": 0.6}], now=t + 700)  # no move in the midpoint, but the heartbeat is due
    snap = MarketSnapshot.objects.order_by("-captured_at").first()
    assert MarketSnapshot.objects.count() == 3 and float(snap.implied_prob) == 0.485 and float(snap.last_trade) == 0.6


def test_gamma_seed_does_not_overwrite_a_fresh_stream_price(market):
    record_prices([{"market_id": ML, "bid": 0.6, "ask": 0.61}], now=NOW)
    record_prices([{"market_id": ML, "bid": 0.47, "ask": 0.48, "volume_24h": 5000}], now=NOW + 30, seed=True)
    price = latest_prices([ML])[ML]
    assert (price["prob"], price["volume_24h"]) == (0.605, 5000)
    record_prices([{"market_id": ML, "bid": 0.47, "ask": 0.48}], now=NOW + 300, seed=True)  # stream silent for 5 min
    assert latest_prices([ML])[ML]["prob"] == 0.475


def test_latest_price_falls_back_to_the_newest_snapshot(market):
    record_prices([{"market_id": ML, "bid": 0.3, "ask": 0.32}], now=NOW)
    cache.clear()  # Redis restarted
    assert latest_prices([ML])[ML]["prob"] == 0.31
    assert latest_prices(["nope"]) == {}


# ---- Stream ------------------------------------------------------------------

YES = "67095893048026221978740664541566477971165824705586991971047705217950439543434"
NO = "17119218746517541967526495761674893601409625825234875646674373036312728181969"
BOOK = {
    "market": "0x3fc1", "asset_id": YES, "timestamp": "1794259031626", "hash": "79e8",
    "bids": [{"price": "0.01", "size": "168054.96"}, {"price": "0.46", "size": "64369.67"}, {"price": "0.47", "size": "3379.07"}],
    "asks": [{"price": "0.99", "size": "150643.87"}, {"price": "0.49", "size": "10"}, {"price": "0.48", "size": "2000"}],
}  # fmt: skip
PRICE_CHANGE = {
    "market": "0x3fc1", "event_type": "price_change", "timestamp": "1794259032156",
    "price_changes": [
        {"asset_id": YES, "price": "0.43", "size": "29193.78", "side": "BUY", "hash": "3ff2", "best_bid": "0.48", "best_ask": "0.49"},
        {"asset_id": NO, "price": "0.57", "size": "29193.78", "side": "SELL", "hash": "b797", "best_bid": "0.51", "best_ask": "0.52"},
    ],
}  # fmt: skip


def test_collector_reads_the_recorded_message_shapes():
    collector = PriceCollector()
    collector.tokens = {YES: ML}
    collector.handle(json.dumps([BOOK]))  # first frame after subscribing: a list of books
    assert collector.pending[ML] == {"market_id": ML, "bid": 0.47, "ask": 0.48}
    collector.handle(json.dumps(PRICE_CHANGE))  # carries the NO token too, which is ignored
    collector.handle(json.dumps({"event_type": "last_trade_price", "asset_id": YES, "price": "0.48", "size": "6.25"}))
    collector.handle("PONG")
    collector.handle("not json")
    updates, resolved = collector.take()
    assert updates == [{"market_id": ML, "bid": "0.48", "ask": "0.49", "last": "0.48"}] and resolved == {}
    assert collector.pending == {}

    collector.handle(json.dumps({"event_type": "best_bid_ask", "asset_id": YES, "best_bid": "0.5", "best_ask": "0.51", "spread": "0.01"}))
    collector.handle(json.dumps({"event_type": "market_resolved", "assets_ids": [YES, NO], "winning_asset_id": NO, "winning_outcome": "Hawks"}))
    updates, resolved = collector.take()
    assert updates[0]["bid"] == "0.5" and resolved == {ML: "no"}


def test_stream_follows_matched_open_markets_of_upcoming_games(slate):
    apply_events([event()])
    now = datetime.fromtimestamp(NOW, tz=timezone.utc)
    wanted = wanted_markets(now)
    assert len(wanted) == 11 and wanted[YES] == ML

    Market.objects.filter(pk=LUKA_PTS).update(match_confidence=0.7)  # below the threshold
    Market.objects.filter(pk=TOTAL_235).update(closed=True)
    assert set(wanted_markets(now).values()) == set(wanted.values()) - {LUKA_PTS, TOTAL_235}
    assert wanted_markets(now - timedelta(days=3)) == {}  # tip-off more than 36 hours away
    Game.objects.filter(pk="0022600150").update(status="final")
    assert wanted_markets(now) == {}


@pytest.mark.django_db(transaction=True)
def test_stream_end_to_end_against_a_local_feed(slate, settings, monkeypatch):
    """A local WebSocket server plays back the recorded messages; the stream must
    subscribe correctly, keep pinging, and land the prices in the cache and snapshots."""
    from websockets.asyncio.server import serve

    apply_events([event()])
    cache.clear()
    MarketSnapshot.objects.all().delete()
    monkeypatch.setattr("markets.stream.wanted_markets", lambda: {YES: ML})
    monkeypatch.setattr("markets.stream.PING_EVERY", 0.2)
    monkeypatch.setattr("markets.stream.FLUSH_EVERY", 0.1)
    received = []

    async def feed(ws):
        async for frame in ws:
            received.append(frame)
            if frame == "PING":
                await ws.send("PONG")
                continue
            await ws.send(json.dumps([BOOK]))
            await ws.send(json.dumps(PRICE_CHANGE))

    async def scenario():
        async with serve(feed, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            settings.POLYMARKET_WS_URL = f"ws://127.0.0.1:{port}"
            stream = MarketStream()
            runner = asyncio.create_task(stream.run())
            for _ in range(100):
                await asyncio.sleep(0.05)
                if "PING" in received and latest_prices([ML]).get(ML, {}).get("prob") == 0.485:
                    break
            runner.cancel()
            for conn in stream.connections:
                await conn.close()

    asyncio.run(scenario())
    subscribe = json.loads(received[0])
    assert subscribe == {"assets_ids": [YES], "type": "market", "custom_feature_enabled": True}
    assert "PING" in received
    assert latest_prices([ML])[ML]["prob"] == 0.485
    assert MarketSnapshot.objects.filter(market_id=ML).exists()
    assert pricing.stream_heartbeat()["assets"] == 1


# ---- Jobs --------------------------------------------------------------------


def test_discover_markets_job(slate, monkeypatch):
    other = event()
    other["id"], other["slug"] = "999", "nba-lal-atl-2026-11-09"
    for m in other["markets"]:
        m["id"] = "x" + m["id"]
        m["conditionId"] = "x" + m["conditionId"]
    apply_events([other])  # an event from an earlier run that is no longer open
    monkeypatch.setattr(polymarket, "fetch_teams", lambda: FIXTURES["teams_sample"])
    monkeypatch.setattr(polymarket, "nba_series_id", lambda: "10345")
    calls = []

    def pages(series, start_min, start_max):
        calls.append((series, start_min, start_max))
        yield [event()]
        yield []

    monkeypatch.setattr(polymarket, "iter_open_events", pages)

    class Before(datetime):  # 4 pm ET, three hours before tonight's tip-off
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(NOW, tz=timezone.utc)

    monkeypatch.setattr(discovery, "datetime", Before)
    assert discovery.discover_markets() == 11
    assert calls[0][0] == "10345" and calls[0][1] == "2026-11-09T21:00:00Z" < calls[0][2]
    assert Team.objects.get(abbreviation="ATL").polymarket_team_id == "100517"
    assert not Market.objects.filter(event_id="999", closed=False).exists()
    assert Market.objects.filter(event_id="1284256", closed=False).count() == 11
    assert IngestionRun.objects.get(job_name="discover_markets").status == "ok"


def test_poll_midpoints_runs_only_when_the_stream_is_down(slate, monkeypatch):
    apply_events([event()])
    monkeypatch.setattr(tasks, "wanted_markets", lambda: {YES: ML})
    monkeypatch.setattr(polymarket, "fetch_midpoints", lambda tokens: {YES: 0.62})
    monkeypatch.setattr(polymarket, "fetch_spreads", lambda tokens: {YES: 0.02})
    pricing.set_stream_heartbeat(assets=10, connections=2, now=NOW)
    assert tasks.poll_midpoints()["skipped"] == "stream is live"
    pricing.set_stream_heartbeat(assets=10, connections=0, now=NOW)  # running, but every connection is down
    assert tasks.poll_midpoints()["rows"] == 1
    price = latest_prices([ML])[ML]
    assert (price["prob"], price["bid"], price["ask"]) == (0.62, 0.61, 0.63)


def test_prune_keeps_snapshots_an_analysis_uses(slate):
    from predictions.models import Analysis, AnalysisGamePrediction

    apply_events([event()])
    MarketSnapshot.objects.all().delete()
    old = datetime.fromtimestamp(NOW, tz=timezone.utc) - timedelta(days=40)
    keep, drop = (MarketSnapshot.objects.create(market_id=ML, captured_at=old, implied_prob=0.5) for _ in range(2))
    MarketSnapshot.objects.create(market_id=ML, captured_at=old + timedelta(days=20), implied_prob=0.5)
    analysis = Analysis.objects.create(player_id=LUKA, cache_key="k", prompt_version="v1", llm_provider="x", llm_model="y")
    AnalysisGamePrediction.objects.create(
        analysis=analysis, game_id="0022600150", stat="pts", line=34.5, market_snapshot=keep, llm_prob=0.5, confidence="low"
    )
    assert tasks.prune(now=datetime.fromtimestamp(NOW, tz=timezone.utc)) == 1
    assert set(MarketSnapshot.objects.values_list("id", flat=True)) >= {keep.id}
    assert not MarketSnapshot.objects.filter(pk=drop.pk).exists()


# ---- API ---------------------------------------------------------------------


@pytest.fixture
def api(slate):
    apply_events([event()])
    return APIClient()


def test_games_show_the_win_probability_and_its_history(api, monkeypatch):
    games = api.get("/api/games").json()["games"]
    win = games[0]["win_prob"]
    assert {k: win[k] for k in ("home", "away", "is_thin", "volume_24h")} == {
        "home": 0.525, "away": 0.475, "is_thin": False, "volume_24h": 428216.0,
    }  # fmt: skip
    assert win["history"] == [[int(NOW * 1000), 0.525]]

    # The Lakers (the YES side) drift up: history is in home-team terms, oldest first, ending at the current price.
    for minutes, (bid, ask) in enumerate([(0.5, 0.51), (0.55, 0.56)], start=1):
        record_prices([{"market_id": ML, "bid": bid, "ask": ask}], now=NOW + 60 * minutes)
    monkeypatch.setattr("markets.pricing.time.time", lambda: NOW + 120)
    history = api.get("/api/games").json()["games"][0]["win_prob"]["history"]
    assert [p for _, p in history] == [0.525, 0.495, 0.445]
    assert [t for t, _ in history] == sorted(t for t, _ in history)
    yesterday = api.get("/api/games?date=2026-11-08").json()["games"]
    assert yesterday[0]["win_prob"] is None


def test_game_page_has_main_lines_and_each_players_props(api):
    body = api.get("/api/games/0022600150").json()
    markets = body["markets"]
    assert markets["moneyline"]["home"] == 0.525
    assert (markets["spread"]["line"], markets["spread"]["prob"]) == (-2.5, 0.405)  # closer to 50% than -3.5
    assert (markets["total"]["line"], markets["total"]["prob"]) == (235.5, 0.515)
    assert markets["props_count"] == 6
    luka = next(p for p in body["away"]["players"] if p["id"] == LUKA)
    assert set(luka["lines"]) == {"pts", "ast"}
    assert (luka["lines"]["pts"]["line"], luka["lines"]["pts"]["prob"], luka["lines"]["pts"]["alternates"]) == (34.5, 0.565, 0)
    kessler = next(p for p in body["home"]["players"] if p["id"] == KESSLER)
    assert kessler["lines"]["reb"]["is_thin"] is True
    assert api.get("/api/games/0022600140").json()["markets"] is None  # final


def test_player_markets_lists_every_line_for_the_next_games(api):
    Market.objects.filter(pk=LUKA_PTS).update(line=29.5)
    alt = Market.objects.get(pk=LUKA_PTS)
    alt.pk, alt.condition_id, alt.yes_token_id, alt.line = "alt1", "alt1", "alt1", 34.5
    alt.save()
    record_prices([{"market_id": "alt1", "bid": 0.2, "ask": 0.22, "volume_24h": 900}], now=NOW)
    body = api.get(f"/api/players/{LUKA}/markets").json()
    assert len(body["games"]) == 1 and body["games"][0]["game"]["id"] == "0022600150"
    pts = body["games"][0]["lines"]["pts"]
    assert [(r["line"], r["is_main"]) for r in pts] == [(29.5, True), (34.5, False)]
    assert body["games"][0]["lines"]["reb"] == []
    assert api.get(f"/api/players/{GIANNIS}/markets").json() == {"games": []}  # no upcoming game


def test_low_confidence_matches_stay_out_of_the_app(api):
    Market.objects.filter(pk=LUKA_PTS).update(match_confidence=0.7)
    body = api.get("/api/games/0022600150").json()
    luka = next(p for p in body["away"]["players"] if p["id"] == LUKA)
    assert "pts" not in luka["lines"]
    status = api.get("/api/status").json()
    assert status["counts"]["markets_open"] == 11 and status["counts"]["markets_matched"] == 10


def test_moves_of_a_point_or_more_are_pushed(slate, monkeypatch):
    from markets.stream import save
    from realtime import push

    monkeypatch.setattr("markets.stream.close_old_connections", lambda: None)  # runs on its own thread in real life
    apply_events([event()])  # Gamma price: 0.475
    assert save([{"market_id": ML, "bid": 0.475, "ask": 0.485}], {}, 1, 1) == []  # half a point: no push
    (payload,) = save([{"market_id": ML, "bid": 0.5, "ask": 0.52}], {}, 1, 1)
    assert {k: payload[k] for k in ("market_id", "game_id", "type", "team_id", "prob", "is_thin")} == {
        "market_id": ML, "game_id": "0022600150", "type": "moneyline", "team_id": slate["teams"]["LAL"].id, "prob": 0.51, "is_thin": False,
    }  # fmt: skip
    assert push.market_groups(payload) == ["live", "game.0022600150"]

    sent = []
    monkeypatch.setattr("realtime.push.send", lambda groups, event, data: sent.append((groups, event, data["market_id"])))
    monkeypatch.setattr(tasks, "wanted_markets", lambda: {YES: ML})
    monkeypatch.setattr(polymarket, "fetch_midpoints", lambda tokens: {YES: 0.7})
    monkeypatch.setattr(polymarket, "fetch_spreads", lambda tokens: {YES: 0.02})
    tasks.poll_prices()
    assert sent == [(["live", "game.0022600150"], "market.update", ML)]


def test_discovery_leaves_games_under_way_alone(slate, monkeypatch):
    apply_events([event()])
    monkeypatch.setattr(polymarket, "fetch_teams", lambda: [])
    monkeypatch.setattr(polymarket, "nba_series_id", lambda: "10345")
    tomorrow = event()
    tomorrow["id"], tomorrow["slug"] = "555", "nba-lal-atl-2026-11-10"
    tomorrow["markets"] = []
    monkeypatch.setattr(polymarket, "iter_open_events", lambda *args: iter([[tomorrow]]))

    class During(datetime):  # 8 pm ET: tonight's game started an hour ago and is no longer listed
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 10, 1, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(discovery, "datetime", During)
    discovery.discover_markets()
    assert Market.objects.filter(event_id="1284256", closed=False).count() == 11  # still followed by the stream
