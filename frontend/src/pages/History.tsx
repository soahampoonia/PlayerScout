import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type AnalysisBrief } from "../api";
import { AnalysisBody } from "../components/AnalysisView";
import { LoadError, Loading, Message } from "../components/PageState";
import { ago } from "../format";

export function HistoryPage() {
  const [params, setParams] = useSearchParams();
  const playerId = Number(params.get("player")) || undefined;
  const list = useInfiniteQuery({
    queryKey: ["analyses", { playerId, history: true }],
    queryFn: ({ pageParam }) => api.analyses({ playerId, before: pageParam, limit: 20 }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before,
  });

  if (list.isPending) return <Loading />;
  if (list.isError) return <LoadError error={list.error} what="history" retry={() => list.refetch()} />;
  const items = list.data.pages.flatMap((page) => page.items);
  const filterName = playerId ? items[0]?.player.name : null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>History</h1>
          <p className="muted">
            {playerId ? (
              <>
                Analyses of {filterName ?? "this player"} ·{" "}
                <button type="button" className="link-button" onClick={() => setParams({})}>
                  show everyone
                </button>
              </>
            ) : (
              "Every analysis, newest first"
            )}
          </p>
        </div>
      </div>
      {items.length === 0 ? (
        <Message title="No analyses yet">
          <p>Open a player and press Analyze. Each analysis is saved here.</p>
        </Message>
      ) : (
        <div className="history-list">
          {items.map((item) => (
            <HistoryRow key={item.id} item={item} onFilter={(id) => setParams({ player: String(id) })} />
          ))}
          {list.hasNextPage && (
            <button type="button" className="button" disabled={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>
              {list.isFetchingNextPage ? "Loading…" : "Older analyses"}
            </button>
          )}
        </div>
      )}
    </>
  );
}

const STATUS_LABEL = { queued: "Queued", running: "Writing…", done: "", failed: "Failed" } as const;

function HistoryRow({ item, onFilter }: { item: AnalysisBrief; onFilter: (playerId: number) => void }) {
  const [open, setOpen] = useState(false);
  const detail = useQuery({ queryKey: ["analysis", item.id], queryFn: () => api.analysis(item.id), enabled: open && item.status === "done" });
  return (
    <article className={open ? "card history-row open" : "card history-row"}>
      <button type="button" className="history-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="history-player">{item.player.name}</span>
        <span className="history-when muted">{ago(item.created_at)}</span>
        {item.status !== "done" && <span className={`history-status ${item.status}`}>{STATUS_LABEL[item.status]}</span>}
        {item.status === "done" && (item.is_stale || item.superseded) && (
          <span className="history-status" title={item.superseded ? "A newer analysis of this player exists" : "Its inputs have changed"}>
            {item.superseded ? "superseded" : "stale"}
          </span>
        )}
        <span className="history-summary">{item.status === "failed" ? item.error : item.summary || "…"}</span>
      </button>
      {open && (
        <div className="history-detail">
          <p className="small">
            <Link to={`/players/${item.player.id}`}>Open {item.player.name}</Link> ·{" "}
            <button type="button" className="link-button" onClick={() => onFilter(item.player.id)}>
              only this player
            </button>
          </p>
          {detail.isPending && item.status === "done" && <Loading />}
          {detail.data && <AnalysisBody analysis={detail.data} />}
        </div>
      )}
    </article>
  );
}
