// Types and fetchers for the REST API (backend/nba/api.py builds these shapes).

export interface TeamRef {
  id: number;
  abbreviation: string;
  city: string;
  name: string;
  full_name: string;
}

export type GameStatus = "scheduled" | "live" | "final" | "postponed";

export interface GameSide extends TeamRef {
  score: number | null;
}

/** Polymarket moneyline, as probabilities that each side wins. */
export interface WinProb {
  home: number;
  away: number;
  is_thin: boolean;
  volume_24h: number | null;
  updated_at: string | null;
  /** Home team's win probability over the last 24 h: [unix ms, probability]. Games list only. */
  history?: [number, number][];
}

export type PropStat = "pts" | "reb" | "ast";

/** One Polymarket market with its latest price. prob is for the YES side:
 *  the team covering (spread), the over (total), or the player going over (prop). */
export interface MarketLine {
  id: string;
  question: string;
  type: "moneyline" | "spread" | "total" | "player_prop";
  stat: PropStat | null;
  line: number | null;
  team_id: number | null;
  player_id: number | null;
  prob: number | null;
  bid: number | null;
  ask: number | null;
  spread: number | null;
  volume_24h: number | null;
  liquidity: number | null;
  is_thin: boolean;
  updated_at: string | null;
}

export interface Game {
  id: string;
  season: string;
  game_date: string; // YYYY-MM-DD, Eastern time
  tipoff_at: string | null; // ISO, UTC
  status: GameStatus;
  period: number | null;
  clock: string;
  home: GameSide;
  away: GameSide;
  win_prob: WinProb | null;
}

export interface Averages {
  season: string;
  games: number;
  minutes: number | null;
  pts: number | null;
  reb: number | null;
  ast: number | null;
  stl: number | null;
  blk: number | null;
  tov: number | null;
  fg3m: number | null;
}

export interface BoxLine {
  minutes: number | null;
  pts: number;
  reb: number;
  ast: number;
  stl: number;
  blk: number;
  tov: number;
  fg3m: number;
  fgm: number;
  fga: number;
  ftm: number;
  fta: number;
  plus_minus: number;
}

export interface PlayerRef {
  id: number;
  name: string;
  position: string;
  jersey: string;
  averages: Averages | null;
}

export interface GamePlayer extends PlayerRef {
  line: BoxLine | null;
  /** Main prop line per stat, with how many alternate lines exist. */
  lines: Partial<Record<PropStat, MarketLine & { alternates: number }>>;
}

export interface GamesResponse {
  date: string;
  today: string;
  previous_date: string | null;
  next_date: string | null;
  games: Game[];
}

export interface GameMarkets {
  moneyline: WinProb | null;
  spread: MarketLine | null;
  total: MarketLine | null;
  props_count: number;
}

export interface GameDetail {
  game: Game;
  has_box_score: boolean;
  home: TeamRef & { players: GamePlayer[] };
  away: TeamRef & { players: GamePlayer[] };
  /** Null once the game is final. */
  markets: GameMarkets | null;
}

export interface Projection {
  mean: number;
  sd: number;
  minutes: number;
  rate: number;
  adjustment: number;
}

export type PlayerLine = MarketLine & {
  is_main: boolean;
  /** baseline model's chance of going over, and model minus market. */
  model?: number | null;
  edge?: number | null;
};

export interface PlayerMarkets {
  games: {
    game: Game;
    lines: Record<PropStat, PlayerLine[]> | null;
    projection: Partial<Record<PropStat, Projection>>;
    model_version: string;
    data_gaps: string[];
  }[];
}

export interface GameLog extends BoxLine {
  game_id: string;
  game_date: string;
  season: string;
  opponent: { id: number; abbreviation: string };
  is_home: boolean;
  result: "W" | "L" | null;
  team_score: number | null;
  opponent_score: number | null;
}

export interface PlayerDetail {
  player: {
    id: number;
    name: string;
    first_name: string;
    last_name: string;
    position: string;
    jersey: string;
    is_active: boolean;
    team: TeamRef | null;
  };
  season_averages: Averages | null;
  recent_games: GameLog[]; // newest first
  next_games: Game[];
}

export interface TeamStats {
  season: string;
  as_of: string;
  pace: number | null;
  off_rating: number | null;
  def_rating: number | null;
  opp_pts: number | null;
  opp_reb: number | null;
  opp_ast: number | null;
}

export interface TeamDetail {
  team: TeamRef & { conference: string };
  season: string;
  record: { wins: number; losses: number };
  stats: TeamStats | null;
  roster: PlayerRef[];
  recent_games: Game[];
  next_games: Game[];
}

export interface SearchEntry {
  type: "team" | "player";
  id: number;
  name: string;
  search_name: string;
  team: string | null;
}

export interface SearchIndex {
  teams: SearchEntry[];
  players: SearchEntry[];
}

export interface JobStatus {
  job: string;
  status: "never_run" | "running" | "ok" | "failed";
  started_at: string | null;
  finished_at: string | null;
  rows_written: number | null;
  error: string | null;
  last_success_at: string | null;
}

export interface StatusResponse {
  season: string;
  today: string;
  counts: {
    teams: number;
    active_players: number;
    games: number;
    player_game_logs: number;
    team_stat_snapshots: number;
    markets_open: number;
    markets_matched: number;
  };
  jobs: JobStatus[];
  market_stream: { ts: number; assets: number; connections: number } | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export type AnalysisStatus = "queued" | "running" | "done" | "failed";

export interface AnalysisBrief {
  id: string;
  player: { id: number; name: string; team: string | null };
  status: AnalysisStatus;
  created_at: string;
  completed_at: string | null;
  summary: string;
  is_stale: boolean;
  stale_reason: string | null;
  superseded: boolean;
  llm_model: string;
  error: string | null;
}

export interface AnalysisPrediction {
  stat: PropStat;
  line: number;
  model_prob: number | null;
  market_prob: number | null;
  market_is_thin: boolean | null;
  line_source: "polymarket" | "model";
  llm_prob: number;
  confidence: "low" | "medium" | "high";
  reasons: string[];
  outcome: "over" | "under" | "void" | null;
  actual_value: number | null;
}

export interface Analysis extends AnalysisBrief {
  outlook: string;
  risks: string[];
  data_gaps: string[];
  games: {
    game_id: string;
    date: string;
    opponent: string;
    home_or_away: "home" | "away";
    status: string | null;
    predictions: AnalysisPrediction[];
  }[];
  llm_provider: string;
  prompt_version: string;
  tokens_in: number | null;
  tokens_out: number | null;
  latency_ms: number | null;
  cost_usd: number | null;
}

export interface AnalysisList {
  items: AnalysisBrief[];
  next_before: string | null;
}

async function post<T>(path: string, body: unknown): Promise<{ status: number; data: T }> {
  const response = await fetch(`/api${path}`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) {
    const detail = (data as { detail?: string } | null)?.detail;
    throw new ApiError(response.status, detail ?? `${response.status} from /api${path}`);
  }
  return { status: response.status, data: data as T };
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new ApiError(response.status, `${response.status} from /api${path}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  games: (date?: string | null) => get<GamesResponse>(date ? `/games?date=${encodeURIComponent(date)}` : "/games"),
  game: (id: string) => get<GameDetail>(`/games/${encodeURIComponent(id)}`),
  player: (id: string) => get<PlayerDetail>(`/players/${encodeURIComponent(id)}`),
  playerMarkets: (id: string) => get<PlayerMarkets>(`/players/${encodeURIComponent(id)}/markets`),
  team: (id: string) => get<TeamDetail>(`/teams/${encodeURIComponent(id)}`),
  searchIndex: () => get<SearchIndex>("/search-index"),
  status: () => get<StatusResponse>("/status"),
  analyze: (playerId: number, force = false) =>
    post<{ analysis: AnalysisBrief | Analysis; cached: boolean }>(`/players/${playerId}/analyses`, { force }),
  analysis: (id: string) => get<Analysis>(`/analyses/${encodeURIComponent(id)}`),
  analyses: (params: { playerId?: number; before?: string | null; limit?: number }) => {
    const q = new URLSearchParams();
    if (params.playerId) q.set("player_id", String(params.playerId));
    if (params.before) q.set("before", params.before);
    if (params.limit) q.set("limit", String(params.limit));
    return get<AnalysisList>(`/analyses?${q.toString()}`);
  },
};
