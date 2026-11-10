"""Market data shaped for the API: the latest price of each market the app shows, and the
"main" line of each group (the line priced closest to 50%, which is the line the crowd
treats as the real one; the others are alternates)."""

from collections import defaultdict
from datetime import datetime, timedelta

from django.conf import settings

from markets.models import Market
from markets.pricing import latest_prices

STATS = (Market.Stat.PTS, Market.Stat.REB, Market.Stat.AST)


def shown(queryset):
    """Markets the app shows: matched with enough confidence and still open."""
    return queryset.filter(
        closed=False,
        match_confidence__gte=settings.MARKET_MATCH_THRESHOLD,
    ).exclude(match_method=Market.MatchMethod.UNMATCHED)


def _num(value, digits=1):
    return None if value is None else round(float(value), digits)


def row(market: Market, price: dict | None) -> dict:
    price = price or {}
    return {
        "id": market.id,
        "question": market.question,
        "type": market.market_type,
        "stat": market.stat,
        "line": _num(market.line),
        "team_id": market.team_id,
        "player_id": market.player_id,
        "prob": price.get("prob"),
        "bid": price.get("bid"),
        "ask": price.get("ask"),
        "spread": price.get("spread"),
        "volume_24h": _num(price.get("volume_24h"), 0),
        "liquidity": _num(price.get("liquidity"), 0),
        "is_thin": price.get("is_thin", True),
        "updated_at": price.get("updated_at"),
    }


def main_line(rows: list[dict]) -> dict | None:
    """The line priced closest to 50% (on a tie, the one that is not thin)."""
    priced = [r for r in rows if r["prob"] is not None]
    if not priced:
        return None
    return min(priced, key=lambda r: (round(abs(r["prob"] - 0.5), 4), r["is_thin"], r["line"] or 0))


def _priced(markets: list[Market]) -> list[dict]:
    prices = latest_prices(m.id for m in markets)
    return [row(m, prices.get(m.id)) for m in markets]


def _downsample(points: list, limit: int) -> list:
    if len(points) <= limit:
        return points
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def win_probabilities(games, history: bool = False, hours: int = 24, points: int = 40) -> dict[str, dict]:
    """{game_id: {"home", "away", "is_thin", "volume_24h", "updated_at"}} from each game's
    moneyline. With history=True each also gets "history": [[unix ms, home prob], ...]
    over the last `hours`, at most `points` long, ending at the current price."""
    by_id = {g.id: g for g in games}
    markets = list(shown(Market.objects.filter(game_id__in=by_id, market_type=Market.MarketType.MONEYLINE)))
    result = {}
    home_side = {}
    for r, market in zip(_priced(markets), markets):
        game = by_id[market.game_id]
        if r["prob"] is None or market.team_id not in (game.home_team_id, game.away_team_id):
            continue
        is_home = market.team_id == game.home_team_id
        home_side[market.id] = (game.id, is_home)
        home = r["prob"] if is_home else 1 - r["prob"]
        result[game.id] = {
            "home": round(home, 4),
            "away": round(1 - home, 4),
            "is_thin": r["is_thin"],
            "volume_24h": r["volume_24h"],
            "updated_at": r["updated_at"],
        }
    if history and home_side:
        from django.utils import timezone

        from markets.models import MarketSnapshot

        since = timezone.now() - timedelta(hours=hours)
        series: dict[str, list] = defaultdict(list)
        rows = (
            MarketSnapshot.objects.filter(market_id__in=home_side, captured_at__gte=since, implied_prob__isnull=False)
            .order_by("captured_at")
            .values_list("market_id", "captured_at", "implied_prob")
        )
        for market_id, captured_at, prob in rows:
            game_id, is_home = home_side[market_id]
            value = float(prob) if is_home else 1 - float(prob)
            series[game_id].append([int(captured_at.timestamp() * 1000), round(value, 4)])
        for game_id, entry in result.items():
            line = series.get(game_id, [])
            if entry["updated_at"]:
                now_ms = int(datetime.fromisoformat(entry["updated_at"]).timestamp() * 1000)
                if not line or line[-1][0] < now_ms:
                    line.append([now_ms, entry["home"]])
            entry["history"] = _downsample(line, points)
    return result


def game_summary(game) -> dict:
    """Moneyline, main spread, main total, and each player's main prop lines for one game."""
    markets = list(shown(game.markets.all()))
    rows = _priced(markets)
    spreads = [r for r in rows if r["type"] == Market.MarketType.SPREAD]
    totals = [r for r in rows if r["type"] == Market.MarketType.TOTAL]
    props: dict[int, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["type"] == Market.MarketType.PLAYER_PROP and r["player_id"]:
            props[r["player_id"]][r["stat"]].append(r)

    player_lines = {}
    for player_id, by_stat in props.items():
        lines = {}
        for stat in STATS:
            best = main_line(by_stat.get(stat, []))
            if best:
                lines[stat] = {**best, "alternates": len(by_stat[stat]) - 1}
        player_lines[player_id] = lines

    return {
        "moneyline": win_probabilities([game]).get(game.id),
        "spread": main_line(spreads),
        "total": main_line(totals),
        "props_count": sum(len(v) for by_stat in props.values() for v in by_stat.values()),
        "player_lines": player_lines,
    }


def player_lines(player, games) -> list[dict]:
    """Every prop line for this player in the given games, grouped by stat, main line flagged."""
    markets = list(
        shown(Market.objects.filter(player=player, game__in=games, market_type=Market.MarketType.PLAYER_PROP))
    )
    by_game: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for r, market in zip(_priced(markets), markets):
        by_game[market.game_id][r["stat"]].append(r)
    result = []
    for game in games:
        stats = by_game.get(game.id)
        if not stats:
            continue
        lines = {}
        for stat in STATS:
            rows = sorted(stats.get(stat, []), key=lambda r: r["line"] or 0)
            best = main_line(rows)
            lines[stat] = [{**r, "is_main": best is not None and r["id"] == best["id"]} for r in rows]
        result.append({"game_id": game.id, "lines": lines})
    return result
