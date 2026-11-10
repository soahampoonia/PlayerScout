import { Link } from "react-router-dom";
import type { Game, GameSide } from "../api";
import { THIN_HINT, pct, pctPair, periodLabel, shortDay, tipoffTime } from "../format";
import { useFlash } from "../useFlash";
import { Sparkline } from "./Sparkline";

export function GameStatusLabel({ game }: { game: Game }) {
  if (game.status === "live") {
    return (
      <span className="status live">
        <span className="live-dot" aria-hidden="true" />
        {liveClock(game)}
      </span>
    );
  }
  if (game.status === "final") return <span className="status">Final</span>;
  if (game.status === "postponed") return <span className="status">Postponed</span>;
  return <span className="status">{tipoffTime(game.tipoff_at)}</span>;
}

/** "Q3 5:42", "Q4 38.6", or the feed's own words between periods ("Half", "End Q1"). */
export function liveClock(game: Game): string {
  const clock = game.clock ?? "";
  if (!clock) return periodLabel(game.period);
  return /^\d/.test(clock) ? `${periodLabel(game.period)} ${clock}` : clock;
}

/** A score that lights up for a moment when it changes. */
export function Score({ value, className }: { value: number | null; className: string }) {
  const flash = useFlash(value);
  return <span className={flash ? `${className} flash` : className}>{value ?? ""}</span>;
}

/** True/false once the game is final, null before that. Shown by weight, not colour. */
export function won(game: Game, side: "home" | "away"): boolean | null {
  const { home, away } = game;
  if (game.status !== "final" || home.score === null || away.score === null) return null;
  return side === "home" ? home.score > away.score : away.score > home.score;
}

interface RowProps {
  side: GameSide;
  result: boolean | null;
  showScore: boolean;
  prob: string | null;
  thin: boolean;
}

function Row({ side, result, showScore, prob, thin }: RowProps) {
  const cls = result === null ? "team-row" : result ? "team-row winner" : "team-row loser";
  return (
    <div className={cls}>
      <span className="team-abbr">{side.abbreviation}</span>
      <span className="team-name">{side.name}</span>
      {showScore ? (
        <Score value={side.score} className="team-score" />
      ) : (
        <span className={thin ? "team-prob thin" : "team-prob"}>{prob ?? ""}</span>
      )}
    </div>
  );
}

export function GameCard({ game }: { game: Game }) {
  const showScore = game.status === "live" || game.status === "final";
  const wp = game.win_prob;
  const [awayPct, homePct] = wp ? pctPair(wp.away) : [null, null];
  return (
    <Link to={`/games/${game.id}`} className="card game-card">
      <div className="game-card-top">
        <GameStatusLabel game={game} />
        {!showScore && wp && (
          <span className="card-hint" title={wp.is_thin ? THIN_HINT : "Chance to win, from Polymarket"}>
            {wp.is_thin ? "Win % · thin" : "Win %"}
          </span>
        )}
      </div>
      <Row side={game.away} result={won(game, "away")} showScore={showScore} prob={awayPct} thin={!!wp?.is_thin} />
      <Row side={game.home} result={won(game, "home")} showScore={showScore} prob={homePct} thin={!!wp?.is_thin} />
      {wp && game.status !== "final" && wp.history && wp.history.length > 1 && (
        <div className={wp.is_thin ? "card-trend thin" : "card-trend"}>
          <span className="trend-label">{game.home.abbreviation} to win</span>
          <Sparkline
            points={wp.history}
            label={`${game.home.abbreviation} win probability over the last day, from ${pct(wp.history[0][1])} to ${pct(wp.home)}`}
          />
          <span className="trend-value">{homePct}</span>
        </div>
      )}
    </Link>
  );
}

/** One line per game, from one team's point of view: "Nov 8  @ BOS  W 112–104". */
export function TeamGameRow({ game, teamId }: { game: Game; teamId: number }) {
  const isHome = game.home.id === teamId;
  const us = isHome ? game.home : game.away;
  const them = isHome ? game.away : game.home;
  const result = won(game, isHome ? "home" : "away");
  return (
    <Link to={`/games/${game.id}`} className="list-row">
      <span className="list-date">{shortDay(game.game_date)}</span>
      <span className="list-main">
        <span className="muted">{isHome ? "vs" : "@"}</span> {them.full_name}
      </span>
      <span className="list-end">
        {result !== null && us.score !== null && them.score !== null ? (
          <>
            <span className={result ? "result win" : "result"}>{result ? "W" : "L"}</span> {us.score}–{them.score}
          </>
        ) : (
          <GameStatusLabel game={game} />
        )}
      </span>
    </Link>
  );
}
