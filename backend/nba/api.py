"""Read API for the Live, Game, Player and Team screens. Plain function views that
build their JSON by hand, so the response shape is visible in one place."""

from datetime import date

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from markets import api as market_api
from markets.models import Market
from predictions import service as model_service
from predictions.model import MODEL_VERSION
from markets.pricing import stream_heartbeat
from nba.ingest.live import STATUS_KEY as LIVE_STATUS_KEY
from nba.models import Game, Player, PlayerGameLog, Team, TeamStatSnapshot
from nba.seasons import current_season, today_et
from nba.tasks import JOBS
from ops.models import IngestionRun

REGULAR_SEASON = "002"  # game_id prefix
AVERAGED = ("minutes", "pts", "reb", "ast", "stl", "blk", "tov", "fg3m")
BOX_FIELDS = ("pts", "reb", "ast", "stl", "blk", "tov", "fg3m", "fgm", "fga", "ftm", "fta", "plus_minus")
UPCOMING = (Game.Status.SCHEDULED, Game.Status.LIVE)


def _num(value, digits=1):
    return None if value is None else round(float(value), digits)


def team_ref(team: Team) -> dict:
    return {
        "id": team.id,
        "abbreviation": team.abbreviation,
        "city": team.city,
        "name": team.name,
        "full_name": team.full_name,
    }


def game_card(game: Game, win_prob: dict | None = None) -> dict:
    return {
        "id": game.id,
        "season": game.season,
        "game_date": game.game_date.isoformat(),
        "tipoff_at": game.tipoff_at.isoformat() if game.tipoff_at else None,
        "status": game.status,
        "period": game.period,
        "clock": game.clock,
        "home": {**team_ref(game.home_team), "score": game.home_score},
        "away": {**team_ref(game.away_team), "score": game.away_score},
        "win_prob": win_prob,  # from the Polymarket moneyline; null when there is none
    }


def game_cards(games, history: bool = False) -> list[dict]:
    games = list(games)
    probs = market_api.win_probabilities([g for g in games if g.status != Game.Status.FINAL], history=history)
    return [game_card(game, probs.get(game.id)) for game in games]


def season_averages(season: str, **filters) -> dict[int, dict]:
    """Regular-season per-game averages, keyed by player id."""
    rows = (
        PlayerGameLog.objects.filter(game__season=season, game__id__startswith=REGULAR_SEASON, **filters)
        .values("player_id")
        .annotate(games=Count("id"), **{field: Avg(field) for field in AVERAGED})
    )
    return {
        row["player_id"]: {"season": season, "games": row["games"], **{f: _num(row[f]) for f in AVERAGED}}
        for row in rows
    }


def player_ref(player: Player, averages: dict | None = None) -> dict:
    return {
        "id": player.id,
        "name": player.full_name,
        "position": player.position,
        "jersey": player.jersey,
        "averages": averages,
    }


def box_line(log: PlayerGameLog) -> dict:
    return {"minutes": _num(log.minutes, 0), **{field: getattr(log, field) for field in BOX_FIELDS}}


def log_row(log: PlayerGameLog) -> dict:
    game = log.game
    team_score, opp_score = (
        (game.home_score, game.away_score) if log.is_home else (game.away_score, game.home_score)
    )
    result = None
    if game.status == Game.Status.FINAL and team_score is not None and opp_score is not None:
        result = "W" if team_score > opp_score else "L"
    return {
        "game_id": game.id,
        "game_date": log.game_date.isoformat(),
        "season": game.season,
        "opponent": {"id": log.opponent_team_id, "abbreviation": log.opponent_team.abbreviation},
        "is_home": log.is_home,
        "result": result,
        "team_score": team_score,
        "opponent_score": opp_score,
        **box_line(log),
    }


def games_for_team(team_id: int):
    return Game.objects.filter(Q(home_team_id=team_id) | Q(away_team_id=team_id)).select_related("home_team", "away_team")


@api_view(["GET"])
def games(request):
    """Games on one date (Eastern time). Default: today."""
    raw = request.query_params.get("date")
    try:
        day = date.fromisoformat(raw) if raw else today_et()
    except ValueError:
        return Response({"detail": "date must be YYYY-MM-DD"}, status=400)

    day_games = Game.objects.filter(game_date=day).select_related("home_team", "away_team")
    dates = Game.objects.values_list("game_date", flat=True)
    previous_date = dates.filter(game_date__lt=day).order_by("-game_date").first()
    next_date = dates.filter(game_date__gt=day).order_by("game_date").first()
    return Response(
        {
            "date": day.isoformat(),
            "today": today_et().isoformat(),
            "previous_date": previous_date.isoformat() if previous_date else None,
            "next_date": next_date.isoformat() if next_date else None,
            "games": game_cards(day_games, history=True),
        }
    )


@api_view(["GET"])
def game_detail(request, game_id: str):
    """One game with both teams' players. A finished game lists who played, with
    their lines; an upcoming game lists the current roster with season averages and
    each player's main Polymarket prop lines."""
    game = get_object_or_404(Game.objects.select_related("home_team", "away_team"), pk=game_id)
    markets = market_api.game_summary(game) if game.status != Game.Status.FINAL else None
    prop_lines = markets.pop("player_lines") if markets else {}
    logs = list(game.player_logs.select_related("player").order_by("-minutes", "-pts"))
    averages = season_averages(game.season, team_id__in=[game.home_team_id, game.away_team_id])

    def side(team: Team) -> dict:
        if logs:
            players = [
                {**player_ref(log.player, averages.get(log.player_id)), "line": box_line(log), "lines": prop_lines.get(log.player_id, {})}
                for log in logs
                if log.team_id == team.id
            ]
        else:
            roster = team.players.filter(is_active=True)
            players = [
                {**player_ref(player, averages.get(player.id)), "line": None, "lines": prop_lines.get(player.id, {})}
                for player in roster
            ]
            players.sort(key=lambda p: -((p["averages"] or {}).get("pts") or 0))
        return {**team_ref(team), "players": players}

    return Response(
        {
            "game": game_cards([game])[0],
            "has_box_score": bool(logs),
            "home": side(game.home_team),
            "away": side(game.away_team),
            "markets": markets,  # moneyline, main spread and total; null once the game is final
        }
    )


def build_search_index() -> dict:
    teams = [
        {
            "type": "team",
            "id": team.id,
            "name": team.full_name,
            "search_name": team.search_name,
            "team": team.abbreviation,
        }
        for team in Team.objects.all()
    ]
    players = [
        {
            "type": "player",
            "id": player.id,
            "name": player.full_name,
            "search_name": player.search_name,
            "team": player.team.abbreviation if player.team else None,
        }
        for player in Player.objects.filter(is_active=True).select_related("team")
    ]
    return {"teams": teams, "players": players}


@api_view(["GET"])
def search_index(request):
    """Every team and active player, sent once; the browser does the matching."""
    index = cache.get(settings.SEARCH_INDEX_CACHE_KEY)
    if index is None:
        index = build_search_index()
        cache.set(settings.SEARCH_INDEX_CACHE_KEY, index, settings.SEARCH_INDEX_TTL)
    return Response(index)


@api_view(["GET"])
def player_detail(request, player_id: int):
    player = get_object_or_404(Player.objects.select_related("team"), pk=player_id)
    recent = list(
        player.game_logs.select_related("game", "opponent_team").order_by("-game_date", "-game_id")[:10]
    )

    # Averages for the current season, or the player's latest season if they have not played yet.
    season = current_season()
    averages = season_averages(season, player_id=player.id).get(player.id)
    if averages is None:
        latest = (
            player.game_logs.filter(game__id__startswith=REGULAR_SEASON)
            .order_by("-game_date")
            .values_list("game__season", flat=True)
            .first()
        )
        if latest:
            averages = season_averages(latest, player_id=player.id).get(player.id)

    next_games = []
    if player.team_id:
        upcoming = games_for_team(player.team_id).filter(status__in=UPCOMING, game_date__gte=today_et())
        next_games = game_cards(upcoming.order_by("game_date", "tipoff_at")[:3])

    return Response(
        {
            "player": {
                "id": player.id,
                "name": player.full_name,
                "first_name": player.first_name,
                "last_name": player.last_name,
                "position": player.position,
                "jersey": player.jersey,
                "is_active": player.is_active,
                "team": team_ref(player.team) if player.team else None,
            },
            "season_averages": averages,
            "recent_games": [log_row(log) for log in recent],  # newest first
            "next_games": next_games,
        }
    )


@api_view(["GET"])
def player_markets(request, player_id: int):
    """The player's next 3 games: baseline-model projection per stat, and every Polymarket
    prop line with the market's and the model's chance of going over (edge = model - market)."""
    player = get_object_or_404(Player, pk=player_id)
    games = []
    if player.team_id:
        games = list(
            games_for_team(player.team_id)
            .filter(status__in=UPCOMING, game_date__gte=today_et())
            .order_by("game_date", "tipoff_at")[:3]
        )
    by_game = {entry["game_id"]: entry["lines"] for entry in market_api.player_lines(player, games)}
    result = []
    for game, card in zip(games, game_cards(games)):
        lines = by_game.get(game.id)
        snapshot = model_service.snapshot_for(player, game)
        projection = model_service.projections(snapshot)
        if lines:
            wanted = [(stat, row["line"]) for stat, rows in lines.items() for row in rows if row["line"] is not None]
            model = model_service.predict_lines(snapshot, wanted)
            for stat, rows in lines.items():
                for row in rows:
                    p = model.get((stat, row["line"]))
                    row["model"] = p
                    row["edge"] = None if p is None or row["prob"] is None else round(p - row["prob"], 4)
        result.append(
            {
                "game": card,
                "lines": lines,
                "projection": projection,
                "model_version": MODEL_VERSION,
                "data_gaps": snapshot.features.get("data_gaps", []),
            }
        )
    return Response({"games": result})


@api_view(["GET"])
def team_detail(request, team_id: int):
    team = get_object_or_404(Team, pk=team_id)
    season = current_season()
    today = today_et()
    averages = season_averages(season, team_id=team.id)
    roster = [player_ref(player, averages.get(player.id)) for player in team.players.filter(is_active=True)]
    roster.sort(key=lambda p: -((p["averages"] or {}).get("pts") or 0))

    team_games = games_for_team(team.id)
    finals = team_games.filter(status=Game.Status.FINAL)
    wins = losses = 0
    for game in finals.filter(season=season, id__startswith=REGULAR_SEASON):
        if game.home_score is None or game.away_score is None:
            continue
        won = (game.home_score > game.away_score) == (game.home_team_id == team.id)
        wins, losses = wins + won, losses + (not won)

    snapshot = TeamStatSnapshot.objects.filter(team_id=team.id).order_by("-as_of").first()
    stats = None
    if snapshot:
        stats = {
            "season": snapshot.season,
            "as_of": snapshot.as_of.isoformat(),
            **{
                field: _num(getattr(snapshot, field))
                for field in ("pace", "off_rating", "def_rating", "opp_pts", "opp_reb", "opp_ast")
            },
        }

    upcoming = team_games.filter(status__in=UPCOMING, game_date__gte=today).order_by("game_date", "tipoff_at")
    return Response(
        {
            "team": {**team_ref(team), "conference": team.conference},
            "season": season,
            "record": {"wins": wins, "losses": losses},
            "stats": stats,
            "roster": roster,
            "recent_games": [game_card(game) for game in finals.order_by("-game_date", "-id")[:5]],
            "next_games": game_cards(upcoming[:5]),
        }
    )


@api_view(["GET"])
def status(request):
    """Is the data there, and when did each sync last run? The UI uses this to
    explain an empty screen on first boot instead of just showing nothing."""
    jobs = []
    for name in [*JOBS, "discover_markets", "poll_midpoints"]:
        runs = IngestionRun.objects.filter(job_name=name).order_by("-started_at")
        last = runs.first()
        last_ok = runs.filter(status=IngestionRun.Status.OK).first()
        jobs.append(
            {
                "job": name,
                "status": last.status if last else "never_run",
                "started_at": last.started_at.isoformat() if last else None,
                "finished_at": last.finished_at.isoformat() if last and last.finished_at else None,
                "rows_written": last.rows_written if last else None,
                "error": last.error if last else None,
                "last_success_at": last_ok.finished_at.isoformat() if last_ok and last_ok.finished_at else None,
            }
        )
    return Response(
        {
            "season": current_season(),
            "today": today_et().isoformat(),
            "counts": {
                "teams": Team.objects.count(),
                "active_players": Player.objects.filter(is_active=True).count(),
                "games": Game.objects.count(),
                "player_game_logs": PlayerGameLog.objects.count(),
                "team_stat_snapshots": TeamStatSnapshot.objects.count(),
                "markets_open": Market.objects.filter(closed=False).count(),
                "markets_matched": market_api.shown(Market.objects.all()).count(),
            },
            "jobs": jobs,
            "market_stream": stream_heartbeat(),
            "live_scoreboard": cache.get(LIVE_STATUS_KEY),
        }
    )
