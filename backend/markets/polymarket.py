"""The only module that talks to Polymarket over HTTP. Read-only market data needs no
account or key. Tests replace these functions with recorded payloads."""

import json
import logging
import time

import requests
from django.conf import settings

log = logging.getLogger(__name__)

RETRIES = 3
_session = requests.Session()
_session.headers.update({"Accept": "application/json", "User-Agent": "player-scout/2"})


class PolymarketUnavailable(Exception):
    """Polymarket did not give a usable answer after all retries."""


def _request(method: str, url: str, **kwargs):
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            response = _session.request(method, url, timeout=settings.POLYMARKET_TIMEOUT, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"HTTP {response.status_code}", response=response)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            log.warning("Polymarket %s attempt %s/%s failed: %s", url, attempt, RETRIES, exc)
            if attempt < RETRIES:
                time.sleep(2**attempt)
    path = url.split("://", 1)[-1].split("?", 1)[0]
    raise PolymarketUnavailable(f"{path}: {type(last_error).__name__}: {last_error}") from last_error


def _gamma(path: str, **params):
    return _request("GET", f"{settings.POLYMARKET_GAMMA_URL}{path}", params=params)


def nba_series_id() -> str:
    """Polymarket's id for the NBA series, from /sports (falls back to the setting)."""
    try:
        for sport in _gamma("/sports"):
            if sport.get("sport") == "nba" and sport.get("series"):
                return str(sport["series"])
    except PolymarketUnavailable:
        log.warning("Could not read /sports; using POLYMARKET_NBA_SERIES_ID")
    return str(settings.POLYMARKET_NBA_SERIES_ID)


def fetch_teams() -> list[dict]:
    """Polymarket's NBA teams: id, abbreviation (lowercase), name. Includes All-Star teams."""
    return _gamma("/teams", league="nba", limit=100)


def iter_open_events(series_id: str, start_min: str, start_max: str, page_size: int = 20):
    """Open NBA events (one per game, each with all its markets) whose start time falls
    in the window. Yields one page at a time: a page of a busy slate is several MB."""
    cursor = None
    while True:
        params = {
            "series_id": series_id,
            "closed": "false",
            "limit": page_size,
            "start_time_min": start_min,
            "start_time_max": start_max,
        }
        if cursor:
            params["after_cursor"] = cursor
        body = _gamma("/events/keyset", **params)
        events = body.get("events") or []
        yield events
        cursor = body.get("next_cursor")
        if not cursor or not events:
            return


def fetch_midpoints(token_ids: list[str]) -> dict[str, float]:
    """Midpoint of the order book for each token. Up to 500 per request."""
    result: dict[str, float] = {}
    for start in range(0, len(token_ids), 500):
        batch = token_ids[start : start + 500]
        body = _request("POST", f"{settings.POLYMARKET_CLOB_URL}/midpoints", json=[{"token_id": t} for t in batch])
        result.update({token: float(mid) for token, mid in body.items() if mid not in (None, "")})
    return result


def fetch_spreads(token_ids: list[str]) -> dict[str, float]:
    """Bid/ask spread for each token. Up to 500 per request."""
    result: dict[str, float] = {}
    for start in range(0, len(token_ids), 500):
        batch = token_ids[start : start + 500]
        body = _request("POST", f"{settings.POLYMARKET_CLOB_URL}/spreads", json=[{"token_id": t} for t in batch])
        result.update({token: float(spread) for token, spread in body.items() if spread not in (None, "")})
    return result


def json_list(value) -> list:
    """Gamma sends some arrays as JSON-encoded strings ("[\\"Yes\\", \\"No\\"]")."""
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []
