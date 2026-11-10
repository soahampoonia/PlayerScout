const WIDTH = 96;
const HEIGHT = 26;
const PAD = 5;

/** A small line of one probability over time: 2px line, end dot with a surface ring.
 *  The y range fits the data (at least 10 points tall) so small moves stay visible. */
export function Sparkline({ points, label }: { points: [number, number][]; label: string }) {
  if (points.length < 2) return null;
  const times = points.map(([t]) => t);
  const values = points.map(([, v]) => v);
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (hi - lo < 0.1) {
    const mid = (hi + lo) / 2;
    lo = mid - 0.05;
    hi = mid + 0.05;
  }
  const x = (t: number) => PAD + ((t - t0) / Math.max(t1 - t0, 1)) * (WIDTH - 2 * PAD);
  const y = (v: number) => PAD + (1 - (v - lo) / (hi - lo)) * (HEIGHT - 2 * PAD);
  const d = points.map(([t, v], i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const [lastT, lastV] = points[points.length - 1];
  return (
    <svg className="sparkline" width={WIDTH} height={HEIGHT} viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={label}>
      <path className="spark-line" d={d} />
      <circle className="spark-dot" cx={x(lastT)} cy={y(lastV)} r={3.5} />
    </svg>
  );
}
