import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { FirstSync } from "../components/DataStatus";
import { GameCard } from "../components/GameCard";
import { LoadError, Loading, Message } from "../components/PageState";
import { longDay, shortDay } from "../format";
import { useLiveConnected } from "../live";

export function LivePage() {
  const [params] = useSearchParams();
  const date = params.get("date");
  const connected = useLiveConnected();
  const games = useQuery({
    queryKey: ["games", date ?? "today"],
    queryFn: () => api.games(date),
    // Scores and prices arrive as pushes; poll only while the live socket is down.
    refetchInterval: date || connected ? false : 60_000,
  });
  // Only needed to tell "no games on this date" from "no data loaded yet".
  const empty = games.data?.games.length === 0;
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, enabled: empty, refetchInterval: 5_000 });

  if (games.isPending) return <Loading />;
  if (games.isError) return <LoadError error={games.error} what="page" retry={() => games.refetch()} />;

  const data = games.data;
  const isToday = data.date === data.today;
  const noData = empty && status.data?.counts.games === 0;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{isToday ? "Today" : longDay(data.date)}</h1>
          <p className="muted">
            {isToday ? longDay(data.date) : "Eastern time"}
            {data.games.length > 0 && ` · ${data.games.length} ${data.games.length === 1 ? "game" : "games"}`}
          </p>
        </div>
        <nav className="date-nav" aria-label="Change date">
          <DateLink to={data.previous_date} label="Previous game day">
            ←
          </DateLink>
          {!isToday && (
            <Link to="/" className="button">
              Today
            </Link>
          )}
          <DateLink to={data.next_date} label="Next game day">
            →
          </DateLink>
        </nav>
      </div>

      {noData ? (
        <FirstSync />
      ) : empty ? (
        <Message title="No games on this date">
          {data.next_date && <Link to={`/?date=${data.next_date}`}>Next games: {shortDay(data.next_date)}</Link>}
        </Message>
      ) : (
        <div className="game-grid">
          {data.games.map((game) => (
            <GameCard key={game.id} game={game} />
          ))}
        </div>
      )}
    </>
  );
}

function DateLink({ to, label, children }: { to: string | null; label: string; children: string }) {
  if (!to) {
    return (
      <span className="button disabled" aria-disabled="true" aria-label={label}>
        {children}
      </span>
    );
  }
  return (
    <Link to={`/?date=${to}`} className="button" aria-label={`${label}: ${shortDay(to)}`} title={shortDay(to)}>
      {children}
    </Link>
  );
}
