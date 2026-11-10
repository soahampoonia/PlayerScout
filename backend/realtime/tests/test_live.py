import asyncio
import copy
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.core.cache import cache

from config.asgi import application
from nba.ingest import client, live
from nba.ingest.live import apply_live, format_clock, live_state, sync_live_scoreboard
from nba.ingest.reference import seed_static
from nba.models import Game, Team
from realtime import push

PREGAME = json.loads((Path(__file__).parents[2] / "nba" / "tests" / "fixtures" / "live_scoreboard_pregame.json").read_text())
ROW = PREGAME["scoreboard"]["games"][0]  # LAL @ ATL, 0022600203, 7:00 pm ET


def row(**changes):
    r = copy.deepcopy(ROW)
    home, away = changes.pop("home", None), changes.pop("away", None)
    r.update(changes)
    if home is not None:
        r["homeTeam"]["score"] = home
    if away is not None:
        r["awayTeam"]["score"] = away
    return r


# ---- Parsing -----------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, shown",
    [("PT05M42.00S", "5:42"), ("PT12M00.00S", "12:00"), ("PT00M38.60S", "38.6"), ("PT00M05.00S", "5.0"), ("PT00M00.00S", "0.0"), ("", ""), ("junk", "")],
)
def test_format_clock(raw, shown):
    assert format_clock(raw) == shown


def test_live_states():
    assert live_state(ROW) == {"status": "scheduled", "period": None, "clock": "", "home_score": None, "away_score": None}
    assert live_state(row(gameStatus=2, gameStatusText="Q3 5:42", period=3, gameClock="PT05M42.00S", home=71, away=68)) == {
        "status": "live", "period": 3, "clock": "5:42", "home_score": 71, "away_score": 68,
    }  # fmt: skip
    assert live_state(row(gameStatus=2, gameStatusText="Half", period=2, gameClock="PT00M00.00S", home=55, away=60))["clock"] == "Half"
    assert live_state(row(gameStatus=2, gameStatusText="End Q1", period=1, gameClock="PT00M00.00S"))["clock"] == "End Q1"
    assert live_state(row(gameStatus=2, gameStatusText="OT 2:10", period=5, gameClock="PT02M10.00S"))["period"] == 5
    assert live_state(row(gameStatus=2, period=6, gameClock="PT00M00.00S"))["clock"] == "End 2OT"
    final = live_state(row(gameStatus=3, gameStatusText="Final", period=4, home=112, away=118))
    assert (final["status"], final["clock"], final["home_score"], final["away_score"]) == ("final", "", 112, 118)
    assert live_state(row(gameStatus=1, gameStatusText="PPD"))["status"] == "postponed"


# ---- Applying ----------------------------------------------------------------


@pytest.fixture
def game(db):
    seed_static()
    return Game.objects.create(
        id="0022600203", season="2026-27", game_date=date(2026, 11, 9),
        tipoff_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
        home_team=Team.objects.get(abbreviation="ATL"), away_team=Team.objects.get(abbreviation="LAL"),
    )  # fmt: skip


def test_apply_live_follows_a_game_from_tip_to_final(game):
    assert apply_live([ROW]) == ([], [])  # nothing changed before tip-off

    changed, finished = apply_live([row(gameStatus=2, period=1, gameClock="PT11M40.00S", home=2, away=0)])
    assert [g.id for g in changed] == [game.id] and finished == []
    game.refresh_from_db()
    assert (game.status, game.period, game.clock, game.home_score, game.away_score) == ("live", 1, "11:40", 2, 0)

    assert apply_live([row(gameStatus=2, period=1, gameClock="PT11M40.00S", home=2, away=0)]) == ([], [])
    assert apply_live([ROW]) == ([], [])  # a stale "scheduled" after tip-off is ignored

    changed, finished = apply_live([row(gameStatus=3, gameStatusText="Final", period=4, home=112, away=118)])
    assert [g.id for g in finished] == [game.id]
    assert apply_live([row(gameStatus=2, period=4, gameClock="PT00M30.00S", home=110, away=118)]) == ([], [])  # never backwards
    game.refresh_from_db()
    assert (game.status, game.home_score, game.away_score) == ("final", 112, 118)


def test_unknown_games_in_the_feed_are_ignored(game):
    assert apply_live([row(gameId="0032600001", gameStatus=2, period=1)]) == ([], [])


def test_the_job_is_idle_unless_a_game_is_near_or_live(game, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "fetch_live_scoreboard", lambda: calls.append(1) or [ROW])
    now = datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc)  # 4 pm ET
    monkeypatch.setattr("nba.ingest.live.timezone.now", lambda: now)
    assert sync_live_scoreboard() == {"idle": True} and calls == []

    now = datetime(2026, 11, 9, 23, 55, tzinfo=timezone.utc)  # five minutes before tip-off
    monkeypatch.setattr("nba.ingest.live.timezone.now", lambda: now)
    assert sync_live_scoreboard() == {"games": 1, "changed": 0, "finished": 0} and calls == [1]
    assert cache.get(live.STATUS_KEY)["ok"] is True

    later = now + timedelta(hours=6)  # long after tip-off and no live game: stop asking
    monkeypatch.setattr("nba.ingest.live.timezone.now", lambda: later)
    assert sync_live_scoreboard() == {"idle": True}


def test_changes_are_pushed_and_a_final_queues_box_scores_once(game, monkeypatch):
    feed = {"rows": [row(gameStatus=2, period=2, gameClock="PT03M00.00S", home=50, away=48)]}
    monkeypatch.setattr(client, "fetch_live_scoreboard", lambda: feed["rows"])
    monkeypatch.setattr("nba.ingest.live.timezone.now", lambda: datetime(2026, 11, 10, 1, tzinfo=timezone.utc))
    queued = []
    monkeypatch.setattr("nba.tasks.sync_game_logs.apply_async", lambda **kw: queued.append(kw))

    layer = get_channel_layer()

    async def listen():
        channel = await layer.new_channel()
        await layer.group_add(push.ALL, channel)
        return channel

    channel = asyncio.run(listen())
    sync_live_scoreboard()
    message = asyncio.run(layer.receive(channel))
    assert message == {
        "type": "push", "event": "game.update",
        "data": {"game_id": game.id, "status": "live", "period": 2, "clock": "3:00", "home_score": 50, "away_score": 48},
    }  # fmt: skip

    feed["rows"] = [row(gameStatus=3, gameStatusText="Final", period=4, home=101, away=99)]
    sync_live_scoreboard()
    Game.objects.filter(pk=game.pk).update(status="live")  # pretend a second game ends a minute later
    sync_live_scoreboard()
    assert len(queued) == 1
    assert queued[0]["kwargs"] == {"seasons": ["2026-27"]} and queued[0]["countdown"] == live.FINAL_SYNC_DELAY


def test_a_feed_failure_is_recorded(game, monkeypatch):
    def fail():
        raise client.NBAUnavailable("ScoreBoard: timed out")

    monkeypatch.setattr(client, "fetch_live_scoreboard", fail)
    monkeypatch.setattr("nba.ingest.live.timezone.now", lambda: datetime(2026, 11, 10, 1, tzinfo=timezone.utc))
    with pytest.raises(client.NBAUnavailable):
        sync_live_scoreboard()
    assert cache.get(live.STATUS_KEY)["error"] == "ScoreBoard: timed out"


# ---- WebSocket -----------------------------------------------------------------


def connect():
    return WebsocketCommunicator(application, "/ws/live", headers=[(b"origin", b"http://localhost:5173")])


def test_socket_subscriptions_and_pushes():
    async def scenario():
        ws = connect()
        connected, _ = await ws.connect()
        assert connected
        assert await ws.receive_json_from() == {"type": "hello", "data": {}}

        await push.asend([push.ALL], "game.update", {"game_id": "0022600203", "home_score": 3})
        assert (await ws.receive_json_from())["data"]["home_score"] == 3

        await ws.send_json_to({"subscribe": {"games": ["0022600203", "bad id", 7], "players": [1629029, "x", True]}})
        groups = (await ws.receive_json_from())["data"]["groups"]
        assert groups == ["game.0022600203", "live", "player.1629029"]

        await push.asend(["game.0022600203"], "market.update", {"market_id": "1"})
        await push.asend(["player.1629029"], "market.update", {"market_id": "2"})
        await push.asend(["game.0022600999"], "market.update", {"market_id": "3"})  # not subscribed
        got = [(await ws.receive_json_from())["data"]["market_id"] for _ in range(2)]
        assert got == ["1", "2"]
        assert await ws.receive_nothing(timeout=0.2)

        await ws.send_json_to({"subscribe": {"games": [], "players": []}})  # leaving the page
        await ws.receive_json_from()
        await push.asend(["game.0022600203"], "market.update", {"market_id": "4"})
        assert await ws.receive_nothing(timeout=0.2)
        await ws.disconnect()

    asyncio.run(scenario())


def test_socket_refuses_other_websites(settings):
    async def scenario():
        ws = WebsocketCommunicator(application, "/ws/live", headers=[(b"origin", b"https://evil.example")])
        connected, _ = await ws.connect()
        assert not connected

    asyncio.run(scenario())


def test_market_groups():
    assert push.market_groups({"type": "moneyline", "game_id": "g", "player_id": None}) == ["live", "game.g"]
    assert push.market_groups({"type": "player_prop", "game_id": "g", "player_id": 5}) == ["game.g", "player.5"]
