from datetime import date

import pytest

from nba.ingest import client, game_logs, reference, schedule, team_stats
from nba.ingest.runs import JobAlreadyRunning, ingestion_run
from nba.models import Game, Player, PlayerGameLog, Team, TeamStatSnapshot
from nba.seasons import season_for_date, seasons_to_sync
from nba.tests import factories
from nba.textnorm import search_key
from ops.models import IngestionRun

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "name, key",
    [
        ("Nikola Jokić", "nikola jokic"),
        ("Luka Dončić", "luka doncic"),
        ("Shai Gilgeous-Alexander", "shai gilgeous alexander"),
        ("P.J. Tucker", "pj tucker"),
        ("De'Aaron Fox", "deaaron fox"),
        ("Nikola Đurišić", "nikola durisic"),
        ("  LeBron   JAMES ", "lebron james"),
    ],
)
def test_search_key(name, key):
    assert search_key(name) == key


def test_season_strings(settings):
    assert season_for_date(date(2026, 11, 9)) == "2026-27"
    assert season_for_date(date(2027, 6, 1)) == "2026-27"
    assert season_for_date(date(2027, 8, 1)) == "2026-27"
    assert season_for_date(date(1999, 12, 1)) == "1999-00"
    settings.NBA_LOG_SEASONS = 2
    assert seasons_to_sync() == ["2026-27", "2025-26"]


def test_seed_static_is_offline_and_repeatable():
    first = reference.seed_static()
    assert Team.objects.count() == 30
    assert Team.objects.filter(conference="East").count() == 15
    assert first == 30 + Player.objects.count()
    assert reference.seed_static() == 30  # no new players the second time
    assert Team.objects.get(abbreviation="LAL").search_name == "los angeles lakers lal"


def test_player_index_sets_rosters_and_deactivates_the_rest():
    reference.seed_static()
    rows = factories.player_index_rows()
    assert reference.apply_player_index(rows) == 450
    assert Player.objects.filter(is_active=True).count() == 450
    assert Player.objects.filter(is_active=True, team__isnull=True).count() == 0

    # A trade and a release: the next sync follows both.
    traded, released = rows[0], rows[1]
    new_team = next(t["id"] for t in factories.TEAMS if t["id"] != traded["TEAM_ID"])
    rows = [{**traded, "TEAM_ID": new_team}, *rows[2:]]
    reference.apply_player_index(rows)
    assert Player.objects.get(pk=traded["PERSON_ID"]).team_id == new_team
    gone = Player.objects.get(pk=released["PERSON_ID"])
    assert (gone.is_active, gone.team_id) == (False, None)


def test_short_player_index_is_refused():
    reference.seed_static()
    reference.apply_player_index(factories.player_index_rows())
    with pytest.raises(client.NBAUnavailable):
        reference.apply_player_index(factories.player_index_rows()[:40])
    assert Player.objects.filter(is_active=True).count() == 450  # nobody was deactivated


def test_schedule_filters_and_maps_status(today):
    reference.seed_static()
    rows, _ = factories.season_fixture(today, days_back=2, days_ahead=2)
    lal, bos = 1610612747, 1610612738
    extra = {
        "gameStatus": 1, "gameStatusText": "7:00 pm ET", "gameDateEst": "2026-11-20T00:00:00Z",
        "gameDateTimeUTC": "2026-11-21T00:00:00Z", "postponedStatus": "A",
        "homeTeam": {"teamId": lal, "score": 0}, "awayTeam": {"teamId": bos, "score": 0},
    }  # fmt: skip
    rows += [
        {**extra, "gameId": "0012600001"},  # preseason: skipped
        {**extra, "gameId": "0032600001"},  # all-star: skipped
        {**extra, "gameId": "0022609001", "awayTeam": {"teamId": 0}},  # opponent not decided: skipped
        {**extra, "gameId": "0022609002", "gameStatusText": "PPD"},
        {**extra, "gameId": "0022609003", "gameStatus": 2, "homeTeam": {"teamId": lal, "score": 55}, "awayTeam": {"teamId": bos, "score": 51}},
        {**extra, "gameId": "0042600101"},  # playoffs: kept
    ]  # fmt: skip
    assert schedule.apply_schedule(rows, "2026-27") == 5 * 6 + 3

    assert not Game.objects.filter(id__in=["0012600001", "0032600001", "0022609001"]).exists()
    assert Game.objects.get(pk="0022609002").status == Game.Status.POSTPONED
    live = Game.objects.get(pk="0022609003")
    assert (live.status, live.home_score, live.away_score) == ("live", 55, 51)
    upcoming = Game.objects.get(pk="0042600101")
    assert (upcoming.status, upcoming.home_score) == ("scheduled", None)
    assert upcoming.game_date == date(2026, 11, 20)
    assert upcoming.tipoff_at.isoformat() == "2026-11-21T00:00:00+00:00"
    assert Game.objects.filter(status="final").count() == 12
    assert Game.objects.filter(status="final", home_score__isnull=True).count() == 0


def test_schedule_never_steps_a_final_game_backwards(today):
    reference.seed_static()
    rows, _ = factories.season_fixture(today, days_back=1, days_ahead=0)
    schedule.apply_schedule(rows, "2026-27")
    final = Game.objects.filter(status="final").first()
    stale = [{**r, "gameStatus": 1, "homeTeam": {**r["homeTeam"], "score": 0}} for r in rows if r["gameId"] == final.id]
    schedule.apply_schedule(stale, "2026-27")
    after = Game.objects.get(pk=final.id)
    assert (after.status, after.home_score, after.away_score) == ("final", final.home_score, final.away_score)


def test_game_logs_written_once_and_updated_in_place(league):
    logs = league["logs"]
    assert PlayerGameLog.objects.count() == len(logs) == 20 * 6 * 20
    sample = logs[0]
    log = PlayerGameLog.objects.select_related("game").get(player_id=sample["PLAYER_ID"], game_id=sample["GAME_ID"])
    assert log.is_home is True and log.game.home_team_id == sample["TEAM_ID"]
    assert log.opponent_team_id == log.game.away_team_id
    assert (log.pts, log.reb, log.fg3m, float(log.minutes)) == (sample["PTS"], sample["REB"], sample["FG3M"], sample["MIN"])
    assert log.started is None

    corrected = [{**sample, "PTS": sample["PTS"] + 2}, *logs[1:]]  # the league corrects a box score
    game_logs.apply_game_logs(corrected, "2026-27")
    assert PlayerGameLog.objects.count() == len(logs)
    log.refresh_from_db()
    assert log.pts == sample["PTS"] + 2


def test_game_logs_create_missing_games_players_and_finalise(today):
    reference.seed_static()
    reference.apply_player_index(factories.player_index_rows())
    rows, logs = factories.season_fixture(today, days_back=1, days_ahead=0)
    final_row = rows[0]
    # The schedule still says "scheduled" for a game that has in fact been played.
    schedule.apply_schedule([{**final_row, "gameStatus": 1}], "2026-27")
    assert Game.objects.get(pk=final_row["gameId"]).status == "scheduled"

    unknown = {**logs[0], "PLAYER_ID": 99999999, "PLAYER_NAME": "Test Rookie-Jr."}
    written = game_logs.apply_game_logs([*logs, unknown], "2026-27")
    assert written == len(logs) + 1

    game = Game.objects.get(pk=final_row["gameId"])
    home_points = sum(r["PTS"] for r in [*logs, unknown] if r["GAME_ID"] == game.id and r["TEAM_ID"] == game.home_team_id)
    assert (game.status, game.home_score) == ("final", home_points)
    assert Game.objects.count() == 6  # the five games the schedule had not seen were created from the logs
    rookie = Player.objects.get(pk=99999999)
    assert (rookie.full_name, rookie.search_name, rookie.is_active) == ("Test Rookie-Jr.", "test rookie jr", False)


def test_team_stats_one_row_per_team_per_day(today):
    reference.seed_static()
    advanced, opponent = factories.team_stat_rows()
    assert team_stats.apply_team_stats(advanced, opponent, "2026-27", today) == 30
    advanced[0]["PACE"] = 101.23
    team_stats.apply_team_stats(advanced, opponent, "2026-27", today)
    assert TeamStatSnapshot.objects.count() == 30
    assert float(TeamStatSnapshot.objects.get(team_id=advanced[0]["TEAM_ID"]).pace) == 101.23
    team_stats.apply_team_stats(advanced, opponent, "2026-27", date(2026, 11, 10))
    assert TeamStatSnapshot.objects.count() == 60


def test_ingestion_run_records_success_failure_and_locks():
    with ingestion_run("job_a") as run:
        run.rows_written = 12
        with pytest.raises(JobAlreadyRunning):
            with ingestion_run("job_a"):
                pass
    ok = IngestionRun.objects.get(job_name="job_a")
    assert (ok.status, ok.rows_written, ok.error) == ("ok", 12, None)
    assert ok.finished_at >= ok.started_at

    with pytest.raises(ValueError):
        with ingestion_run("job_b"):
            raise ValueError("boom")
    failed = IngestionRun.objects.get(job_name="job_b")
    assert (failed.status, failed.error) == ("failed", "ValueError: boom")
    with ingestion_run("job_b"):  # the lock was released after the failure
        pass


def _patch_client(monkeypatch, today, fail=()):
    schedule_rows, log_rows = factories.season_fixture(today)
    advanced, opponent = factories.team_stat_rows()
    calls = []
    monkeypatch.setattr("markets.tasks.discover_markets.delay", lambda: calls.append(("discover_markets",)))

    def fake(name, result):
        def inner(*args):
            calls.append((name, *args))
            if name in fail:
                raise client.NBAUnavailable(f"{name}: timed out")
            return result(*args) if callable(result) else result

        return inner

    monkeypatch.setattr(client, "fetch_player_index", fake("player_index", factories.player_index_rows()))
    monkeypatch.setattr(client, "fetch_schedule", fake("schedule", schedule_rows))
    monkeypatch.setattr(client, "fetch_team_stats", fake("team_stats", lambda season, m: advanced if m == "Advanced" else opponent))
    monkeypatch.setattr(
        client,
        "fetch_player_game_logs",
        fake("game_logs", lambda season, kind: log_rows if (season, kind) == ("2026-27", "Regular Season") else []),
    )
    return calls, log_rows


def test_bootstrap_runs_every_job(monkeypatch, today, settings):
    from nba.tasks import bootstrap

    settings.NBA_LOG_SEASONS = 2
    calls, log_rows = _patch_client(monkeypatch, today)
    results = bootstrap()
    assert [r["job"] for r in results] == ["sync_reference", "sync_schedule", "sync_team_stats", "sync_game_logs"]
    assert all("rows" in r for r in results)
    assert [c for c in calls if c[0] == "game_logs"] == [
        ("game_logs", season, kind) for season in ("2026-27", "2025-26") for kind in ("Regular Season", "PlayIn", "Playoffs")
    ]
    assert PlayerGameLog.objects.count() == len(log_rows)
    assert TeamStatSnapshot.objects.count() == 30
    assert calls[-1] == ("discover_markets",)  # markets are matched once games and rosters exist
    assert set(IngestionRun.objects.values_list("job_name", "status")) == {(name, "ok") for name in (r["job"] for r in results)}


def test_bootstrap_carries_on_past_a_failed_job(monkeypatch, today):
    from nba.tasks import bootstrap

    _patch_client(monkeypatch, today, fail={"schedule"})
    results = {r["job"]: r for r in bootstrap()}
    assert "error" in results["sync_schedule"]
    assert results["sync_game_logs"]["rows"] > 0  # games were created from the logs instead
    failed = IngestionRun.objects.get(job_name="sync_schedule")
    assert failed.status == "failed" and "timed out" in failed.error


def test_client_retries_then_gives_up(monkeypatch, settings):
    settings.NBA_REQUEST_RETRIES = 3
    monkeypatch.setattr(client.time, "sleep", lambda seconds: None)
    attempts = []

    class Endpoint:
        def __init__(self, **params):
            attempts.append(params)
            raise TimeoutError("read timed out")

    with pytest.raises(client.NBAUnavailable, match="Endpoint: TimeoutError"):
        client._call(Endpoint, season="2026-27")
    assert len(attempts) == 3 and attempts[0]["timeout"] == settings.NBA_REQUEST_TIMEOUT


def test_client_reads_nba_api_response_shapes():
    """Feed real nba_api endpoint classes a canned payload, without any network call."""
    from nba_api.stats.endpoints import leaguegamelog, scheduleleaguev2
    from nba_api.stats.library.http import NBAStatsResponse

    def loaded(endpoint_cls, payload, **params):
        import json

        endpoint = endpoint_cls(get_request=False, **params)
        endpoint.nba_response = NBAStatsResponse(response=json.dumps(payload), status_code=200, url="test")
        endpoint.load_response()
        return endpoint

    logs = loaded(
        leaguegamelog.LeagueGameLog,
        {"resultSets": [{"name": "LeagueGameLog", "headers": ["PLAYER_ID", "PTS"], "rowSet": [[2544, 30], [201939, 41]]}]},
        player_or_team_abbreviation="P",
    )
    assert client._rows(logs, "LeagueGameLog") == [{"PLAYER_ID": 2544, "PTS": 30}, {"PLAYER_ID": 201939, "PTS": 41}]
    with pytest.raises(client.NBAUnavailable):
        client._rows(logs, "NoSuchSet")
