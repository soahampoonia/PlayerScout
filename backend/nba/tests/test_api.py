import pytest
from rest_framework.test import APIClient

from nba.ingest import reference
from nba.models import Game, Player, Team
from nba.tests import factories

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def test_games_for_today_and_other_dates(api, league):
    body = api.get("/api/games").json()
    assert body["date"] == body["today"] == "2026-11-09"
    assert (body["previous_date"], body["next_date"]) == ("2026-11-08", "2026-11-10")
    assert len(body["games"]) == 6
    game = body["games"][0]
    assert game["status"] == "scheduled" and game["home"]["score"] is None
    assert set(game["home"]) == {"id", "abbreviation", "city", "name", "full_name", "score"}
    assert game["tipoff_at"].startswith("2026-11-10T00:")

    past = api.get("/api/games?date=2026-11-08").json()
    assert all(g["status"] == "final" and g["home"]["score"] > 0 for g in past["games"])
    assert api.get("/api/games?date=2026-07-04").json()["games"] == []
    assert api.get("/api/games?date=tomorrow").status_code == 400


def test_finished_game_shows_who_played(api, league):
    game = Game.objects.filter(status="final").order_by("-game_date").first()
    body = api.get(f"/api/games/{game.id}").json()
    assert body["has_box_score"] is True and body["markets"] is None
    assert body["home"]["id"] == game.home_team_id and len(body["home"]["players"]) == 10
    lines = [p["line"] for p in body["home"]["players"]]
    assert sum(line["pts"] for line in lines) == game.home_score
    assert [line["minutes"] for line in lines] == sorted((line["minutes"] for line in lines), reverse=True)
    assert body["home"]["players"][0]["averages"]["games"] >= 1


def test_upcoming_game_shows_rosters_with_averages(api, league):
    game = Game.objects.filter(status="scheduled").first()
    body = api.get(f"/api/games/{game.id}").json()
    assert body["has_box_score"] is False
    players = body["away"]["players"]
    assert len(players) == 15 and all(p["line"] is None for p in players)
    scoring = [(p["averages"] or {}).get("pts") or 0 for p in players]
    assert scoring == sorted(scoring, reverse=True)
    assert api.get("/api/games/0000000000").status_code == 404


def test_search_index_lists_active_players_and_teams_and_refreshes(api, league):
    body = api.get("/api/search-index").json()
    assert len(body["teams"]) == 30 and len(body["players"]) == 450
    assert {"type": "team", "id": 1610612747, "name": "Los Angeles Lakers", "search_name": "los angeles lakers lal", "team": "LAL"} in body["teams"]
    player = body["players"][0]
    assert set(player) == {"type", "id", "name", "search_name", "team"} and len(player["team"]) == 3

    # A roster sync drops the cached copy, so a released player disappears at once.
    released = Player.objects.get(pk=player["id"])
    rows = [r for r in factories.player_index_rows() if r["PERSON_ID"] != released.id]
    reference.apply_player_index(rows)
    assert len(api.get("/api/search-index").json()["players"]) == 449


def test_player_page(api, league):
    row = league["logs"][0]
    played = [r for r in league["logs"] if r["PLAYER_ID"] == row["PLAYER_ID"]]
    body = api.get(f'/api/players/{row["PLAYER_ID"]}').json()
    assert body["player"]["team"]["id"] == row["TEAM_ID"] and body["player"]["is_active"] is True

    recent = body["recent_games"]
    assert len(recent) == min(10, len(played))
    assert [g["game_date"] for g in recent] == sorted((g["game_date"] for g in recent), reverse=True)
    newest = max(played, key=lambda r: r["GAME_DATE"])
    assert (recent[0]["game_id"], recent[0]["pts"]) == (newest["GAME_ID"], newest["PTS"])
    assert recent[0]["result"] in ("W", "L")
    assert (recent[0]["result"] == "W") == (recent[0]["team_score"] > recent[0]["opponent_score"])

    averages = body["season_averages"]
    assert averages["season"] == "2026-27" and averages["games"] == len(played)
    assert averages["pts"] == round(sum(r["PTS"] for r in played) / len(played), 1)

    next_games = body["next_games"]
    assert 1 <= len(next_games) <= 3
    assert all(row["TEAM_ID"] in (g["home"]["id"], g["away"]["id"]) and g["game_date"] >= "2026-11-09" for g in next_games)


def test_player_without_games_or_team(api, league):
    retired = Player.objects.filter(is_active=False, team__isnull=True).first()
    body = api.get(f"/api/players/{retired.id}").json()
    assert body["player"]["team"] is None
    assert (body["season_averages"], body["recent_games"], body["next_games"]) == (None, [], [])
    assert api.get("/api/players/1").status_code == 404


def test_team_page(api, league):
    team = Team.objects.get(abbreviation="BOS")
    body = api.get(f"/api/teams/{team.id}").json()
    assert body["team"]["conference"] == "East" and body["season"] == "2026-27"
    assert len(body["roster"]) == 15
    finals = [g for g in Game.objects.filter(status="final") if team.id in (g.home_team_id, g.away_team_id)]
    wins = sum((g.home_score > g.away_score) == (g.home_team_id == team.id) for g in finals)
    assert body["record"] == {"wins": wins, "losses": len(finals) - wins}
    assert len(body["recent_games"]) == min(5, len(finals))
    assert all(g["status"] == "final" for g in body["recent_games"])
    assert all(g["status"] == "scheduled" for g in body["next_games"])
    assert set(body["stats"]) == {"season", "as_of", "pace", "off_rating", "def_rating", "opp_pts", "opp_reb", "opp_ast"}
    assert api.get("/api/teams/1").status_code == 404


def test_status_before_and_after_first_sync(api, db, today):
    empty = api.get("/api/status").json()
    assert empty["counts"]["games"] == 0
    assert {j["job"]: j["status"] for j in empty["jobs"]} == {
        "sync_reference": "never_run", "sync_schedule": "never_run", "sync_team_stats": "never_run", "sync_game_logs": "never_run",
        "discover_markets": "never_run", "poll_midpoints": "never_run",
    }  # fmt: skip
    assert empty["market_stream"] is None

    from nba.ingest.runs import ingestion_run

    with pytest.raises(RuntimeError):
        with ingestion_run("sync_schedule"):
            raise RuntimeError("stats.nba.com timed out")
    job = next(j for j in api.get("/api/status").json()["jobs"] if j["job"] == "sync_schedule")
    assert job["status"] == "failed" and "timed out" in job["error"] and job["last_success_at"] is None
