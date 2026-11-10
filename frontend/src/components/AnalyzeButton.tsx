import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api } from "../api";

/** Starts (or reuses) an analysis and opens the player's page, where it appears. */
export function AnalyzeButton({ playerId, name }: { playerId: number; name: string }) {
  const navigate = useNavigate();
  const [state, setState] = useState<"idle" | "busy" | "error">("idle");
  const [message, setMessage] = useState("");
  return (
    <button
      type="button"
      className="mini-button"
      disabled={state === "busy"}
      title={state === "error" ? message : `Analyze ${name}`}
      aria-label={`Analyze ${name}`}
      onClick={async () => {
        setState("busy");
        try {
          await api.analyze(playerId);
          navigate(`/players/${playerId}`);
        } catch (error) {
          setState("error");
          setMessage(error instanceof ApiError ? error.message : "Could not start the analysis.");
        }
      }}
    >
      {state === "busy" ? "…" : state === "error" ? "!" : "Analyze"}
    </button>
  );
}
