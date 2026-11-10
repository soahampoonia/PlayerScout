import { describe, expect, it } from "vitest";
import type { Game, GamesResponse, PlayerDetail } from "./api";
import { applyGameUpdate, applyMoneyline, type MarketUpdate } from "./liveCache";

const team = (id: number, abbreviation: string) => ({ id, abbreviation, city: "", name: abbreviation, full_name: abbreviation });

function game(id: string, extra: Partial<Game> = {}): Game {
  return {
    id,
    season: "2026-27",
    game_date: "2026-11-09",
    tipoff_at: "2026-11-10T00:00:00Z",
    status: "scheduled",
    period: null,
    clock: "",
    home: { ...team(1, "ATL"), score: null },
    away: { ...team(2, "LAL"), score: null },
    win_prob: { home: 0.525, away: 0.475, is_thin: false, volume_24h: 1000, updated_at: null, history: [[1, 0.525]] },
    ...extra,
  };
}

const list: GamesResponse = { date: "2026-11-09", today: "2026-11-09", previous_date: null, next_date: null, games: [game("A"), game("B")] };

describe("game.update", () => {
  const update = { game_id: "A", status: "live" as const, period: 3, clock: "5:42", home_score: 71, away_score: 68 };

  it("patches the game wherever it appears and leaves the rest untouched", () => {
    const next = applyGameUpdate(list, update) as GamesResponse;
    expect(next.games[0]).toMatchObject({ status: "live", period: 3, clock: "5:42", home: { score: 71 }, away: { score: 68 } });
    expect(next.games[1]).toBe(list.games[1]);

    const player = { player: {}, season_averages: null, recent_games: [], next_games: [game("A")] } as unknown as PlayerDetail;
    expect((applyGameUpdate(player, update) as PlayerDetail).next_games[0].home.score).toBe(71);
  });

  it("returns the same object when nothing changes", () => {
    const once = applyGameUpdate(list, update);
    expect(applyGameUpdate(once, update)).toBe(once);
    expect(applyGameUpdate(list, { ...update, game_id: "Z" })).toBe(list);
    expect(applyGameUpdate(undefined, update)).toBe(undefined);
  });
});

describe("market.update", () => {
  const move: MarketUpdate = {
    market_id: "m", game_id: "A", player_id: null, team_id: 2, type: "moneyline", stat: null, line: null,
    prob: 0.55, is_thin: false, updated_at: "2026-11-09T23:00:00Z",
  };

  it("turns a move of the away side into home and away probabilities and extends the history", () => {
    const next = applyMoneyline(list, move) as GamesResponse;
    expect(next.games[0].win_prob).toMatchObject({ home: 0.45, away: 0.55, volume_24h: 1000 });
    expect(next.games[0].win_prob!.history).toEqual([[1, 0.525], [Date.parse("2026-11-09T23:00:00Z"), 0.45]]);
    expect(next.games[1]).toBe(list.games[1]);
  });

  it("ignores props, other games and teams that are not playing", () => {
    expect(applyMoneyline(list, { ...move, type: "player_prop" })).toBe(list);
    expect(applyMoneyline(list, { ...move, game_id: "Z" })).toBe(list);
    expect(applyMoneyline(list, { ...move, team_id: 99 })).toEqual(list);
  });

  it("starts a win probability for a game that had none", () => {
    const bare = { ...list, games: [game("A", { win_prob: null })] };
    const next = applyMoneyline(bare, { ...move, team_id: 1 }) as GamesResponse;
    expect(next.games[0].win_prob).toMatchObject({ home: 0.55, away: 0.45, volume_24h: null });
  });
});
