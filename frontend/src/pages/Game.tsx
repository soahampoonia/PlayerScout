import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, type Game, type GameDetail, type GameMarkets, type GamePlayer, type GameSide, type PropStat } from "../api";
import { AnalyzeButton } from "../components/AnalyzeButton";
import { GameStatusLabel, Score, won } from "../components/GameCard";
import { useLiveConnected, useLiveSubscription } from "../live";
import { LoadError, Loading } from "../components/PageState";
import { THIN_HINT, ago, dollars, longDay, pct, pctPair, signed, spreadLine, stat } from "../format";

const PROP_STATS: { key: PropStat; label: string }[] = [
  { key: "pts", label: "PTS line" },
  { key: "reb", label: "REB line" },
  { key: "ast", label: "AST line" },
];

export function GamePage() {
  const { gameId = "" } = useParams();
  const connected = useLiveConnected();
  useLiveSubscription([gameId], []);
  // Pushes keep this page current; it polls only while the live socket is down.
  const query = useQuery({ queryKey: ["game", gameId], queryFn: () => api.game(gameId), refetchInterval: connected ? false : 30_000 });

  if (query.isPending) return <Loading />;
  if (query.isError) return <LoadError error={query.error} what="game" retry={() => query.refetch()} />;

  const { game, has_box_score, home, away, markets } = query.data;
  const showScore = game.status === "live" || game.status === "final";
  const withLines = !has_box_score && !!markets && markets.props_count > 0;

  return (
    <>
      <section className="card scoreboard">
        <ScoreSide side={game.away} result={won(game, "away")} showScore={showScore} place="Away" />
        <div className="scoreboard-mid">
          <GameStatusLabel game={game} />
          <span className="muted">{longDay(game.game_date)}</span>
        </div>
        <ScoreSide side={game.home} result={won(game, "home")} showScore={showScore} place="Home" />
      </section>

      {markets && <MarketStrip game={game} markets={markets} />}

      {!has_box_score && (
        <p className="note muted">
          Current rosters with {game.season} season averages.
          {withLines && " Lines are each player's main Polymarket line and the chance of going over it."} The box score
          appears after the game.
        </p>
      )}

      <div className={has_box_score || withLines ? "stack" : "two-col"}>
        <TeamTable team={away} boxScore={has_box_score} withLines={withLines} />
        <TeamTable team={home} boxScore={has_box_score} withLines={withLines} />
      </div>
    </>
  );
}

function ScoreSide({ side, result, showScore, place }: { side: GameSide; result: boolean | null; showScore: boolean; place: string }) {
  return (
    <div className={result === false ? "score-side loser" : "score-side"}>
      <Link to={`/teams/${side.id}`} className="score-team">
        <span className="score-abbr">{side.abbreviation}</span>
        <span className="score-name">
          <span className="score-city">{side.city} </span>
          {side.name}
        </span>
      </Link>
      <span className="score-place muted">{place}</span>
      {showScore && side.score !== null && <Score value={side.score} className="score-value" />}
    </div>
  );
}

/** Moneyline, main spread and main total, in one row of cards. */
function MarketStrip({ game, markets }: { game: Game; markets: GameMarkets }) {
  const { moneyline: ml, spread, total } = markets;
  if (!ml && !spread && !total) {
    return <p className="note muted">No Polymarket markets matched to this game yet.</p>;
  }
  const spreadTeam = spread && (spread.team_id === game.home.id ? game.home : game.away);
  const updated = ml?.updated_at ?? spread?.updated_at ?? total?.updated_at ?? null;
  const [awayPct, homePct] = ml ? pctPair(ml.away) : ["", ""];
  return (
    <section className="market-strip" aria-label="Polymarket">
      {ml && (
        <div className={ml.is_thin ? "card market-tile thin" : "card market-tile"} title={ml.is_thin ? THIN_HINT : undefined}>
          <div className="market-label">Win probability{ml.is_thin && <span className="thin-tag">thin</span>}</div>
          <div className="win-split">
            <span>
              {game.away.abbreviation} <strong>{awayPct}</strong>
            </span>
            <span>
              <strong>{homePct}</strong> {game.home.abbreviation}
            </span>
          </div>
          <div className="split-bar" role="img" aria-label={`${game.away.abbreviation} ${awayPct}, ${game.home.abbreviation} ${homePct}`}>
            <span className="split-away" style={{ flexGrow: ml.away }} />
            <span className="split-home" style={{ flexGrow: ml.home }} />
          </div>
          <div className="market-foot">{dollars(ml.volume_24h)} traded in 24 h</div>
        </div>
      )}
      {spread && spreadTeam && (
        <div className={spread.is_thin ? "card market-tile thin" : "card market-tile"} title={spread.is_thin ? THIN_HINT : undefined}>
          <div className="market-label">Spread{spread.is_thin && <span className="thin-tag">thin</span>}</div>
          <div className="market-main">
            {spreadTeam.abbreviation} {spreadLine(spread.line)}
          </div>
          <div className="market-foot">{pct(spread.prob)} to cover</div>
        </div>
      )}
      {total && (
        <div className={total.is_thin ? "card market-tile thin" : "card market-tile"} title={total.is_thin ? THIN_HINT : undefined}>
          <div className="market-label">Total points{total.is_thin && <span className="thin-tag">thin</span>}</div>
          <div className="market-main">O/U {total.line}</div>
          <div className="market-foot">{pct(total.prob)} over</div>
        </div>
      )}
      <p className="market-source muted">
        Polymarket{updated && ` · updated ${ago(updated)}`}
        {markets.props_count > 0 && ` · ${markets.props_count} player lines`}
      </p>
    </section>
  );
}

function TeamTable({ team, boxScore, withLines }: { team: GameDetail["home"]; boxScore: boolean; withLines: boolean }) {
  // On a phone the averages make way for the lines (they are on each player's page).
  const avgClass = withLines ? "avg-col" : "";
  return (
    <section className="card">
      <h2 className="card-title">
        <Link to={`/teams/${team.id}`}>{team.full_name}</Link>
      </h2>
      {team.players.length === 0 ? (
        <p className="muted pad">No roster loaded yet.</p>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              {boxScore ? (
                <tr>
                  <th className="left">Player</th>
                  <th>MIN</th>
                  <th>PTS</th>
                  <th>REB</th>
                  <th>AST</th>
                  <th>STL</th>
                  <th>BLK</th>
                  <th>TOV</th>
                  <th>3PM</th>
                  <th>FG</th>
                  <th>FT</th>
                  <th>+/−</th>
                </tr>
              ) : (
                <tr>
                  <th className="left">Player</th>
                  <th className={avgClass}>GP</th>
                  <th className={avgClass}>MIN</th>
                  <th className={avgClass}>PTS</th>
                  <th className={avgClass}>REB</th>
                  <th className={avgClass}>AST</th>
                  <th className={avgClass}>3PM</th>
                  {withLines &&
                    PROP_STATS.map((s) => (
                      <th key={s.key}>
                        {s.label.replace(" line", "")}
                        <span className="th-suffix"> line</span>
                      </th>
                    ))}
                  {withLines && <th aria-label="Analyze" />}
                </tr>
              )}
            </thead>
            <tbody>
              {team.players.map((player) =>
                boxScore ? <BoxRow key={player.id} player={player} /> : <RosterRow key={player.id} player={player} withLines={withLines} />,
              )}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function PlayerCell({ player }: { player: GamePlayer }) {
  return (
    <td className="left">
      <Link to={`/players/${player.id}`}>{player.name}</Link>
      {player.position && <span className="pos">{player.position}</span>}
    </td>
  );
}

function BoxRow({ player }: { player: GamePlayer }) {
  const line = player.line;
  if (!line) return null;
  return (
    <tr>
      <PlayerCell player={player} />
      <td>{stat(line.minutes, 0)}</td>
      <td className="strong">{line.pts}</td>
      <td>{line.reb}</td>
      <td>{line.ast}</td>
      <td>{line.stl}</td>
      <td>{line.blk}</td>
      <td>{line.tov}</td>
      <td>{line.fg3m}</td>
      <td>
        {line.fgm}-{line.fga}
      </td>
      <td>
        {line.ftm}-{line.fta}
      </td>
      <td>{signed(line.plus_minus)}</td>
    </tr>
  );
}

function RosterRow({ player, withLines }: { player: GamePlayer; withLines: boolean }) {
  const avg = player.averages;
  const avgClass = withLines ? "avg-col" : "";
  return (
    <tr>
      <PlayerCell player={player} />
      <td className={avgClass}>{avg ? avg.games : "–"}</td>
      <td className={avgClass}>{stat(avg?.minutes)}</td>
      <td className={`strong ${avgClass}`}>{stat(avg?.pts)}</td>
      <td className={avgClass}>{stat(avg?.reb)}</td>
      <td className={avgClass}>{stat(avg?.ast)}</td>
      <td className={avgClass}>{stat(avg?.fg3m)}</td>
      {withLines &&
        PROP_STATS.map(({ key }) => {
          const line = player.lines[key];
          if (!line) return <td key={key} className="line-cell muted">–</td>;
          return (
            <td key={key} className={line.is_thin ? "line-cell thin" : "line-cell"} title={line.is_thin ? THIN_HINT : `Chance of going over ${line.line}`}>
              <span className="line-value">{line.line}</span> <span className="line-prob">{pct(line.prob)}</span>
            </td>
          );
        })}
      {withLines && (
        <td className="analyze-cell">
          {Object.keys(player.lines).length > 0 && <AnalyzeButton playerId={player.id} name={player.name} />}
        </td>
      )}
    </tr>
  );
}
