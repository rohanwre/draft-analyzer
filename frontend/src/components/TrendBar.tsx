interface Props {
  position: string;
  pct: number;
}

export default function TrendBar({ position, pct }: Props) {
  return (
    <div className="trend-bar">
      <span className={`pos pos-${position}`}>{position}</span>
      <div className="trend-bar-track">
        <div className={`trend-bar-fill fill-${position}`} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className="trend-bar-pct num">{pct.toFixed(1)}%</span>
    </div>
  );
}
