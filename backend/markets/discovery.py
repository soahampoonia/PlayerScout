"""discover_markets: find Polymarket's NBA markets and tie them to our games, teams
and players.

Polymarket has one event per game (slug "nba-lal-atl-2026-11-09": away, home, date
in Eastern time) holding every market for that game. Player Scout keeps the ones it
uses: moneyline, spreads, totals, and player points / rebounds / assists.

Matching (stored on each market as match_method + match_confidence):
- game: Eastern date plus the two teams (team_date);
- player props: the name in the title ("Luka Dončić: Points O/U 34.5") against the
  two rosters in that game (roster_name). A match below MARKET_MATCH_THRESHOLD stays
  out of the app and shows up in the admin's market list instead;
- a market fixed by hand in the admin (manual) is never re-matched."""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Q

from markets import polymarket
from markets.models import Market
from markets.pricing import record_prices
from nba.ingest.runs import ingestion_run
from nba.models import Game, Player, Team
from nba.textnorm import search_key

log = logging.getLogger(__name__)

EVENT_SLUG = re.compile(r"^nba-([a-z]{2,4})-([a-z]{2,4})-(\d{4}-\d{2}-\d{2})$")
PROP_TITLE = re.compile(r"^(?P<name>.+?):\s*(?P<stat>Points|Rebounds|Assists)\s+O/U\s+(?P<line>\d+(?:\.\d+)?)\s*$", re.I)
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

TYPES = {
    "moneyline": (Market.MarketType.MONEYLINE, None),
    "spreads": (Market.MarketType.SPREAD, None),
    "totals": (Market.MarketType.TOTAL, None),
    "points": (Market.MarketType.PLAYER_PROP, Market.Stat.PTS),
    "rebounds": (Market.MarketType.PLAYER_PROP, Market.Stat.REB),
    "assists": (Market.MarketType.PLAYER_PROP, Market.Stat.AST),
}

FINGERPRINT = [
    "condition_id", "event_id", "event_slug", "question", "market_type", "stat", "line", "game_id", "player_id",
    "team_id", "match_method", "match_confidence", "yes_token_id", "no_token_id", "active", "closed", "resolved_outcome",
]  # fmt: skip

UPDATE_FIELDS = [
    "condition_id", "event_id", "event_slug", "question", "market_type", "stat", "line", "game", "player", "team",
    "match_method", "match_confidence", "yes_token_id", "no_token_id", "active", "closed", "resolved_outcome", "raw",
    "updated_at",
]  # fmt: skip


# ---- Teams -------------------------------------------------------------------


def sync_teams(rows: list[dict]) -> int:
    """Store Polymarket's team id on our teams, matched by abbreviation (lal -> LAL)."""
    ours = {team.abbreviation: team for team in Team.objects.all()}
    changed = []
    for row in rows:
        team = ours.get(str(row.get("abbreviation") or "").upper())
        if team and team.polymarket_team_id != str(row["id"]):
            team.polymarket_team_id = str(row["id"])
            changed.append(team)
    Team.objects.bulk_update(changed, ["polymarket_team_id"])
    return len(changed)


class TeamIndex:
    """Find our Team from what Polymarket gives: an abbreviation, a team id or a nickname."""

    def __init__(self):
        teams = list(Team.objects.all())
        self.by_abbr = {t.abbreviation: t for t in teams}
        self.by_pm_id = {t.polymarket_team_id: t for t in teams if t.polymarket_team_id}
        self.by_name = {search_key(t.name): t for t in teams}

    def find(self, abbreviation=None, pm_id=None, name=None) -> Team | None:
        if pm_id is not None and str(pm_id) in self.by_pm_id:
            return self.by_pm_id[str(pm_id)]
        if abbreviation and abbreviation.upper() in self.by_abbr:
            return self.by_abbr[abbreviation.upper()]
        if name:
            return self.by_name.get(search_key(name))
        return None


# ---- Games -------------------------------------------------------------------


def event_teams(event: dict, teams: TeamIndex) -> tuple[Team | None, Team | None, dict[str, Team]]:
    """(away, home, {outcome name -> Team}) for an event."""
    away = home = None
    by_name: dict[str, Team] = {}
    for row in event.get("teams") or []:
        team = teams.find(row.get("abbreviation"), row.get("id"), row.get("name"))
        if not team:
            continue
        for label in (row.get("name"), row.get("alias")):
            if label:
                by_name[search_key(label)] = team
        if row.get("ordering") == "home":
            home = team
        elif row.get("ordering") == "away":
            away = team
    slug = EVENT_SLUG.match(event.get("slug") or "")
    if slug and (away is None or home is None):
        away = away or teams.find(slug.group(1))
        home = home or teams.find(slug.group(2))
    for team in (away, home):
        if team:
            by_name.setdefault(search_key(team.name), team)
    return away, home, by_name


def event_date(event: dict) -> date | None:
    raw = event.get("eventDate")
    if not raw:
        slug = EVENT_SLUG.match(event.get("slug") or "")
        raw = slug.group(3) if slug else None
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def match_game(event: dict, away: Team | None, home: Team | None) -> tuple[Game | None, float]:
    day = event_date(event)
    if not (day and away and home):
        return None, 0.0
    pair = Q(home_team=home, away_team=away) | Q(home_team=away, away_team=home)
    for offset, confidence in ((0, 1.0), (-1, 0.8), (1, 0.8)):
        game = Game.objects.filter(pair, game_date=day + timedelta(days=offset)).first()
        if game:
            if game.home_team_id != home.id:  # Polymarket has the sides the other way round
                confidence -= 0.1
            return game, confidence
    return None, 0.0


# ---- Players -----------------------------------------------------------------


def _name_parts(key: str) -> tuple[str, str]:
    """("herbert", "jones") from "herbert jones"; suffixes like jr / iii are dropped."""
    words = [w for w in key.split() if w not in SUFFIXES]
    if not words:
        return "", ""
    return words[0], words[-1]


def match_player(name: str, candidates: list[Player]) -> tuple[Player | None, float]:
    """Best player for a name, and how sure we are (1.0 exact, 0.85 nickname, 0.7 last name only)."""
    key = search_key(name)
    if not key or not candidates:
        return None, 0.0

    def keys_of(player: Player) -> set[str]:
        return {player.search_name, *(search_key(alias) for alias in player.name_aliases or [])}

    exact = [p for p in candidates if key in keys_of(p)]
    if len(exact) == 1:
        return exact[0], 1.0

    first, last = _name_parts(key)
    stripped = [p for p in candidates if any(_name_parts(k) == (first, last) for k in keys_of(p))]
    if len(stripped) == 1:  # "Jabari Smith" vs "Jabari Smith Jr."
        return stripped[0], 0.95

    same_last = [p for p in candidates if _name_parts(p.search_name)[1] == last]
    nickname = [p for p in same_last if (f := _name_parts(p.search_name)[0]).startswith(first) or first.startswith(f)]
    if len(nickname) == 1:  # "Herb Jones" vs "Herbert Jones"
        return nickname[0], 0.85
    if len(same_last) == 1:
        return same_last[0], 0.7
    return None, 0.0


class Rosters:
    """Candidate players per game: both teams' current rosters, plus anyone who has a box
    score in that game (a player traded since then)."""

    def __init__(self):
        self._cache: dict[str, list[Player]] = {}
        self._everyone: list[Player] | None = None

    def for_game(self, game: Game) -> list[Player]:
        if game.id not in self._cache:
            self._cache[game.id] = list(
                Player.objects.filter(
                    Q(is_active=True, team_id__in=[game.home_team_id, game.away_team_id]) | Q(game_logs__game=game)
                ).distinct()
            )
        return self._cache[game.id]

    def everyone(self) -> list[Player]:
        if self._everyone is None:
            self._everyone = list(Player.objects.filter(is_active=True))
        return self._everyone


# ---- Markets -----------------------------------------------------------------


def _decimal(value) -> Decimal | None:
    try:
        return None if value is None or value == "" else Decimal(str(value))
    except InvalidOperation:
        return None


def resolved_outcome(market: dict) -> str | None:
    if not market.get("closed") and market.get("umaResolutionStatus") != "resolved":
        return None
    prices = polymarket.json_list(market.get("outcomePrices"))
    try:
        yes, no = float(prices[0]), float(prices[1])
    except (IndexError, TypeError, ValueError):
        return None
    if yes >= 0.99 and no <= 0.01:
        return Market.Outcome.YES
    if no >= 0.99 and yes <= 0.01:
        return Market.Outcome.NO
    if abs(yes - 0.5) < 0.01 and abs(no - 0.5) < 0.01:
        return Market.Outcome.VOID
    return None


def build_market(event: dict, raw: dict, game: Game | None, game_conf: float, by_name: dict, rosters: Rosters):
    """A Market (unsaved) for one Gamma market, or None for a type we do not track."""
    kind = TYPES.get(raw.get("sportsMarketType"))
    tokens = polymarket.json_list(raw.get("clobTokenIds"))
    if not kind or len(tokens) < 2:
        return None
    market_type, stat = kind
    outcomes = polymarket.json_list(raw.get("outcomes"))
    line = _decimal(raw.get("line"))
    team = player = None
    method, confidence = (Market.MatchMethod.TEAM_DATE, game_conf) if game else (Market.MatchMethod.UNMATCHED, 0.0)

    if market_type in (Market.MarketType.MONEYLINE, Market.MarketType.SPREAD) and outcomes:
        team = by_name.get(search_key(str(outcomes[0])))  # the YES side's team
        if team is None:
            method, confidence = Market.MatchMethod.UNMATCHED, 0.0

    if market_type == Market.MarketType.PLAYER_PROP:
        title = PROP_TITLE.match(raw.get("question") or "")
        if not title:
            return None
        line = line if line is not None else _decimal(title.group("line"))
        if game:
            player, name_conf = match_player(title.group("name"), rosters.for_game(game))
            if player is None:  # not on either roster: maybe signed or traded since the last roster sync
                player, name_conf = match_player(title.group("name"), rosters.everyone())
                name_conf = min(name_conf, 0.8)
        else:
            player, name_conf = None, 0.0
        if player:
            team = player.team_id
            method, confidence = Market.MatchMethod.ROSTER_NAME, round(min(game_conf, name_conf), 2)
        else:
            method, confidence = Market.MatchMethod.UNMATCHED, 0.0

    return Market(
        id=str(raw["id"]),
        condition_id=raw.get("conditionId") or str(raw["id"]),
        event_id=str(event.get("id") or ""),
        event_slug=event.get("slug") or "",
        question=raw.get("question") or "",
        market_type=market_type,
        stat=stat,
        line=line,
        game=game,
        player=player,
        team_id=team if isinstance(team, int) or team is None else team.id,
        match_method=method,
        match_confidence=Decimal(str(round(confidence, 2))),
        yes_token_id=str(tokens[0]),
        no_token_id=str(tokens[1]),
        active=bool(raw.get("active")) and not raw.get("closed"),
        closed=bool(raw.get("closed")),
        resolved_outcome=resolved_outcome(raw),
        raw=raw,
    )


def apply_events(events: list[dict], teams: TeamIndex | None = None, rosters: Rosters | None = None) -> tuple[int, set[str]]:
    """Upsert the markets of these Gamma events. Returns (markets written, event ids seen)."""
    teams = teams or TeamIndex()
    rosters = rosters or Rosters()
    markets, prices, seen = [], [], set()
    for event in events:
        if not EVENT_SLUG.match(event.get("slug") or ""):
            continue  # "...-exact-margin" and other side events
        seen.add(str(event.get("id")))
        away, home, by_name = event_teams(event, teams)
        game, game_conf = match_game(event, away, home)
        for raw in event.get("markets") or []:
            market = build_market(event, raw, game, game_conf, by_name, rosters)
            if market is None:
                continue
            markets.append(market)
            if not market.closed:
                prices.append(
                    {
                        "market_id": market.id,
                        "bid": raw.get("bestBid"),
                        "ask": raw.get("bestAsk"),
                        "last": raw.get("lastTradePrice"),
                        "volume_24h": raw.get("volume24hr") or 0,
                        "liquidity": raw.get("liquidityNum"),
                    }
                )
    if not markets:
        return 0, seen

    # Never undo a match someone fixed by hand.
    manual = {
        m.id: m for m in Market.objects.filter(id__in=[m.id for m in markets], match_method=Market.MatchMethod.MANUAL)
    }
    for market in markets:
        fixed = manual.get(market.id)
        if fixed:
            market.game_id, market.player_id, market.team_id = fixed.game_id, fixed.player_id, fixed.team_id
            market.match_method, market.match_confidence = fixed.match_method, fixed.match_confidence

    # Write only markets that are new or whose matching or state changed: prices change
    # every run, but they live in the price cache, not in this table.
    stored = {
        row["id"]: _fingerprint(row)
        for row in Market.objects.filter(id__in=[m.id for m in markets]).values("id", *FINGERPRINT)
    }
    changed = [m for m in markets if stored.get(m.id) != _fingerprint(m)]
    if changed:
        Market.objects.bulk_create(
            changed, update_conflicts=True, unique_fields=["id"], update_fields=UPDATE_FIELDS, batch_size=500
        )
    record_prices(prices, seed=True)
    return len(changed), seen


def _fingerprint(market) -> tuple:
    """What discovery decides about a market: everything but prices."""
    get = market.get if isinstance(market, dict) else lambda name: getattr(market, name)
    values = []
    for name in FINGERPRINT:
        value = get(name)
        if name == "line" and value is not None:
            value = Decimal(str(value)).normalize()
        if name == "match_confidence" and value is not None:
            value = Decimal(str(value)).quantize(Decimal("0.01"))
        values.append(value)
    return tuple(values)


def discover_markets() -> int:
    with ingestion_run("discover_markets") as run:
        if Team.objects.filter(polymarket_team_id__isnull=True).exists():
            sync_teams(polymarket.fetch_teams())
        series = polymarket.nba_series_id()
        now = datetime.now(timezone.utc)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        # Games that have tipped off are skipped: their markets are already known, the
        # price stream keeps their prices, and on a game night they are most of the
        # download (measured: 12.6 of 17.3 MB at 9:30 pm ET).
        start_min = now.strftime(fmt)
        start_max = (now + timedelta(days=settings.MARKET_DISCOVERY_DAYS_AHEAD)).strftime(fmt)

        teams, rosters = TeamIndex(), Rosters()
        seen: set[str] = set()
        for page in polymarket.iter_open_events(series, start_min, start_max):
            written, page_seen = apply_events(page, teams, rosters)
            run.rows_written += written
            seen |= page_seen

        # Events that dropped out of the open list have closed (or were pulled). Games
        # already under way are not in the list at all, so they are left alone here.
        if seen:
            missing = Market.objects.filter(closed=False).exclude(event_id__in=seen).values("id", "game__tipoff_at", "raw__gameStartTime")
            gone = [row["id"] for row in missing if _starts_after(row, now)]
            Market.objects.filter(id__in=gone).update(closed=True, active=False)
        return run.rows_written


def _starts_after(row: dict, now: datetime) -> bool:
    tipoff = row.get("game__tipoff_at")
    if tipoff is None and row.get("raw__gameStartTime"):
        try:
            tipoff = datetime.fromisoformat(str(row["raw__gameStartTime"]).replace(" ", "T").replace("+00", "+00:00"))
        except ValueError:
            tipoff = None
    return tipoff is None or tipoff >= now
