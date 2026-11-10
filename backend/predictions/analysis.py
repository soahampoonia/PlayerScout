"""run_analysis: one player's next 3 games, explained by the LLM.

Code does the math and the LLM explains it. For each upcoming game and each of points,
rebounds and assists we pick a target line (Polymarket's main line, or the model's own
line where there is no market), and send one JSON package: the player, his last 10
games, the features, and the model's and market's probabilities. The answer must match
OUTPUT_SCHEMA. We store our own model and market numbers beside the LLM's, never the
LLM's copy of them.

Cache: an analysis is reused while its inputs are the same. The key is a SHA-256 of
the player, the target games, each game's feature hash, each market's probability
and market behind each line, PROMPT_VERSION and the model. Market prices are not in
the key: a stored analysis is reused until one of its prices moves REUSE_MAX_MOVE."""

import hashlib
import json
import math
import uuid
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from markets import api as market_api
from markets.models import Market, MarketSnapshot
from nba.models import Game, Player
from nba.seasons import today_et
from predictions import llm
from predictions import service as model_service
from predictions.model import MODEL_VERSION, p_over
from predictions.models import Analysis, AnalysisGamePrediction

PROMPT_VERSION = "v2"
REUSE_MAX_MOVE = 0.03  # a stored analysis is reused until a market it used moves 3 points
GAMES = 3
STATS = ("pts", "reb", "ast")
STAT_NAMES = {"pts": "points", "reb": "rebounds", "ast": "assists"}
LOCK_TTL = 5 * 60

INSTRUCTIONS = """You are the analyst in Player Scout, an NBA player-analysis app.

You get one JSON package about one player: his averages over recent stretches (form), his last 10 games (a table: columns, then one row per game, newest first), and for each of his next games a set of target lines (points, rebounds, assists). For each line you get two probabilities that he goes OVER it: model_prob from a simple stats model (recent per-minute production x projected minutes, adjusted for opponent and pace), and market_prob from Polymarket prices (null when there is no market; market_is_thin means a wide spread or little trading, so the price says less).

Task: summarize his current performance and his outlook, and give your own probability of going over each target line, with the reasons.

Rules:
- Use only the data in the package. Do not invent injuries, news, minutes restrictions or matchups that are not in it. If something important is missing, say so in data_gaps.
- Give a probability for every target line in the package, using the exact game_id, stat and line given. Probabilities are for going over the line and must be between 0.01 and 0.99.
- Explain where you differ from the model and from the market, and why. Where they disagree, say which you lean toward and why. Do not move far from both without a concrete reason from the data.
- Small samples are noisy: 5 games is not a trend unless minutes or role changed.
- confidence: high only when model, market and recent form agree and the market is not thin; low when data is thin or the inputs disagree.
- reasons: 1 to 4 short, specific sentences with numbers from the package.
- summary: 2 to 4 sentences on current performance. outlook: 2 to 4 sentences on the next games.
- Plain, neutral language. This is analysis, not betting advice."""

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "outlook", "games", "risks", "data_gaps"],
    "properties": {
        "summary": {"type": "string"},
        "outlook": {"type": "string"},
        "games": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["game_id", "stat", "line", "probability", "confidence", "reasons"],
                "properties": {
                    "game_id": {"type": "string"},
                    "stat": {"type": "string", "enum": list(STATS)},
                    "line": {"type": "number"},
                    "probability": {"type": "number"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "data_gaps": {"type": "array", "items": {"type": "string"}},
    },
}


class NoUpcomingGames(Exception):
    pass


def _round(value, digits=1):
    return None if value is None else round(float(value), digits)


def upcoming_games(player: Player) -> list[Game]:
    if not player.team_id:
        return []
    from django.db.models import Q

    return list(
        Game.objects.filter(Q(home_team_id=player.team_id) | Q(away_team_id=player.team_id))
        .filter(status__in=[Game.Status.SCHEDULED, Game.Status.LIVE], game_date__gte=today_et())
        .select_related("home_team", "away_team")
        .order_by("game_date", "tipoff_at")[:GAMES]
    )


def build_package(player: Player) -> dict:
    """{"payload", "targets", "key_parts"} for the player's next games."""
    games = upcoming_games(player)
    if not games:
        raise NoUpcomingGames(f"{player.full_name} has no upcoming games.")
    market_lines = {entry["game_id"]: entry["lines"] for entry in market_api.player_lines(player, games)}

    targets, key_parts, upcoming = [], [], []
    form = league = None
    for game in games:
        snapshot = model_service.snapshot_for(player, game)
        projected = model_service.projections(snapshot)
        f = snapshot.features
        key_parts.append(snapshot.data_hash)
        lines = []
        for stat in STATS:
            proj = projected.get(stat)
            rows = (market_lines.get(game.id) or {}).get(stat) or []
            main = next((r for r in rows if r["is_main"]), None)
            if main:
                line = main["line"]
            elif proj:
                line = math.floor(proj["mean"]) + 0.5  # the model's own line where there is no market
            else:
                continue
            model_prob = p_over(proj["center"], proj["sd"], line) if proj else None
            target = {
                "game_id": game.id,
                "stat": stat,
                "line": line,
                "model_prob": model_prob,
                "model_mean": proj["mean"] if proj else None,
                "model_sd": proj["sd"] if proj else None,
                "market_id": main["id"] if main else None,
                "market_prob": main["prob"] if main else None,
                "market_is_thin": main["is_thin"] if main else None,
                "snapshot_id": snapshot.id,
            }
            targets.append(target)
            key_parts.append(f"{game.id}:{stat}:{line}:{target['market_id'] or ''}")
            lines.append(
                {
                    "stat": stat,
                    "line": line,
                    "line_source": "polymarket" if main else "model",
                    "model_prob": model_prob,
                    "model_mean": target["model_mean"],
                    "model_sd": target["model_sd"],
                    "market_prob": target["market_prob"],
                    "market_is_thin": target["market_is_thin"],
                    "market_spread": main["spread"] if main else None,
                    "market_volume_24h": main["volume_24h"] if main else None,
                }
            )
        is_home = game.home_team_id == f.get("team_id")
        opponent = game.away_team if is_home else game.home_team
        if form is None:  # the same for every upcoming game: sent once
            form = {name: _form(f["windows"].get(name)) for name in ("last5", "last10", "last20", "season")}
            form["last_season"] = _form(f.get("prior"))
            league = f.get("league")
        opp = f.get("opponent") or {}
        upcoming.append(
            {
                "game_id": game.id,
                "date": game.game_date.isoformat(),
                "opponent": opponent.full_name,
                "home_or_away": "home" if is_home else "away",
                "rest_days": f.get("rest_days"),
                "back_to_back": f.get("back_to_back"),
                "opponent_numbers": {k: opp.get(k) for k in ("def_rating", "pace", "opp_pts", "opp_reb", "opp_ast")} if opp else None,
                "lines": lines,
            }
        )

    recent = list(player.game_logs.select_related("game", "opponent_team").order_by("-game_date")[:10])
    payload = {
        "player": {
            "name": player.full_name,
            "team": player.team.full_name if player.team else None,
            "position": player.position,
        },
        "today": today_et().isoformat(),
        "form": form,
        "league_averages": league,
        "last_10_games": {
            "columns": ["date", "opp", "home", "min", "pts", "reb", "ast", "fg3m", "fgm", "fga", "ftm", "fta", "tov", "plus_minus"],
            "rows": [
                [log.game_date.isoformat(), log.opponent_team.abbreviation, log.is_home, _round(log.minutes, 0), log.pts, log.reb,
                 log.ast, log.fg3m, log.fgm, log.fga, log.ftm, log.fta, log.tov, log.plus_minus]
                for log in recent
            ],
        },
        "upcoming_games": upcoming,
        "injuries": "No injury data is available to this app yet.",
        "model": MODEL_VERSION,
    }
    return {"payload": compact(payload), "targets": targets, "key_parts": key_parts}


def _form(window: dict | None) -> dict | None:
    """Averages over a stretch of games (the per-minute rates are left out: they follow
    from these)."""
    if not window:
        return None
    return {k: window.get(k) for k in ("games", "minutes", "pts", "reb", "ast", "fg3m", "usage")}


def compact(value):
    """Drop empty fields and round floats to 3 decimals: the same information in fewer tokens."""
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if v is not None and v != [] and v != {}}
    if isinstance(value, list):
        return [compact(v) for v in value]
    if isinstance(value, float):
        return round(value, 3)
    return value


def cache_key(player: Player, key_parts: list[str]) -> str:
    raw = json.dumps([player.id, key_parts, PROMPT_VERSION, settings.LLM_MODEL if settings.LLM_PROVIDER == "openai" else settings.LLM_PROVIDER])
    return hashlib.sha256(raw.encode()).hexdigest()


def prices_still_close(stored_targets: list[dict], targets: list[dict]) -> bool:
    """True while every market price is within REUSE_MAX_MOVE of the price the stored
    analysis saw. (Replayed on real Polymarket price history, a 3-point band reused an
    analysis on 52% of half-hourly requests, against 13% for exact-cent matching.)"""
    stored = {(t["game_id"], t["stat"], float(t["line"])): t.get("market_prob") for t in stored_targets}
    for t in targets:
        key = (t["game_id"], t["stat"], float(t["line"]))
        then, now = stored.get(key), t.get("market_prob")
        if (then is None) != (now is None):
            return False
        if then is not None and abs(then - now) >= REUSE_MAX_MOVE:
            return False
    return True


def request_analysis(player: Player, force: bool = False) -> tuple[Analysis, bool]:
    """(analysis, created). Reuses a matching analysis unless force; otherwise creates a
    queued one. The caller queues run_analysis when created is True.

    The cache key covers the games, lines, markets and model inputs. Market prices are
    compared separately: a stored analysis stays valid while no price has moved
    REUSE_MAX_MOVE or more since it was written."""
    package = build_package(player)
    key = cache_key(player, package["key_parts"])
    if not force:
        existing = Analysis.objects.filter(cache_key=key).first()
        if existing and existing.status != Analysis.Status.FAILED and not existing.is_stale:
            if prices_still_close((existing.input_payload or {}).get("targets", []), package["targets"]):
                return existing, False
            existing.is_stale = True
            existing.stale_reason = Analysis.StaleReason.MARKET_MOVE
        if existing:  # failed, stale, or the market moved: start over under the same key
            existing.cache_key = hashlib.sha256(f"{key}:{uuid.uuid4()}".encode()).hexdigest()
            existing.save(update_fields=["cache_key", "is_stale", "stale_reason"])
    else:
        old = Analysis.objects.filter(cache_key=key).first()
        if old:
            old.cache_key = hashlib.sha256(f"{key}:{uuid.uuid4()}".encode()).hexdigest()
            old.stale_reason = old.stale_reason or Analysis.StaleReason.MANUAL
            old.is_stale = True
            old.save(update_fields=["cache_key", "stale_reason", "is_stale"])
    analysis = Analysis.objects.create(
        player=player,
        cache_key=key,
        prompt_version=PROMPT_VERSION,
        llm_provider=settings.LLM_PROVIDER,
        llm_model=settings.LLM_MODEL if settings.LLM_PROVIDER == "openai" else settings.LLM_PROVIDER,
        input_payload={"payload": package["payload"], "targets": package["targets"]},
    )
    return analysis, True


def validate(output: dict, targets: list[dict]) -> tuple[list[dict], list[str]]:
    """Keep the LLM's game rows that match a target exactly and have a sane probability."""
    by_key = {(t["game_id"], t["stat"], float(t["line"])): t for t in targets}
    rows, problems, seen = [], [], set()
    for item in output.get("games") or []:
        try:
            key = (str(item["game_id"]), item["stat"], float(item["line"]))
            prob = float(item["probability"])
        except (KeyError, TypeError, ValueError):
            problems.append("an answer row was malformed and was dropped")
            continue
        if key not in by_key:
            problems.append(f"the model answered a line it was not asked about ({key[1]} {key[2]}); dropped")
            continue
        if key in seen:
            continue
        if not 0 <= prob <= 1:
            problems.append(f"probability {prob} for {key[1]} {key[2]} is not between 0 and 1; dropped")
            continue
        seen.add(key)
        rows.append({**by_key[key], "llm_prob": min(max(prob, 0.01), 0.99), "confidence": item.get("confidence", "low"), "reasons": [str(r) for r in item.get("reasons") or []][:6]})
    for key in by_key:
        if key not in seen:
            problems.append(f"no probability for {STAT_NAMES[key[1]]} {key[2]} in game {key[0]}")
    return rows, problems


def run_analysis(analysis_id) -> Analysis:
    """The Celery task body. Idempotent: a finished analysis is returned as is."""
    from realtime import push

    analysis = Analysis.objects.select_related("player").get(pk=analysis_id)
    if analysis.status in (Analysis.Status.DONE, Analysis.Status.FAILED):
        return analysis
    lock = f"lock:analysis:{analysis.cache_key}"
    if not cache.add(lock, "1", timeout=LOCK_TTL):
        return analysis
    try:
        analysis.status = Analysis.Status.RUNNING
        analysis.save(update_fields=["status"])
        targets = analysis.input_payload["targets"]
        try:
            output, usage = llm.analyze(INSTRUCTIONS, analysis.input_payload["payload"], OUTPUT_SCHEMA)
            rows, problems = validate(output, targets)
            if not rows:
                raise llm.LLMError("The answer had no usable probabilities.")
        except llm.LLMError as exc:
            analysis.status = Analysis.Status.FAILED
            analysis.error = str(exc)[:2000]
            analysis.completed_at = timezone.now()
            analysis.save(update_fields=["status", "error", "completed_at"])
            push.send([push.ALL, push.player_group(analysis.player_id)], "analysis.done", _push_payload(analysis))
            return analysis

        game_ids = {t["game_id"] for t in targets}
        valid_games = set(Game.objects.filter(id__in=game_ids).values_list("id", flat=True))
        latest_snaps = {}
        for market_id in {r["market_id"] for r in rows if r["market_id"]}:
            latest_snaps[market_id] = (
                MarketSnapshot.objects.filter(market_id=market_id).order_by("-captured_at").values_list("id", flat=True).first()
            )
        existing_markets = set(Market.objects.filter(pk__in=[r["market_id"] for r in rows if r["market_id"]]).values_list("id", flat=True))
        with transaction.atomic():
            AnalysisGamePrediction.objects.bulk_create(
                [
                    AnalysisGamePrediction(
                        analysis=analysis,
                        game_id=r["game_id"],
                        stat=r["stat"],
                        line=r["line"],
                        market_id=r["market_id"] if r["market_id"] in existing_markets else None,
                        market_snapshot_id=latest_snaps.get(r["market_id"]),
                        model_prob=r["model_prob"],
                        market_prob=r["market_prob"],
                        llm_prob=r["llm_prob"],
                        confidence=r["confidence"] if r["confidence"] in ("low", "medium", "high") else "low",
                        reasons=r["reasons"],
                    )
                    for r in rows
                    if r["game_id"] in valid_games
                ]
            )
            output["data_gaps"] = list(output.get("data_gaps") or []) + problems
            analysis.output = output
            analysis.summary = str(output.get("summary") or "")
            analysis.outlook = str(output.get("outlook") or "")
            analysis.tokens_in = usage.tokens_in
            analysis.tokens_out = usage.tokens_out
            analysis.latency_ms = usage.latency_ms
            analysis.cost_usd = usage.cost_usd
            analysis.llm_model = usage.model
            analysis.status = Analysis.Status.DONE
            analysis.completed_at = timezone.now()
            analysis.save()
            Analysis.objects.filter(
                player_id=analysis.player_id, status=Analysis.Status.DONE, superseded_by__isnull=True, created_at__lt=analysis.created_at
            ).update(superseded_by=analysis)
        push.send([push.ALL, push.player_group(analysis.player_id)], "analysis.done", _push_payload(analysis))
        return analysis
    finally:
        cache.delete(lock)


def _push_payload(analysis: Analysis) -> dict:
    return {"analysis_id": str(analysis.id), "player_id": analysis.player_id, "status": analysis.status}


def recover_stuck(minutes: int = 15) -> int:
    """Mark analyses that never finished (worker restarted mid-run) as failed."""
    cutoff = timezone.now() - timedelta(minutes=minutes)
    return Analysis.objects.filter(status__in=[Analysis.Status.QUEUED, Analysis.Status.RUNNING], created_at__lt=cutoff).update(
        status=Analysis.Status.FAILED, error="Did not finish (the worker may have restarted). Try again.", completed_at=timezone.now()
    )
