import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "../api";
import { ago } from "../format";
import { useLiveConnected } from "../live";
import { AnalysisBody } from "./AnalysisView";

/** The newest analysis for a player, with Analyze / Refresh. */
export function AnalysisCard({ playerId, hasGames }: { playerId: number; hasGames: boolean }) {
  const queryClient = useQueryClient();
  const connected = useLiveConnected();
  const latest = useQuery({
    queryKey: ["analyses", { playerId }],
    queryFn: () => api.analyses({ playerId, limit: 1 }),
  });
  const brief = latest.data?.items[0];
  const pending = brief && (brief.status === "queued" || brief.status === "running");
  const detail = useQuery({
    queryKey: ["analysis", brief?.id],
    queryFn: () => api.analysis(brief!.id),
    enabled: !!brief && brief.status === "done",
  });
  // analysis.done arrives as a push; poll only without the live socket.
  useQuery({
    queryKey: ["analyses", { playerId }, "poll"],
    queryFn: async () => {
      await queryClient.invalidateQueries({ queryKey: ["analyses", { playerId }] });
      return null;
    },
    enabled: !!pending && !connected,
    refetchInterval: 3000,
  });

  const run = useMutation({
    mutationFn: (force: boolean) => api.analyze(playerId, force),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["analyses"] }),
  });

  const error = run.error instanceof ApiError ? run.error.message : run.error ? "Could not start the analysis." : null;
  const busy = run.isPending || pending;

  return (
    <section className="card analysis-card">
      <div className="card-head">
        <div>
          <h2 className="card-title flush">Analysis</h2>
          <p className="chart-key">
            {brief?.status === "done"
              ? `Written ${ago(brief.completed_at ?? brief.created_at)}${brief.is_stale ? " · inputs have changed since" : ""}`
              : "Current form, outlook and over/under probabilities for the next 3 games"}
          </p>
        </div>
        {hasGames && (
          <button type="button" className="button primary" disabled={busy} onClick={() => run.mutate(!!brief && brief.status === "done")}>
            {busy ? "Analyzing…" : brief?.status === "done" ? "Refresh" : brief?.status === "failed" ? "Try again" : "Analyze"}
          </button>
        )}
      </div>
      <div className="pad">
        {error && <p className="analysis-error">{error}</p>}
        {!brief && !error && (
          <p className="muted">
            {hasGames ? "No analysis yet. It takes about a minute and is saved to History." : "No upcoming games to analyze."}
          </p>
        )}
        {pending && (
          <p className="analysis-pending">
            <span className="live-dot" aria-hidden="true" /> Writing the analysis… usually under a minute.
          </p>
        )}
        {brief?.status === "failed" && <p className="analysis-error">The last analysis failed: {brief.error}</p>}
        {detail.data && <AnalysisBody analysis={detail.data} />}
      </div>
    </section>
  );
}
