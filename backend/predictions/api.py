"""Analyses: ask for one, read one, list them (the History tab)."""

from datetime import datetime

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view
from rest_framework.response import Response

from nba.models import Game, Player
from predictions import llm
from predictions.analysis import NoUpcomingGames, request_analysis
from predictions.models import Analysis

STAT_ORDER = {"pts": 0, "reb": 1, "ast": 2}


def _f(value, digits=4):
    return None if value is None else round(float(value), digits)


def analysis_brief(a: Analysis) -> dict:
    player = a.player
    return {
        "id": str(a.id),
        "player": {"id": player.id, "name": player.full_name, "team": player.team.abbreviation if player.team else None},
        "status": a.status,
        "created_at": a.created_at.isoformat(),
        "completed_at": a.completed_at.isoformat() if a.completed_at else None,
        "summary": a.summary,
        "is_stale": a.is_stale,
        "stale_reason": a.stale_reason,
        "superseded": a.superseded_by_id is not None,
        "llm_model": a.llm_model,
        "error": a.error,
    }


def analysis_full(a: Analysis) -> dict:
    targets = {(t["game_id"], t["stat"], float(t["line"])): t for t in (a.input_payload or {}).get("targets", [])}
    upcoming = {g["game_id"]: g for g in ((a.input_payload or {}).get("payload") or {}).get("upcoming_games", [])}
    games = Game.objects.in_bulk(list(upcoming))
    rows = {}
    for p in a.game_predictions.all():
        t = targets.get((p.game_id, p.stat, float(p.line)), {})
        rows.setdefault(p.game_id, []).append(
            {
                "stat": p.stat,
                "line": _f(p.line, 1),
                "model_prob": _f(p.model_prob),
                "market_prob": _f(p.market_prob),
                "market_is_thin": t.get("market_is_thin"),
                "line_source": "polymarket" if t.get("market_id") else "model",
                "llm_prob": _f(p.llm_prob),
                "confidence": p.confidence,
                "reasons": p.reasons,
                "outcome": p.outcome,
                "actual_value": p.actual_value,
            }
        )
    output = a.output or {}
    return {
        **analysis_brief(a),
        "outlook": a.outlook,
        "risks": output.get("risks", []),
        "data_gaps": output.get("data_gaps", []),
        "games": [
            {
                "game_id": game_id,
                "date": info["date"],
                "opponent": info["opponent"],
                "home_or_away": info["home_or_away"],
                "status": games[game_id].status if game_id in games else None,
                "predictions": sorted(rows.get(game_id, []), key=lambda r: STAT_ORDER.get(r["stat"], 9)),
            }
            for game_id, info in upcoming.items()
        ],
        "llm_provider": a.llm_provider,
        "prompt_version": a.prompt_version,
        "tokens_in": a.tokens_in,
        "tokens_out": a.tokens_out,
        "latency_ms": a.latency_ms,
        "cost_usd": _f(a.cost_usd, 6),
    }


@api_view(["POST"])
def create_analysis(request, player_id: int):
    """200 with a cached analysis, or 202 with a new one that is being written."""
    player = get_object_or_404(Player.objects.select_related("team"), pk=player_id)
    if not llm.is_configured():
        return Response({"detail": "No LLM is set up. Add OPENAI_API_KEY to .env and restart."}, status=503)
    force = bool((request.data or {}).get("force")) if isinstance(request.data, dict) else False
    try:
        analysis, created = request_analysis(player, force=force)
    except NoUpcomingGames as exc:
        return Response({"detail": str(exc)}, status=409)
    if created:
        from predictions.tasks import run_analysis

        run_analysis.delay(str(analysis.id))
        return Response({"analysis": analysis_brief(analysis), "cached": False}, status=202)
    return Response({"analysis": analysis_full(analysis), "cached": True}, status=200)


@api_view(["GET"])
def analysis_detail(request, analysis_id):
    analysis = get_object_or_404(Analysis.objects.select_related("player__team"), pk=analysis_id)
    return Response(analysis_full(analysis))


@api_view(["GET"])
def analysis_list(request):
    """Newest first. ?player_id= filters; ?before=<created_at of the last item> pages."""
    rows = Analysis.objects.select_related("player__team").order_by("-created_at")
    player_id = request.query_params.get("player_id")
    if player_id:
        if not player_id.isdigit():
            return Response({"detail": "player_id must be a number"}, status=400)
        rows = rows.filter(player_id=int(player_id))
    before = request.query_params.get("before")
    if before:
        try:
            # A "+" in the offset arrives as a space when the caller forgot to encode it.
            rows = rows.filter(created_at__lt=datetime.fromisoformat(before.replace(" ", "+")))
        except ValueError:
            return Response({"detail": "before must be an ISO timestamp"}, status=400)
    try:
        limit = min(max(int(request.query_params.get("limit", 20)), 1), 50)
    except ValueError:
        limit = 20
    page = list(rows[: limit + 1])
    items = [analysis_brief(a) for a in page[:limit]]
    return Response({"items": items, "next_before": items[-1]["created_at"] if len(page) > limit else None})
