import type { ScarcityAlertItem } from "../api/types";

interface Props {
  alerts: ScarcityAlertItem[];
}

export default function ScarcityAlerts({ alerts }: Props) {
  if (alerts.length === 0) return null;

  return (
    <div className="panel-section">
      <h3>Scarcity alerts</h3>
      <ul className="scarcity-list">
        {alerts.map((a) => (
          <li key={a.position}>
            {a.position} going {a.ratio}x faster than ADP suggests ({a.taken} taken, expected{" "}
            {a.expected}, league demand: {a.demand_pct}% of starts)
          </li>
        ))}
      </ul>
    </div>
  );
}
