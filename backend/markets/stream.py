"""market_stream: keeps Polymarket prices current from the CLOB WebSocket.

One long-running process (`python manage.py market_stream`, its own Compose service).
It follows the YES token of every matched, open market for games tipping off within
MARKET_STREAM_HOURS_AHEAD, spread over several connections of at most
MARKET_STREAM_ASSETS_PER_CONNECTION tokens each. Every two seconds it hands the price
changes it has collected to pricing.record_prices (cache + snapshots) on a worker
thread, and every minute it re-reads which markets to follow.

Wire format (recorded from the live feed, Nov 2026): the first message after a
subscribe is a JSON array of "book" objects; after that, single objects with an
event_type of book, price_change (with best_bid / best_ask for both of the market's
tokens), best_bid_ask, last_trade_price, tick_size_change or market_resolved. The
server answers the text "PING" with "PONG"."""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections

from markets.models import Market
from markets.pricing import record_prices, set_stream_heartbeat
from nba.models import Game
from realtime import push

log = logging.getLogger("markets.stream")

PING_EVERY = 10
FLUSH_EVERY = 2
REFRESH_EVERY = 60


def wanted_markets(now: datetime | None = None) -> dict[str, str]:
    """{yes_token_id: market_id} for the markets the stream should follow."""
    now = now or datetime.now(timezone.utc)
    rows = Market.objects.filter(
        active=True,
        closed=False,
        match_confidence__gte=settings.MARKET_MATCH_THRESHOLD,
        game__status__in=[Game.Status.SCHEDULED, Game.Status.LIVE],
        game__tipoff_at__gte=now - timedelta(hours=6),
        game__tipoff_at__lte=now + timedelta(hours=settings.MARKET_STREAM_HOURS_AHEAD),
    ).exclude(match_method=Market.MatchMethod.UNMATCHED)
    return dict(rows.values_list("yes_token_id", "id"))


class PriceCollector:
    """Turns feed messages into pending price updates, one per market."""

    def __init__(self):
        self.tokens: dict[str, str] = {}  # yes token -> market id
        self.pending: dict[str, dict] = {}
        self.resolved: dict[str, str] = {}  # market id -> yes / no
        self.messages = 0

    def _set(self, asset_id, **fields):
        market_id = self.tokens.get(str(asset_id))
        if market_id is None:
            return
        update = self.pending.setdefault(market_id, {"market_id": market_id})
        update.update({k: v for k, v in fields.items() if v not in (None, "")})

    def handle(self, raw: str):
        if raw in ("PONG", "PING", ""):
            return
        try:
            data = json.loads(raw)
        except ValueError:
            log.debug("ignoring non-JSON frame: %.80s", raw)
            return
        for message in data if isinstance(data, list) else [data]:
            if isinstance(message, dict):
                self.messages += 1
                self._message(message)

    def _message(self, msg: dict):
        kind = msg.get("event_type") or ("book" if "bids" in msg or "asks" in msg else None)
        if kind == "book":
            bids = [float(level["price"]) for level in msg.get("bids") or []]
            asks = [float(level["price"]) for level in msg.get("asks") or []]
            self._set(msg.get("asset_id"), bid=max(bids) if bids else None, ask=min(asks) if asks else None)
        elif kind == "price_change":
            for change in msg.get("price_changes") or []:
                self._set(change.get("asset_id"), bid=change.get("best_bid"), ask=change.get("best_ask"))
        elif kind == "best_bid_ask":
            self._set(msg.get("asset_id"), bid=msg.get("best_bid"), ask=msg.get("best_ask"))
        elif kind == "last_trade_price":
            self._set(msg.get("asset_id"), last=msg.get("price"))
        elif kind == "market_resolved":
            for asset in msg.get("assets_ids") or []:
                market_id = self.tokens.get(str(asset))
                if market_id:
                    winner = str(msg.get("winning_asset_id"))
                    self.resolved[market_id] = Market.Outcome.YES if winner == str(asset) else Market.Outcome.NO

    def take(self) -> tuple[list[dict], dict[str, str]]:
        updates, resolved = list(self.pending.values()), self.resolved
        self.pending, self.resolved = {}, {}
        return updates, resolved


def save(updates: list[dict], resolved: dict[str, str], assets: int, connections: int) -> list[dict]:
    """Runs on a worker thread: Django's ORM is synchronous. Returns market.update
    payloads for the markets that moved 1 point or more."""
    close_old_connections()
    try:
        moved = record_prices(updates)
        for market_id, outcome in resolved.items():
            Market.objects.filter(id=market_id).update(resolved_outcome=outcome, closed=True, active=False)
        set_stream_heartbeat(assets, connections)
        return push.market_payloads(moved)
    finally:
        close_old_connections()


class Connection:
    """One WebSocket and the tokens it carries. Reconnects by itself."""

    def __init__(self, number: int, collector: PriceCollector):
        self.number = number
        self.collector = collector
        self.assets: set[str] = set()
        self.ws = None
        self.task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self.ws is not None

    async def change(self, add: set[str], remove: set[str]):
        self.assets |= add
        self.assets -= remove
        if self.ws is None:
            return  # the next (re)connect subscribes to the full set
        try:
            if remove:
                await self.ws.send(json.dumps({"assets_ids": sorted(remove), "operation": "unsubscribe"}))
            if add:
                await self.ws.send(
                    json.dumps({"assets_ids": sorted(add), "operation": "subscribe", "custom_feature_enabled": True})
                )
        except Exception as exc:
            log.warning("connection %s: could not change subscription: %s", self.number, exc)

    async def run(self):
        from websockets.asyncio.client import connect

        delay = 1
        while True:
            try:
                async with connect(settings.POLYMARKET_WS_URL, ping_interval=None, open_timeout=20, max_size=2**24) as ws:
                    await ws.send(json.dumps({"assets_ids": sorted(self.assets), "type": "market", "custom_feature_enabled": True}))
                    self.ws, delay = ws, 1
                    log.info("connection %s: subscribed to %s markets", self.number, len(self.assets))
                    pinger = asyncio.create_task(self._ping(ws))
                    try:
                        async for frame in ws:
                            self.collector.handle(frame if isinstance(frame, str) else frame.decode())
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("connection %s dropped (%s); reconnecting in %ss", self.number, exc, delay)
            self.ws = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _ping(self, ws):
        while True:
            await asyncio.sleep(PING_EVERY)
            await ws.send("PING")

    async def close(self):
        if self.task:
            self.task.cancel()


class MarketStream:
    def __init__(self):
        self.collector = PriceCollector()
        self.connections: list[Connection] = []

    async def refresh(self):
        wanted = await sync_to_async(wanted_markets, thread_sensitive=False)()
        self.collector.tokens = wanted
        current = {asset for conn in self.connections for asset in conn.assets}
        add, remove = set(wanted) - current, current - set(wanted)
        for conn in self.connections:
            gone = conn.assets & remove
            if gone:
                await conn.change(set(), gone)
        per_connection = settings.MARKET_STREAM_ASSETS_PER_CONNECTION
        pending = sorted(add)
        for conn in self.connections:
            room = per_connection - len(conn.assets)
            if room > 0 and pending:
                await conn.change(set(pending[:room]), set())
                pending = pending[room:]
        while pending:
            conn = Connection(len(self.connections) + 1, self.collector)
            conn.assets = set(pending[:per_connection])
            pending = pending[per_connection:]
            conn.task = asyncio.create_task(conn.run())
            self.connections.append(conn)
        if add or remove:
            log.info("following %s markets on %s connections (+%s, -%s)", len(wanted), len(self.connections), len(add), len(remove))

    async def flush(self):
        updates, resolved = self.collector.take()
        assets = sum(len(c.assets) for c in self.connections)
        live = sum(1 for c in self.connections if c.connected)
        payloads = await sync_to_async(save, thread_sensitive=False)(updates, resolved, assets, live)
        for payload in payloads:
            await push.asend(push.market_groups(payload), "market.update", payload)

    async def run(self):
        log.info("market_stream starting")
        last_refresh = 0.0
        while True:
            if time.monotonic() - last_refresh >= REFRESH_EVERY:
                try:
                    await self.refresh()
                except Exception:
                    log.exception("could not read the market list; will retry")
                last_refresh = time.monotonic()
            try:
                await self.flush()
            except Exception:
                log.exception("could not save prices; will retry")
            await asyncio.sleep(FLUSH_EVERY)
