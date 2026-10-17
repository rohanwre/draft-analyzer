import { useState } from "react";

// Small hand-rolled SVG charts. Colors come from CSS tokens (--side-a, --side-b,
// --pos-*) so light/dark are both handled in trade.css.

interface SeasonBarsProps {
  seasons: string[];
  a: number[];
  b: number[];
  labelA: string;
  labelB: string;
}

/** Grouped bars: lineup points each side gains per season. */
export function SeasonBars({ seasons, a, b, labelA, labelB }: SeasonBarsProps) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 560, H = 190, padL = 36, padB = 26, padT = 12;
  const max = Math.max(1, ...a, ...b);
  const groupW = (W - padL) / seasons.length;
  const barW = Math.min(26, groupW / 3);
  const y = (v: number) => padT + (H - padT - padB) * (1 - Math.max(0, v) / max);
  const ticks = [0, max / 2, max];

  return (
    <figure className="chart">
      <div className="legend" aria-hidden>
        <span><i className="sw sw-a" />{labelA}</span>
        <span><i className="sw sw-b" />{labelB}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Lineup points gained per season. ${seasons.map((s, i) => `${s}: ${labelA} ${Math.round(a[i])}, ${labelB} ${Math.round(b[i])}`).join("; ")}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={padL} x2={W} y1={y(t)} y2={y(t)} className="grid" />
            <text x={padL - 6} y={y(t) + 4} className="axis" textAnchor="end">{Math.round(t)}</text>
          </g>
        ))}
        {seasons.map((s, i) => {
          const cx = padL + groupW * i + groupW / 2;
          return (
            <g key={s} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
              <rect x={cx - groupW / 2} y={0} width={groupW} height={H} className="hit" />
              <path d={barPath(cx - barW - 1, y(a[i]), barW, y(0))} className="bar-a" />
              <path d={barPath(cx + 1, y(b[i]), barW, y(0))} className="bar-b" />
              <text x={cx} y={H - 8} className="axis" textAnchor="middle">{s}</text>
            </g>
          );
        })}
      </svg>
      {hover != null && (
        <div className="tip" style={{ left: `${((padL + groupW * hover + groupW / 2) / W) * 100}%` }}>
          <b>{seasons[hover]}</b>
          <span><i className="sw sw-a" />{labelA} <span className="num">{Math.round(a[hover])}</span></span>
          <span><i className="sw sw-b" />{labelB} <span className="num">{Math.round(b[hover])}</span></span>
        </div>
      )}
    </figure>
  );
}

// Bar rising from the baseline with 4px rounded top corners.
function barPath(x: number, top: number, w: number, base: number): string {
  const h = base - top;
  if (h <= 0.5) return `M${x},${base}h${w}`;
  const r = Math.min(4, h, w / 2);
  return `M${x},${base}V${top + r}Q${x},${top} ${x + r},${top}H${x + w - r}Q${x + w},${top} ${x + w},${top + r}V${base}Z`;
}

interface CurveProps {
  points: [number, number, number][];   // z, playoff rate, n
  fit: (z: number) => number;
}

/** Historical playoff rate by lineup strength, with the fitted curve the engine uses. */
export function PlayoffCurve({ points, fit }: CurveProps) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 520, H = 200, padL = 40, padB = 30, padT = 10;
  const x = (z: number) => padL + ((z + 2) / 4) * (W - padL - 10);
  const y = (p: number) => padT + (H - padT - padB) * (1 - p);
  const line = Array.from({ length: 41 }, (_, i) => -2 + i * 0.1).map((z, i) => `${i ? "L" : "M"}${x(z).toFixed(1)},${y(fit(z)).toFixed(1)}`).join("");
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Share of teams making the playoffs by how strong their drafted lineup was relative to their league">
        {[0, 0.25, 0.5, 0.75, 1].map((p) => (
          <g key={p}>
            <line x1={padL} x2={W - 10} y1={y(p)} y2={y(p)} className="grid" />
            <text x={padL - 6} y={y(p) + 4} className="axis" textAnchor="end">{p * 100}%</text>
          </g>
        ))}
        {[-2, -1, 0, 1, 2].map((z) => (
          <text key={z} x={x(z)} y={H - 10} className="axis" textAnchor="middle">{z > 0 ? `+${z}` : z} SD</text>
        ))}
        <path d={line} className="line-fit" />
        {points.map(([z, p, n], i) => (
          <g key={z} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
            <circle cx={x(z)} cy={y(p)} r={12} className="hit" />
            <circle cx={x(z)} cy={y(p)} r={4.5} className="dot" />
            {hover === i && (
              <text x={x(z)} y={y(p) - 10} className="axis strong" textAnchor="middle">
                {(p * 100).toFixed(0)}% of {n.toLocaleString()}
              </text>
            )}
          </g>
        ))}
      </svg>
    </figure>
  );
}

interface AgeProps {
  startAge: number;
  byPos: Record<string, number[]>;
}

/** Year-over-year retention by age, one line per position, direct-labeled. */
export function AgeCurves({ startAge, byPos }: AgeProps) {
  const [hover, setHover] = useState<number | null>(null);
  const ages = Array.from({ length: 15 }, (_, i) => 21 + i);
  const W = 520, H = 210, padL = 40, padB = 28, padT = 10, padR = 36;
  const lo = 0.55, hi = 1.25;
  const x = (a: number) => padL + ((a - ages[0]) / (ages.length - 1)) * (W - padL - padR);
  const y = (v: number) => padT + (H - padT - padB) * (1 - (v - lo) / (hi - lo));
  const val = (pos: string, a: number) => byPos[pos][Math.min(byPos[pos].length - 1, a - startAge)];
  return (
    <figure className="chart">
      <div className="legend" aria-hidden>
        {Object.keys(byPos).map((p) => <span key={p}><i className={`sw pos-bg-${p}`} />{p}</span>)}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Expected points next season relative to this season, by age and position">
        {[0.6, 0.8, 1, 1.2].map((v) => (
          <g key={v}>
            <line x1={padL} x2={W - padR} y1={y(v)} y2={y(v)} className={v === 1 ? "grid strong" : "grid"} />
            <text x={padL - 6} y={y(v) + 4} className="axis" textAnchor="end">{v.toFixed(1)}×</text>
          </g>
        ))}
        {ages.filter((a) => a % 2 === 1).map((a) => (
          <text key={a} x={x(a)} y={H - 8} className="axis" textAnchor="middle">{a}</text>
        ))}
        {Object.keys(byPos).map((pos) => (
          <g key={pos}>
            <path d={ages.map((a, i) => `${i ? "L" : "M"}${x(a).toFixed(1)},${y(val(pos, a)).toFixed(1)}`).join("")} className={`age-line pos-stroke-${pos}`} />
            <text x={W - padR + 4} y={y(val(pos, ages[ages.length - 1])) + 4} className="axis strong">{pos}</text>
          </g>
        ))}
        {ages.map((a, i) => (
          <rect key={a} x={x(a) - 9} y={0} width={18} height={H - padB} className="hit" onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} />
        ))}
        {hover != null && <line x1={x(ages[hover])} x2={x(ages[hover])} y1={padT} y2={H - padB} className="crosshair" />}
      </svg>
      {hover != null && (
        <div className="tip" style={{ left: `${(x(ages[hover]) / W) * 100}%` }}>
          <b>Age {ages[hover]}</b>
          {Object.keys(byPos).map((p) => (
            <span key={p}><i className={`sw pos-bg-${p}`} />{p} <span className="num">{val(p, ages[hover]).toFixed(2)}×</span></span>
          ))}
        </div>
      )}
    </figure>
  );
}
