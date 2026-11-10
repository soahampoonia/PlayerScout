"""Pushes to browsers over /ws/live, through the Channels layer (Redis).

Groups:
- "live": every connected browser. Gets every game.update, and market.update for
  moneylines (the win probability on each Live card).
- "game.<game_id>": browsers on that game's page. Gets every market.update for the game.
- "player.<player_id>": browsers on that player's page. Gets his props' market.update.

A push that fails (Redis down) is logged and dropped: the pages also re-read the API
when the socket is not connected, so nothing is lost for good."""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

log = logging.getLogger(__name__)

ALL = "live"


def game_group(game_id) -> str:
    return f"game.{game_id}"


def player_group(player_id) -> str:
    return f"player.{player_id}"


def _message(event: str, data: dict) -> dict:
    return {"type": "push", "event": event, "data": data}


async def asend(groups, event: str, data: dict):
    layer = get_channel_layer()
    if layer is None:
        return
    for group in groups:
        try:
            await layer.group_send(group, _message(event, data))
        except Exception as exc:
            log.warning("push %s to %s failed: %s", event, group, exc)


def send(groups, event: str, data: dict):
    try:
        async_to_sync(asend)(groups, event, data)
    except Exception as exc:  # pragma: no cover - asend already swallows layer errors
        log.warning("push %s failed: %s", event, exc)


def game_update_payload(game) -> dict:
    return {
        "game_id": game.id,
        "status": game.status,
        "period": game.period,
        "clock": game.clock,
        "home_score": game.home_score,
        "away_score": game.away_score,
    }


def market_groups(payload: dict) -> list[str]:
    groups = []
    if payload.get("type") == "moneyline":
        groups.append(ALL)
    if payload.get("game_id"):
        groups.append(game_group(payload["game_id"]))
    if payload.get("player_id"):
        groups.append(player_group(payload["player_id"]))
    return groups


def market_payloads(market_ids) -> list[dict]:
    """market.update payloads for markets whose price just moved (sync: uses the ORM)."""
    from markets.models import Market
    from markets.pricing import latest_prices

    market_ids = list(market_ids)
    if not market_ids:
        return []
    prices = latest_prices(market_ids)
    rows = Market.objects.filter(id__in=market_ids).values(
        "id", "game_id", "player_id", "team_id", "market_type", "stat", "line"
    )
    payloads = []
    for row in rows:
        price = prices.get(row["id"])
        if not price:
            continue
        payloads.append(
            {
                "market_id": row["id"],
                "game_id": row["game_id"],
                "player_id": row["player_id"],
                "team_id": row["team_id"],
                "type": row["market_type"],
                "stat": row["stat"],
                "line": None if row["line"] is None else float(row["line"]),
                "prob": price["prob"],
                "is_thin": price["is_thin"],
                "updated_at": price["updated_at"],
            }
        )
    return payloads


def push_markets(market_ids):
    for payload in market_payloads(market_ids):
        send(market_groups(payload), "market.update", payload)
