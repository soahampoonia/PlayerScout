import { Link } from "react-router-dom";
import type { Analysis, AnalysisPrediction } from "../api";
import { THIN_HINT, ago, monthDay, pct } from "../format";

const STAT_LABEL = { pts: "Points", reb: "Rebounds", ast: "Assists" } as const;

function signedPoints(a: number | null, b: number | null): string {
  if (a === null || b === null) return "";
  const d = Math.round((a - b) * 100);
  return d > 0 ? `+${d}` : d < 0 ? `−${Math.abs(d)}` : "±0";
}

/** The body of a finished analysis: summary, outlook, per-game probabilities, risks. */
export function AnalysisBody({ analysis, showPlayer = false }: { analysis: Analysis; showPlayer?: boolean }) {
  return (
    <div className="analysis-body">
      {showPlayer && (
        <p className="muted small">
          <Link to={`/players/${analysis.player.id}`}>{analysis.player.name}</Link>
          {analysis.player.team && ` · ${analysis.player.team}`}
        </p>
      )}
      <p className="analysis-summary">{analysis.summary}</p>
      {analysis.outlook && <p className="analysis-outlook">{analysis.outlook}</p>}

      {analysis.games.map((game) => (
        <div key={game.game_id} className="analysis-game">
          <h3 className="lines-title flush-x">
            <Link to={`/games/${game.game_id}`}>
              {monthDay(game.date)} · {game.home_or_away === "home" ? "vs" : "@"} {game.opponent}
            </Link>
          </h3>
          {game.predictions.length === 0 ? (
            <p className="muted small">No probabilities for this game.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th className="left">Stat</th>
                    <th>Line</th>
                    <th title="Baseline stats model">Model</th>
                    <th title="Polymarket">Market</th>
                    <th title="The LLM's own probability">Analyst</th>
                    <th title="Analyst minus market, in points">vs market</th>
                    <th className="left">Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {game.predictions.map((p) => (
                    <PredictionRows key={p.stat} p={p} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ))}

      {(analysis.risks.length > 0 || analysis.data_gaps.length > 0) && (
        <div className="analysis-notes">
          {analysis.risks.length > 0 && (
            <div>
              <h4>Risks</h4>
              <ul>
                {analysis.risks.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          {analysis.data_gaps.length > 0 && (
            <div>
              <h4>Missing data</h4>
              <ul>
                {analysis.data_gaps.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      <p className="analysis-meta muted">
        {analysis.llm_model} · prompt {analysis.prompt_version} · {ago(analysis.completed_at ?? analysis.created_at)}
        {analysis.tokens_in !== null && analysis.tokens_out !== null && ` · ${(analysis.tokens_in + analysis.tokens_out).toLocaleString()} tokens`}
        {analysis.cost_usd !== null && analysis.cost_usd > 0 && ` · $${analysis.cost_usd.toFixed(4)}`}
        {analysis.latency_ms !== null && analysis.latency_ms > 0 && ` · ${(analysis.latency_ms / 1000).toFixed(0)} s`}
      </p>
    </div>
  );
}

function PredictionRows({ p }: { p: AnalysisPrediction }) {
  const thin = p.market_is_thin === true;
  return (
    <>
      <tr>
        <td className="left">{STAT_LABEL[p.stat]}</td>
        <td className="strong">
          {p.line}
          {p.line_source === "model" && <span className="pos" title="No Polymarket line: the model's own line">model</span>}
        </td>
        <td>{pct(p.model_prob)}</td>
        <td className={thin ? "thin" : undefined} title={thin ? THIN_HINT : undefined}>
          {pct(p.market_prob)}
          {thin && <span className="pos">thin</span>}
        </td>
        <td className="strong">{pct(p.llm_prob)}</td>
        <td>{signedPoints(p.llm_prob, p.market_prob)}</td>
        <td className="left">
          <span className={`confidence ${p.confidence}`}>{p.confidence}</span>
        </td>
      </tr>
      {p.reasons.length > 0 && (
        <tr className="reasons-row">
          <td colSpan={7} className="left">
            <details>
              <summary>Why</summary>
              <ul>
                {p.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </details>
          </td>
        </tr>
      )}
    </>
  );
}
