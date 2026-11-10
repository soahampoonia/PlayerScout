import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Averages } from "../api";
import { AnalysisCard } from "../components/AnalysisCard";
import { GameCard } from "../components/GameCard";
import { PlayerLines } from "../components/PlayerLines";
import { LoadError, Loading } from "../components/PageState";
import { CHART_STATS, StatChart, type ChartStat } from "../components/StatChart";
import { monthDay, signed, stat } from "../format";

const TILES: { key: keyof Averages; label: string }[] = [
  { key: "pts", label: "Points" },
  { key: "reb", label: "Rebounds" },
  { key: "ast", label: "Assists" },
  { key: "fg3m", label: "Threes" },
  { key: "stl", label: "Steals" },
  { key: "blk", label: "Blocks" },
  { key: "tov", label: "Turnovers" },
  { key: "minutes", label: "Minutes" },
];

export function PlayerPage() {
  const { playerId = "" } = useParams();
  const query = useQuery({ queryKey: ["player", playerId], queryFn: () => api.player(playerId) });
  const [chartStat, setChartStat] = useState<ChartStat>("pts");

  if (query.isPending) return <Loading />;
  if (query.isError) return <LoadError error={query.error} what="player" retry={() => query.refetch()} />;

  const { player, season_averages: averages, recent_games: recent, next_games: next } = query.data;
  const oldestFirst = [...recent].reverse();
  const details = [player.position, player.jersey && `#${player.jersey}`].filter(Boolean).join(" · ");

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{player.name}</h1>
          <p className="muted">
            {player.team ? <Link to={`/teams/${player.team.id}`}>{player.team.full_name}</Link> : player.is_active ? "Free agent" : "Not on a current roster"}
            {details && ` · ${details}`}
          </p>
        </div>
      </div>

      <AnalysisCard playerId={player.id} hasGames={next.length > 0} />

      {averages && (
        <section className="card">
          <h2 className="card-title">
            {averages.season} averages <span className="muted">· {averages.games} {averages.games === 1 ? "game" : "games"}</span>
          </h2>
          <dl className="tiles">
            {TILES.map((tile) => (
              <div className="tile" key={tile.key}>
                <dt>{tile.label}</dt>
                <dd>{stat(averages[tile.key] as number | null)}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <section className="card">
        <div className="card-head">
          <div>
            <h2 className="card-title flush">Last {recent.length || 10} games</h2>
            {averages && recent.length > 0 && (
              <p className="chart-key">
                <span className="chart-key-line" aria-hidden="true" />
                {averages.season} average: {stat(averages[chartStat])}
              </p>
            )}
          </div>
          {recent.length > 0 && (
            <div className="segmented" role="group" aria-label="Stat shown in the chart">
              {CHART_STATS.map((option) => (
                <button key={option.key} type="button" aria-pressed={chartStat === option.key} onClick={() => setChartStat(option.key)}>
                  {option.short}
                </button>
              ))}
            </div>
          )}
        </div>
        {recent.length === 0 ? (
          <p className="muted pad">No games on record for this player.</p>
        ) : (
          <>
            <StatChart games={oldestFirst} stat={chartStat} average={averages ? averages[chartStat] : null} />
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="left">Date</th>
                    <th className="left">Opp</th>
                    <th className="left">Result</th>
                    <th>MIN</th>
                    <th>PTS</th>
                    <th>REB</th>
                    <th>AST</th>
                    <th>STL</th>
                    <th>BLK</th>
                    <th>3PM</th>
                    <th>FG</th>
                    <th>FT</th>
                    <th>+/−</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((game) => (
                    <tr key={game.game_id}>
                      <td className="left">
                        <Link to={`/games/${game.game_id}`}>{monthDay(game.game_date)}</Link>
                      </td>
                      <td className="left">
                        <span className="muted">{game.is_home ? "vs" : "@"}</span>{" "}
                        <Link to={`/teams/${game.opponent.id}`}>{game.opponent.abbreviation}</Link>
                      </td>
                      <td className="left">
                        {game.result ? (
                          <>
                            <span className={game.result === "W" ? "result win" : "result"}>{game.result}</span> {game.team_score}–{game.opponent_score}
                          </>
                        ) : (
                          "–"
                        )}
                      </td>
                      <td>{stat(game.minutes, 0)}</td>
                      <td className="strong">{game.pts}</td>
                      <td>{game.reb}</td>
                      <td>{game.ast}</td>
                      <td>{game.stl}</td>
                      <td>{game.blk}</td>
                      <td>{game.fg3m}</td>
                      <td>
                        {game.fgm}-{game.fga}
                      </td>
                      <td>
                        {game.ftm}-{game.fta}
                      </td>
                      <td>{signed(game.plus_minus)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <PlayerLines playerId={playerId} teamId={player.team?.id ?? null} recent={recent} />

      {next.length > 0 && (
        <section>
          <h2 className="section-title">Next games</h2>
          <div className="game-grid">
            {next.map((game) => (
              <div key={game.id}>
                <p className="grid-label muted">{monthDay(game.game_date)}</p>
                <GameCard game={game} />
              </div>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
