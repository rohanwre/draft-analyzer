import type { PositionalNeedItem } from "../api/types";

interface Props {
  needs: PositionalNeedItem[];
}

export default function PositionalNeedsList({ needs }: Props) {
  if (needs.length === 0) return null;

  return (
    <div className="panel-section">
      <h3>Positional needs</h3>
      <ul className="needs-list">
        {needs.map((n) => (
          <li key={n.position} className={n.urgency === "urgent" ? "need-urgent" : "need-normal"}>
            {n.urgency === "urgent" ? "!! " : "-> "}
            {n.position} {n.urgency === "urgent" ? "— URGENT, running out of rounds" : "— not yet filled"}
          </li>
        ))}
      </ul>
    </div>
  );
}
