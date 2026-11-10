import { useEffect, useRef, useState } from "react";
import type { GameLog } from "../api";
import { monthDay, numericDay } from "../format";

export type ChartStat = "pts" | "reb" | "ast" | "fg3m" | "minutes";

export const CHART_STATS: { key: ChartStat; short: string; unit: string }[] = [
  { key: "pts", short: "PTS", unit: "points" },
  { key: "reb", short: "REB", unit: "rebounds" },
  { key: "ast", short: "AST", unit: "assists" },
  { key: "fg3m", short: "3PM", unit: "threes made" },
  { key: "minutes", short: "MIN", unit: "minutes" },
];

const HEIGHT = 230;
const MARGIN = { top: 26, right: 8, bottom: 42, left: 30 };
const BAR_MAX = 24;
const TIP_HALF = 100; // keeps the tooltip inside the card near the edges

/** A round axis top and step: 37 -> ticks 0, 10, 20, 30, 40. */
function axis(maxValue: number): { top: number; step: number } {
  const steps = [1, 2, 5, 10, 20, 25, 50];
  const step = steps.find((s) => maxValue / s <= 4) ?? 100;
  return { top: Math.max(step, Math.ceil(maxValue / step) * step), step };
}

/** A column with a rounded top and a square foot on the baseline. */
function column(x: number, y: number, width: number, height: number): string {
  const r = Math.min(4, width / 2, height);
  return `M${x},${y + height} V${y + r} Q${x},${y} ${x + r},${y} H${x + width - r} Q${x + width},${y} ${x + width},${y + r} V${y + height} Z`;
}

interface Props {
  games: GameLog[]; // oldest first
  stat: ChartStat;
  average: number | null;
}

export function StatChart({ games, stat, average }: Props) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const [hover, setHover] = useState<number | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(260, entry.contentRect.width)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const unit = CHART_STATS.find((s) => s.key === stat)!.unit;
  const values = games.map((game) => game[stat] ?? 0);
  const { top, step } = axis(Math.max(1, ...values, average ?? 0));
  const plotW = width - MARGIN.left - MARGIN.right;
  const plotH = HEIGHT - MARGIN.top - MARGIN.bottom;
  const slot = plotW / games.length;
  const barW = Math.min(BAR_MAX, slot * 0.6);
  const tight = slot < 40;
  const y = (value: number) => MARGIN.top + plotH - (value / top) * plotH;
  const baseline = y(0);
  const ticks = Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step);

  // Label only the best game and the latest one; the tooltip and the table carry the rest.
  const best = values.indexOf(Math.max(...values));
  const labelled = new Set([best, values.length - 1]);

  const hovered = hover !== null ? games[hover] : null;
  const centre = (i: number) => MARGIN.left + slot * i + slot / 2;

  return (
    <div className="chart" ref={wrap}>
      <svg width={width} height={HEIGHT} className={tight ? "tight" : undefined} role="list" aria-label={`${unit} in each of the last ${games.length} games`}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line className={tick === 0 ? "chart-axis" : "chart-grid"} x1={MARGIN.left} x2={width - MARGIN.right} y1={y(tick)} y2={y(tick)} />
            <text className="chart-tick" x={MARGIN.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
              {tick}
            </text>
          </g>
        ))}

        {games.map((game, i) => {
          const value = values[i];
          const x = centre(i) - barW / 2;
          const label = `${monthDay(game.game_date)} ${game.is_home ? "vs" : "at"} ${game.opponent.abbreviation}: ${value} ${unit}`;
          return (
            <g
              key={game.game_id}
              role="listitem"
              aria-label={label}
              tabIndex={0}
              className={hover === i ? "chart-bar active" : "chart-bar"}
              onPointerEnter={() => setHover(i)}
              onPointerLeave={() => setHover(null)}
              onFocus={() => setHover(i)}
              onBlur={() => setHover(null)}
            >
              {/* The whole column of the plot is the hover target, not just the painted bar. */}
              <rect className="chart-hit" x={MARGIN.left + slot * i} y={MARGIN.top - 20} width={slot} height={plotH + 20 + MARGIN.bottom} />
              {value > 0 && <path className="chart-mark" d={column(x, y(value), barW, baseline - y(value))} />}
              {labelled.has(i) && (
                <text className="chart-value" x={centre(i)} y={y(value) - 7} textAnchor="middle">
                  {value}
                </text>
              )}
              {/* On a phone ten labels do not fit in full: drop the "@" and keep only the end dates. */}
              <text className="chart-x" x={centre(i)} y={baseline + 16} textAnchor="middle">
                {game.is_home || tight ? "" : "@"}
                {game.opponent.abbreviation}
              </text>
              {(!tight || i === 0 || i === games.length - 1) && (
                <text className="chart-x dim" x={centre(i)} y={baseline + 31} textAnchor="middle">
                  {numericDay(game.game_date)}
                </text>
              )}
            </g>
          );
        })}

        {average !== null && <line className="chart-average" x1={MARGIN.left} x2={width - MARGIN.right} y1={y(average)} y2={y(average)} />}
      </svg>

      {hovered && hover !== null && (
        <div
          className="chart-tip"
          role="status"
          style={{
            left: Math.min(Math.max(centre(hover), TIP_HALF), width - TIP_HALF),
            top: Math.max(0, y(values[hover]) - 12),
          }}
        >
          <div className="chart-tip-value">
            {values[hover]} <span>{unit}</span>
          </div>
          <div className="chart-tip-meta">
            {monthDay(hovered.game_date)} · {hovered.is_home ? "vs" : "@"} {hovered.opponent.abbreviation}
            {hovered.result && hovered.team_score !== null && hovered.opponent_score !== null && (
              <>
                {" "}
                · {hovered.result} {hovered.team_score}–{hovered.opponent_score}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
