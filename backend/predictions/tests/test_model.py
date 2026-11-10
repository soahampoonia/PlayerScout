from datetime import date, timedelta
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from markets.models import Market
from markets.pricing import record_prices
from nba.models import Game, Player, PlayerGameLog, TeamStatSnapshot
from predictions import service
from predictions.backtest import run as backtest
from predictions.features import Line, build, data_hash, from_logs
from predictions.model import p_over, project
from predictions.models import FeatureSnapshot, ModelPrediction


def line(day, season="2026-27", minutes=30, pts=20, reb=5, ast=4, **kw):
    return Line(
        game_id=f"g{day}", game_date=date(2026, 11, 1) - timedelta(days=day), season=season, minutes=minutes,
        pts=pts, reb=reb, ast=ast, fg3m=kw.get("fg3m", 2), fga=kw.get("fga", 15), fta=kw.get("fta", 4), tov=kw.get("tov", 2),
        team_fga=kw.get("team_fga", 88), team_fta=kw.get("team_fta", 22), team_tov=kw.get("team_tov", 14), team_minutes=240,
    )  # fmt: skip


def features(lines, **kw):
    args = dict(
        player_id=1, game_id="0022600300", game_date=date(2026, 11, 2), season="2026-27", team_id=10, opponent_id=20,
        is_home=False, lines=lines, last_team_game=date(2026, 10, 31), opponent=None, team_pace=None, league=None,
    )  # fmt: skip
    args.update(kw)
    return from_logs(**args)


# ---- Features ----------------------------------------------------------------


def test_usage_rate():
    # 15 FGA + 0.44*4 FTA + 2 TOV = 18.76 plays in 30 of 48 minutes, team 88 + 9.68 + 14 = 111.68 plays
    assert round(line(1).usage, 1) == round(100 * 18.76 * 48 / (30 * 111.68), 1)
    assert line(1, minutes=0).usage is None


def test_windows_skip_dnps_and_later_games():
    lines = [line(d, pts=d) for d in range(1, 13)]  # newest first: 1, 2, ... 12 points
    lines.insert(2, line(0, minutes=0, pts=0))  # a DNP
    lines.insert(0, line(-5, pts=99))  # after the game: must be ignored
    f = features(lines)
    assert f["windows"]["last5"]["games"] == 5 and f["windows"]["last5"]["pts"] == 3.0
    assert f["windows"]["last10"]["pts"] == 5.5 and f["windows"]["season"]["games"] == 12
    assert f["windows"]["season"]["per_min"]["pts"] == round(sum(range(1, 13)) / (12 * 30), 4)
    assert f["rest_days"] == 2 and f["back_to_back"] is False and f["prior"] is None
    assert f["stdev"]["pts"] > 0
    assert "injuries: no injury source is connected yet" in f["data_gaps"]


def test_last_season_is_the_prior_early_on():
    lines = [line(1, pts=30), line(2, pts=30)] + [line(200 + d, season="2025-26", pts=10) for d in range(20)]
    f = features(lines, last_team_game=date(2026, 11, 1))
    assert f["prior"]["games"] == 20 and f["back_to_back"] is True
    assert any("last season used as the prior" in gap for gap in f["data_gaps"])
    # The model leans on last season's 10 points as the prior, not only on two 30-point games.
    assert 10 < project(f, "pts")["mean"] < 30


def test_hash_changes_with_inputs():
    a = features([line(d) for d in range(1, 11)])
    assert data_hash(a) == data_hash(features([line(d) for d in range(1, 11)]))
    assert data_hash(a) != data_hash(features([line(d) for d in range(1, 12)]))


# ---- Model -------------------------------------------------------------------


def test_projection_blends_rates_and_minutes():
    steady = features([line(d) for d in range(1, 21)])  # 20 points in 30 minutes, every night
    proj = project(steady, "pts")
    assert proj["mean"] == 20.0 and proj["minutes"] == 30.0 and proj["adjustment"] == 1.0
    assert proj["sd"] == pytest.approx(0.9 * 20**0.5, abs=0.01)  # no spread at all: the floor applies

    hot = features([line(d, minutes=36, pts=30) for d in range(1, 6)] + [line(d) for d in range(6, 21)])
    p = project(hot, "pts")
    assert 20 < p["mean"] < 30 and 30 < p["minutes"] < 36

    tired = features([line(d) for d in range(1, 21)], last_team_game=date(2026, 11, 1))
    assert project(tired, "pts")["mean"] == pytest.approx(20 * 0.97, abs=0.01)


def test_opponent_and_pace_adjustments():
    league = {"def_rating": 115.0, "pace": 100.0, "opp_pts": 115.0, "opp_reb": 44.0, "opp_ast": 26.0}
    soft = {"def_rating": 120.0, "pace": 104.0, "opp_reb": 48.4, "opp_ast": 26.0}
    f = features([line(d) for d in range(1, 21)], opponent=soft, league=league, team_pace=100.0)
    assert project(f, "pts")["adjustment"] == round(120 / 115 * 102 / 100, 3)
    assert project(f, "reb")["adjustment"] == round(1 + 0.5 * 0.1, 3)
    assert project(f, "ast")["adjustment"] == 1.0
    home = features([line(d) for d in range(1, 21)], is_home=True)
    assert project(home, "pts")["adjustment"] == 1.01


def test_p_over():
    assert p_over(20, 5, 20) == 0.5
    assert p_over(20, 5, 15.5) > 0.8 > 0.2 > p_over(20, 5, 24.5)
    assert p_over(20, 5, 60) == 0.01 and p_over(20, 5, 0.5) == 0.99
    assert project(features([]), "pts") is None


# ---- Database ------------------------------------------------------------------


@pytest.fixture
def setup(league):
    game = Game.objects.filter(status="scheduled").order_by("game_date", "id").first()
    log = PlayerGameLog.objects.filter(team=game.home_team, player__team=game.home_team).order_by("-pts").first()
    return {"game": game, "player": log.player}


def test_build_from_the_database(setup):
    f = build(setup["player"], setup["game"])
    assert f["is_home"] is True and f["opponent_id"] == setup["game"].away_team_id
    assert f["windows"]["season"]["games"] >= 5 and f["windows"]["last5"]["usage"] > 0
    assert f["opponent"]["def_rating"] > 0 and f["league"]["pace"] > 0
    assert f["rest_days"] >= 1


def test_snapshots_are_reused_until_inputs_change(setup):
    player, game = setup["player"], setup["game"]
    first = service.snapshot_for(player, game)
    assert service.snapshot_for(player, game).pk == first.pk
    TeamStatSnapshot.objects.filter(team_id=game.away_team_id).update(def_rating=Decimal("130.0"))
    second = service.snapshot_for(player, game)
    assert second.pk != first.pk and FeatureSnapshot.objects.count() == 2
    assert service.projections(second)["pts"]["mean"] > service.projections(first)["pts"]["mean"]


def test_predictions_are_stored_once(setup):
    snapshot = service.snapshot_for(setup["player"], setup["game"])
    result = service.predict_lines(snapshot, [("pts", 20.5), ("pts", 25.5), ("reb", 6.5)])
    assert result[("pts", 20.5)] > result[("pts", 25.5)]
    assert ModelPrediction.objects.count() == 3
    service.predict_lines(snapshot, [("pts", 20.5)])
    assert ModelPrediction.objects.count() == 3
    row = ModelPrediction.objects.get(stat="pts", line=Decimal("20.5"))
    assert row.model_version == "baseline-v2" and float(row.p_over) == result[("pts", 20.5)]


def prop(game, player, stat, line, market_id):
    return Market.objects.create(
        id=market_id, condition_id=market_id, event_id="e", event_slug="s", question=f"{player.full_name}: {stat} O/U {line}",
        market_type="player_prop", stat=stat, line=Decimal(str(line)), game=game, player=player, team=player.team,
        match_method="roster_name", match_confidence=Decimal("1"), yes_token_id=f"y{market_id}", no_token_id=f"n{market_id}",
    )  # fmt: skip


def test_refresh_model_covers_every_matched_line(setup, monkeypatch):
    game, player = setup["game"], setup["player"]
    Game.objects.filter(pk=game.pk).update(tipoff_at=game.tipoff_at)
    monkeypatch.setattr("predictions.service.timezone.now", lambda: game.tipoff_at - timedelta(hours=3))
    prop(game, player, "pts", 22.5, "m1")
    prop(game, player, "ast", 4.5, "m2")
    assert service.refresh_model() == 2
    assert set(ModelPrediction.objects.values_list("stat", flat=True)) == {"pts", "ast"}


def test_player_markets_show_model_and_edge(setup, monkeypatch):
    game, player = setup["game"], setup["player"]
    prop(game, player, "pts", 22.5, "m1")
    record_prices([{"market_id": "m1", "bid": 0.4, "ask": 0.42, "volume_24h": 5000}])
    body = APIClient().get(f"/api/players/{player.id}/markets").json()
    entry = body["games"][0]
    assert entry["game"]["id"] == game.id and entry["model_version"] == "baseline-v2"
    assert set(entry["projection"]) == {"pts", "reb", "ast"} and entry["projection"]["pts"]["mean"] > 0
    row = entry["lines"]["pts"][0]
    assert row["prob"] == 0.41 and 0 < row["model"] < 1 and row["edge"] == round(row["model"] - 0.41, 4)
    assert any("injuries" in gap for gap in entry["data_gaps"])
    # Games without markets still get a projection.
    assert all(e["projection"] for e in body["games"])


def test_backtest_runs_on_stored_games(league):
    report = backtest("2026-27")
    assert set(report) == {"pts", "reb", "ast"}
    assert report["pts"]["n"] > 30 and 0 < report["pts"]["brier_model"] < 0.5
    assert sum(b["n"] for b in report["pts"]["calibration"]) == report["pts"]["n"]
