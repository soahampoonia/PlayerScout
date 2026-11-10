import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { TeamGameRow } from "../components/GameCard";
import { LoadError, Loading } from "../components/PageState";
import { monthDay, stat } from "../format";

export function TeamPage() {
  const { teamId = "" } = useParams();
  const query = useQuery({ queryKey: ["team", teamId], queryFn: () => api.team(teamId) });

  if (query.isPending) return <Loading />;
  if (query.isError) return <LoadError error={query.error} what="team" retry={() => query.refetch()} />;

  const { team, season, record, stats, roster, recent_games: recent, next_games: next } = query.data;
  const played = record.wins + record.losses > 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{team.full_name}</h1>
          <p className="muted">
            {team.conference && `${team.conference}ern Conference · `}
            {season}
            {played && ` · ${record.wins}–${record.losses}`}
          </p>
        </div>
      </div>

      {stats && (
        <section className="card">
          <h2 className="card-title">
            Team numbers <span className="muted">· through {monthDay(stats.as_of)}</span>
          </h2>
          <dl className="tiles">
            <Tile label="Pace" value={stats.pace} />
            <Tile label="Offensive rating" value={stats.off_rating} />
            <Tile label="Defensive rating" value={stats.def_rating} />
            <Tile label="Points allowed" value={stats.opp_pts} />
            <Tile label="Rebounds allowed" value={stats.opp_reb} />
            <Tile label="Assists allowed" value={stats.opp_ast} />
          </dl>
        </section>
      )}

      <div className="two-col">
        <section className="card">
          <h2 className="card-title">Recent games</h2>
          {recent.length === 0 ? <p className="muted pad">No finished games yet.</p> : recent.map((game) => <TeamGameRow key={game.id} game={game} teamId={team.id} />)}
        </section>
        <section className="card">
          <h2 className="card-title">Upcoming</h2>
          {next.length === 0 ? <p className="muted pad">Nothing scheduled.</p> : next.map((game) => <TeamGameRow key={game.id} game={game} teamId={team.id} />)}
        </section>
      </div>

      <section className="card">
        <h2 className="card-title">
          Roster <span className="muted">· {season} averages</span>
        </h2>
        {roster.length === 0 ? (
          <p className="muted pad">No roster loaded yet.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="left">Player</th>
                  <th>No.</th>
                  <th>GP</th>
                  <th>MIN</th>
                  <th>PTS</th>
                  <th>REB</th>
                  <th>AST</th>
                  <th>STL</th>
                  <th>BLK</th>
                  <th>3PM</th>
                </tr>
              </thead>
              <tbody>
                {roster.map((player) => {
                  const avg = player.averages;
                  return (
                    <tr key={player.id}>
                      <td className="left">
                        <Link to={`/players/${player.id}`}>{player.name}</Link>
                        {player.position && <span className="pos">{player.position}</span>}
                      </td>
                      <td>{player.jersey || "–"}</td>
                      <td>{avg ? avg.games : "–"}</td>
                      <td>{stat(avg?.minutes)}</td>
                      <td className="strong">{stat(avg?.pts)}</td>
                      <td>{stat(avg?.reb)}</td>
                      <td>{stat(avg?.ast)}</td>
                      <td>{stat(avg?.stl)}</td>
                      <td>{stat(avg?.blk)}</td>
                      <td>{stat(avg?.fg3m)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function Tile({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="tile">
      <dt>{label}</dt>
      <dd>{stat(value)}</dd>
    </div>
  );
}
