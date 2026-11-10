import { useQuery } from "@tanstack/react-query";
import { api, type JobStatus } from "../api";

const LABELS: Record<string, string> = {
  sync_reference: "Rosters",
  sync_schedule: "Schedule",
  sync_team_stats: "Team stats",
  sync_game_logs: "Box scores",
};

const STATE: Record<JobStatus["status"], string> = {
  never_run: "Waiting",
  running: "Running",
  ok: "Done",
  failed: "Failed",
};

/** Shown on the home screen while the database has no games: explains the first sync
 *  and, if the NBA site refused the requests, says so instead of showing an empty page. */
export function FirstSync() {
  const status = useQuery({ queryKey: ["status"], queryFn: api.status, refetchInterval: 5_000, staleTime: 0 });
  if (!status.data) return null;

  const jobs = status.data.jobs.filter((job) => job.job in LABELS);
  const failed = jobs.filter((job) => job.status === "failed");
  const working = jobs.some((job) => job.status === "running" || job.status === "never_run");
  const stuck = failed.length > 0 && !working;

  return (
    <section className="card sync-card">
      <h2>{stuck ? "The first NBA sync failed" : "Loading NBA data for the first time"}</h2>
      <p className="muted">
        {stuck
          ? "Rosters, the schedule, team stats and box scores come from stats.nba.com, and it did not answer. Teams and player names still work in search; games appear once a sync succeeds."
          : "The first sync pulls rosters, the schedule, team stats and two seasons of box scores from stats.nba.com. It can take a few minutes; this page fills in by itself."}
      </p>
      <ul className="sync-jobs">
        {jobs.map((job) => (
          <li key={job.job}>
            <span>{LABELS[job.job] ?? job.job}</span>
            <span className={`sync-state ${job.status}`}>
              {job.status === "running" && <span className="live-dot" aria-hidden="true" />}
              {STATE[job.status]}
              {job.status === "ok" && job.rows_written !== null && ` · ${job.rows_written.toLocaleString()} rows`}
            </span>
          </li>
        ))}
      </ul>
      {failed.length > 0 && (
        <div className="sync-error">
          <p className="sync-error-text">{failed[0].error}</p>
          <p className="muted">
            stats.nba.com refuses many VPN and cloud addresses. Turn a VPN off, or set NBA_PROXY in .env, then run:
          </p>
          <pre>docker compose exec api python manage.py sync all</pre>
        </div>
      )}
    </section>
  );
}
