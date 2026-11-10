import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { liveSocket } from "../live";
import { applyGameUpdate, applyMoneyline, type GameUpdate, type MarketUpdate } from "../liveCache";

/** Applies live pushes to the query cache. Mounted once, at the top of the app. */
export function LiveBridge() {
  const queryClient = useQueryClient();

  useEffect(() => {
    liveSocket.start();
    const pending = new Map<string, number>();
    // Re-read a page's data after a burst of moves settles, not once per move.
    const refetchSoon = (queryKey: (string | number)[]) => {
      const id = JSON.stringify(queryKey);
      window.clearTimeout(pending.get(id));
      pending.set(
        id,
        window.setTimeout(() => {
          pending.delete(id);
          queryClient.invalidateQueries({ queryKey });
        }, 1500),
      );
    };
    const patchAll = (patch: (value: unknown) => unknown) => {
      for (const query of queryClient.getQueryCache().getAll()) {
        const before = query.state.data;
        if (before === undefined) continue;
        const after = patch(before);
        if (after !== before) queryClient.setQueryData(query.queryKey, after);
      }
    };

    const stop = liveSocket.onMessage((message) => {
      if (message.type === "game.update") {
        const update = message.data as GameUpdate;
        patchAll((value) => applyGameUpdate(value, update));
        if (update.status === "final") {
          refetchSoon(["game", update.game_id]); // markets close; the box score follows later
        }
      } else if (message.type === "market.update") {
        const update = message.data as MarketUpdate;
        patchAll((value) => applyMoneyline(value, update));
        if (update.game_id) refetchSoon(["game", update.game_id]);
        if (update.player_id) refetchSoon(["player-markets", String(update.player_id)]);
      } else if (message.type === "analysis.done") {
        const done = message.data as { analysis_id: string };
        queryClient.invalidateQueries({ queryKey: ["analyses"] });
        queryClient.invalidateQueries({ queryKey: ["analysis", done.analysis_id] });
      } else if (message.type === "hello") {
        // (Re)connected: anything missed while the socket was down is re-read.
        queryClient.invalidateQueries({ queryKey: ["games"] });
      }
    });
    return () => {
      stop();
      pending.forEach((timer) => window.clearTimeout(timer));
    };
  }, [queryClient]);

  return null;
}
