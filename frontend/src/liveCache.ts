// How live pushes change data already in the query cache. Pure functions, so they
// are easy to test: each returns the same object when nothing in it changed.

import type { Game, WinProb } from "./api";

export interface GameUpdate {
  game_id: string;
  status: Game["status"];
  period: number | null;
  clock: string;
  home_score: number | null;
  away_score: number | null;
}

export interface MarketUpdate {
  market_id: string;
  game_id: string | null;
  player_id: number | null;
  team_id: number | null;
  type: "moneyline" | "spread" | "total" | "player_prop";
  stat: string | null;
  line: number | null;
  prob: number;
  is_thin: boolean;
  updated_at: string | null;
}

const HISTORY_POINTS = 60;

function isGame(value: unknown): value is Game {
  if (!value || typeof value !== "object") return false;
  const g = value as Partial<Game>;
  return typeof g.id === "string" && !!g.home && !!g.away && "status" in g;
}

/** Copy `value`, replacing every game card with id `gameId` by `change(card)`. */
export function mapGames(value: unknown, gameId: string, change: (game: Game) => Game): unknown {
  if (Array.isArray(value)) {
    let changed = false;
    const next = value.map((item) => {
      const mapped = mapGames(item, gameId, change);
      if (mapped !== item) changed = true;
      return mapped;
    });
    return changed ? next : value;
  }
  if (!value || typeof value !== "object") return value;
  if (isGame(value) && value.id === gameId) return change(value);
  let changed = false;
  const next: Record<string, unknown> = {};
  for (const [key, item] of Object.entries(value)) {
    const mapped = mapGames(item, gameId, change);
    if (mapped !== item) changed = true;
    next[key] = mapped;
  }
  return changed ? next : value;
}

export function applyGameUpdate(value: unknown, u: GameUpdate): unknown {
  return mapGames(value, u.game_id, (game) => {
    if (
      game.status === u.status &&
      game.period === u.period &&
      game.clock === u.clock &&
      game.home.score === u.home_score &&
      game.away.score === u.away_score
    ) {
      return game;
    }
    return {
      ...game,
      status: u.status,
      period: u.period,
      clock: u.clock,
      home: { ...game.home, score: u.home_score },
      away: { ...game.away, score: u.away_score },
    };
  });
}

/** A moneyline move: the game's win probability, and a new point on its history line. */
export function applyMoneyline(value: unknown, u: MarketUpdate): unknown {
  if (u.type !== "moneyline" || !u.game_id) return value;
  return mapGames(value, u.game_id, (game) => {
    if (u.team_id !== game.home.id && u.team_id !== game.away.id) return game;
    const home = Math.round((u.team_id === game.home.id ? u.prob : 1 - u.prob) * 10000) / 10000;
    const time = u.updated_at ? Date.parse(u.updated_at) : Date.now();
    const previous: WinProb | null = game.win_prob;
    const history = [...(previous?.history ?? []), [time, home] as [number, number]].slice(-HISTORY_POINTS);
    return {
      ...game,
      win_prob: {
        volume_24h: previous?.volume_24h ?? null,
        ...previous,
        home,
        away: Math.round((1 - home) * 10000) / 10000,
        is_thin: u.is_thin,
        updated_at: u.updated_at,
        history,
      },
    };
  });
}
