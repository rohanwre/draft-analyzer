interface Props {
  position: string;
  pct: number;
}

export default function TrendBar({ position, pct }: Props) {
  return (
    <div className="trend-bar">
      <span className="trend-bar-label">{position}</span>
      <div className="trend-bar-track">
        <div className="trend-bar-fill" style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className="trend-bar-pct">{pct.toFixed(1)}%</span>
    </div>
  );
}
