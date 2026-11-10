"""Latest prices and price history.

Every price source (the WebSocket stream, the REST fallback, and discovery's Gamma
data) goes through record_prices(), so the rules live in one place:

- implied probability = order-book midpoint, or the last trade when the spread is
  wider than MARKET_MIDPOINT_MAX_SPREAD (the same rule Polymarket's own site uses);
- the latest price per market lives in the cache (Redis), keyed by market id;
- a market_snapshot row is written when the probability moves MARKET_SNAPSHOT_MOVE
  or more, or MARKET_SNAPSHOT_HEARTBEAT seconds after the last one.

All prices are for the market's YES token (the first outcome)."""

import time
from datetime import datetime, timezone
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache

from markets.models import MarketSnapshot

PRICE_KEY = "mkt:price:{}"
PRICE_TTL = 2 * 24 * 3600
STREAM_HEARTBEAT_KEY = "market-stream:heartbeat"
# Discovery's Gamma prices may be a little behind; they only fill in a price that the
# stream or the fallback poller has not refreshed for this long.
SEED_IF_OLDER_THAN = 120

PRICE_FIELDS = ("bid", "ask", "last")
META_FIELDS = ("volume_24h", "liquidity")


def _f(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def implied_probability(bid, ask, last):
    """(midpoint, spread, implied probability) from the best bid, best ask and last trade."""
    bid, ask, last = _f(bid), _f(ask), _f(last)
    if bid is not None and ask is not None and ask >= bid:
        mid = round((bid + ask) / 2, 4)
        spread = round(ask - bid, 4)
        if spread > settings.MARKET_MIDPOINT_MAX_SPREAD and last is not None:
            return mid, spread, last
        return mid, spread, mid
    if bid is not None and ask is None:
        return None, None, last if last is not None else bid
    if ask is not None and bid is None:
        return None, None, last if last is not None else ask
    return None, None, last


def is_thin(spread, volume_24h) -> bool:
    if spread is None or spread > settings.MARKET_THIN_SPREAD:
        return True
    return volume_24h is not None and volume_24h < settings.MARKET_THIN_MIN_VOLUME_24H


def _dec(value, places=4):
    if value is None:
        return None
    value = min(max(float(value), 0.0), 1.0) if places == 4 else float(value)
    return Decimal(str(round(value, places)))


def record_prices(updates: list[dict], now: float | None = None, seed: bool = False) -> list[str]:
    """Merge price updates into the latest-price cache and write snapshots.

    Each update is {"market_id": ..., and any of bid, ask, last, volume_24h, liquidity}.
    With seed=True (Gamma data from discovery) prices are applied only where nothing
    fresher is known; volume and liquidity are always taken. Returns the ids of markets
    whose probability moved by MARKET_SNAPSHOT_MOVE or more."""
    if not updates:
        return []
    now = time.time() if now is None else now
    keys = {u["market_id"]: PRICE_KEY.format(u["market_id"]) for u in updates}
    previous = cache.get_many(list(keys.values()))

    to_cache, snapshots, moved = {}, [], []
    for update in updates:
        market_id = update["market_id"]
        state = dict(previous.get(keys[market_id]) or {})
        fresh = state.get("ts") is not None and now - state["ts"] < SEED_IF_OLDER_THAN
        if not (seed and fresh):
            for field in PRICE_FIELDS:
                if field in update and update[field] is not None:
                    state[field] = _f(update[field])
            state["ts"] = now
        for field in META_FIELDS:
            if field in update:
                state[field] = _f(update[field])

        mid, spread, implied = implied_probability(state.get("bid"), state.get("ask"), state.get("last"))
        if implied is None:
            to_cache[keys[market_id]] = state
            continue
        state.update(mid=mid, spread=spread, implied=round(implied, 4))
        state["is_thin"] = is_thin(spread, state.get("volume_24h"))

        last_prob, last_at = state.get("snap_prob"), state.get("snap_at")
        changed = last_prob is None or abs(implied - last_prob) >= settings.MARKET_SNAPSHOT_MOVE - 1e-9
        due = last_at is None or now - last_at >= settings.MARKET_SNAPSHOT_HEARTBEAT
        if changed and last_prob is not None:
            moved.append(market_id)
        if changed or due:
            snapshots.append(
                MarketSnapshot(
                    market_id=market_id,
                    captured_at=datetime.fromtimestamp(now, tz=timezone.utc),
                    best_bid=_dec(state.get("bid")),
                    best_ask=_dec(state.get("ask")),
                    midpoint=_dec(mid),
                    last_trade=_dec(state.get("last")),
                    spread=_dec(spread),
                    implied_prob=_dec(implied),
                    volume_24h=_dec(state.get("volume_24h"), 2),
                    liquidity=_dec(state.get("liquidity"), 2),
                    is_thin=state["is_thin"],
                )
            )
            state["snap_prob"], state["snap_at"] = implied, now
        to_cache[keys[market_id]] = state

    if snapshots:
        MarketSnapshot.objects.bulk_create(snapshots, batch_size=1000)
    cache.set_many(to_cache, PRICE_TTL)
    return moved


def latest_prices(market_ids) -> dict[str, dict]:
    """Latest known price per market: from the cache, or from the newest snapshot if the
    cache was emptied (Redis restarted). Markets never priced are left out."""
    market_ids = list(market_ids)
    if not market_ids:
        return {}
    found = cache.get_many([PRICE_KEY.format(m) for m in market_ids])
    result = {}
    for market_id in market_ids:
        state = found.get(PRICE_KEY.format(market_id))
        if state and state.get("implied") is not None:
            result[market_id] = {
                "prob": state["implied"],
                "bid": state.get("bid"),
                "ask": state.get("ask"),
                "spread": state.get("spread"),
                "volume_24h": state.get("volume_24h"),
                "liquidity": state.get("liquidity"),
                "is_thin": state.get("is_thin", True),
                "updated_at": datetime.fromtimestamp(state["ts"], tz=timezone.utc).isoformat() if state.get("ts") else None,
            }
    missing = [m for m in market_ids if m not in result]
    if missing:
        rows = (
            MarketSnapshot.objects.filter(market_id__in=missing)
            .order_by("market_id", "-captured_at")
            .distinct("market_id")
        )
        for snap in rows:
            result[snap.market_id] = {
                "prob": _f(snap.implied_prob),
                "bid": _f(snap.best_bid),
                "ask": _f(snap.best_ask),
                "spread": _f(snap.spread),
                "volume_24h": _f(snap.volume_24h),
                "liquidity": _f(snap.liquidity),
                "is_thin": snap.is_thin,
                "updated_at": snap.captured_at.isoformat(),
            }
    return result


def stream_heartbeat() -> dict | None:
    return cache.get(STREAM_HEARTBEAT_KEY)


def set_stream_heartbeat(assets: int, connections: int, now: float | None = None):
    now = time.time() if now is None else now
    cache.set(STREAM_HEARTBEAT_KEY, {"ts": now, "assets": assets, "connections": connections}, 600)


def stream_is_live(max_age: float = 90) -> bool:
    beat = stream_heartbeat()
    return bool(beat) and time.time() - beat["ts"] < max_age and beat.get("connections", 0) > 0
