import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type GameLog, type PlayerLine, type Projection, type PropStat } from "../api";
import { THIN_HINT, ago, dollars, monthDay, pct, stat } from "../format";
import { useLiveConnected, useLiveSubscription } from "../live";

const STATS: { key: PropStat; label: string }[] = [
  { key: "pts", label: "Points" },
  { key: "reb", label: "Rebounds" },
  { key: "ast", label: "Assists" },
];

interface Props {
  playerId: string;
  teamId: number | null;
  recent: GameLog[];
}

const MODEL_HINT =
  "Baseline model: recent per-minute production times projected minutes, adjusted for the opponent and pace. It does not know about injuries yet.";

/** The player's next games: the model's projection, and each Polymarket line with the
 *  market's and the model's chance of going over. */
export function PlayerLines({ playerId, teamId, recent }: Props) {
  const connected = useLiveConnected();
  useLiveSubscription([], [Number(playerId)].filter((n) => Number.isInteger(n) && n > 0));
  const query = useQuery({
    queryKey: ["player-markets", playerId],
    queryFn: () => api.playerMarkets(playerId),
    refetchInterval: connected ? false : 30_000,
  });
  const [showAll, setShowAll] = useState(false);

  const games = (query.data?.games ?? []).filter((entry) => entry.lines || Object.keys(entry.projection).length > 0);
  if (games.length === 0) return null;
  const rowsOf = (entry: (typeof games)[number]) => STATS.flatMap((s) => entry.lines?.[s.key] ?? []);
  const alternates = games.reduce((n, entry) => n + rowsOf(entry).filter((r) => !r.is_main).length, 0);
  const gaps = [...new Set(games.flatMap((entry) => entry.data_gaps))];
  const updated = games
    .flatMap(rowsOf)
    .map((r) => r.updated_at)
    .filter(Boolean)
    .sort()
    .pop();

  return (
    <section className="card">
      <div className="card-head">
        <div>
          <h2 className="card-title flush">Model vs market</h2>
          <p className="chart-key">
            Chance of going over each Polymarket line{updated && ` · prices updated ${ago(updated)}`}
          </p>
        </div>
        {alternates > 0 && (
          <button type="button" className="button" aria-pressed={showAll} onClick={() => setShowAll(!showAll)}>
            {showAll ? "Main lines only" : `Show ${alternates} alternate lines`}
          </button>
        )}
      </div>
      {games.map(({ game, lines, projection, model_version }) => {
        const home = game.home.id === teamId;
        const opponent = home ? game.away : game.home;
        const hasLines = !!lines && STATS.some((s) => lines[s.key].length > 0);
        return (
          <div key={game.id} className="lines-game">
            <h3 className="lines-title">
              {monthDay(game.game_date)} · {home ? "vs" : "@"} {opponent.full_name}
            </h3>
            <ProjectionRow projection={projection} version={model_version} />
            {!hasLines && <p className="muted pad small">No Polymarket lines for this game yet.</p>}
            {hasLines && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="left">Stat</th>
                    <th>Line</th>
                    <th title="Polymarket's chance of going over">Market</th>
                    <th title={MODEL_HINT}>Model</th>
                    <th title="Model minus market, in percentage points">Edge</th>
                    <th>Bid / ask</th>
                    <th>24 h volume</th>
                    <th>Over in last {Math.min(recent.length, 10)}</th>
                  </tr>
                </thead>
                <tbody>
                  {STATS.flatMap(({ key, label }) =>
                    lines![key]
                      .filter((row) => showAll || row.is_main)
                      .map((row) => (
                        <LineRow key={row.id} row={row} label={label} stat={key} recent={recent} showAll={showAll} />
                      )),
                  )}
                </tbody>
              </table>
            </div>
            )}
          </div>
        );
      })}
      {gaps.length > 0 && <p className="lines-gaps muted">Not in the model yet: {gaps.map((g) => g.split(":")[0]).join(", ")}.</p>}
    </section>
  );
}

const SHORT: Record<PropStat, string> = { pts: "pts", reb: "reb", ast: "ast" };

function ProjectionRow({ projection, version }: { projection: Partial<Record<PropStat, Projection>>; version: string }) {
  const stats = STATS.filter((s) => projection[s.key]);
  if (stats.length === 0) return null;
  const minutes = projection[stats[0].key]!.minutes;
  return (
    <p className="projection" title={MODEL_HINT}>
      <span className="muted">Model ({version}):</span>{" "}
      {stats.map(({ key }, i) => (
        <span key={key}>
          {i > 0 && " · "}
          <strong>{stat(projection[key]!.mean)}</strong> {SHORT[key]}
          <span className="muted"> ±{stat(projection[key]!.sd)}</span>
        </span>
      ))}
      <span className="muted"> · about {Math.round(minutes)} min</span>
    </p>
  );
}

function edgeText(edge: number | null | undefined): string {
  if (edge === null || edge === undefined) return "–";
  const points = Math.round(edge * 100);
  return points > 0 ? `+${points}` : points < 0 ? `−${Math.abs(points)}` : "0";
}

function LineRow(props: { row: PlayerLine; label: string; stat: PropStat; recent: GameLog[]; showAll: boolean }) {
  const { row, label, stat: key, recent, showAll } = props;
  const bigEdge = !row.is_thin && row.edge !== null && row.edge !== undefined && Math.abs(row.edge) >= 0.05;
  const last = recent.slice(0, 10);
  const over = row.line === null ? null : last.filter((game) => game[key] > row.line!).length;
  const classes = [row.is_thin ? "thin" : "", showAll && row.is_main ? "main-line" : ""].filter(Boolean).join(" ");
  return (
    <tr className={classes || undefined} title={row.is_thin ? THIN_HINT : undefined}>
      <td className="left">
        {label}
        {showAll && row.is_main && <span className="pos">main</span>}
        {row.is_thin && <span className="pos">thin</span>}
      </td>
      <td className="strong">{row.line ?? "–"}</td>
      <td className="strong">{pct(row.prob)}</td>
      <td className="strong">{pct(row.model)}</td>
      <td className={bigEdge ? "edge big" : "edge"}>{edgeText(row.edge)}</td>
      <td>
        {row.bid === null || row.ask === null ? "–" : `${Math.round(row.bid * 100)}¢ / ${Math.round(row.ask * 100)}¢`}
      </td>
      <td>{dollars(row.volume_24h)}</td>
      <td>{over === null || last.length === 0 ? "–" : `${over} of ${last.length}`}</td>
    </tr>
  );
}
