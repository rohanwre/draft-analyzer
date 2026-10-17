import type { PositionalNeedItem } from "../api/types";

interface Props {
  needs: PositionalNeedItem[];
}

// One line, not a row per position: in early rounds every position is unfilled, and only
// the urgent ones are worth calling out.
export default function PositionalNeedsList({ needs }: Props) {
  if (needs.length === 0) return null;
  const urgent = needs.filter((n) => n.urgency === "urgent");

  return (
    <div className="panel-section needs-line">
      <span className="cap">Still to fill</span>
      <div className="needs-chips">
        {needs.map((n) => (
          <span key={n.position} className={`need-chip${n.urgency === "urgent" ? " need-urgent" : ""}`}>
            <span className={`pos pos-${n.position}`}>{n.position}</span>
            {n.urgency === "urgent" && "running out of rounds"}
          </span>
        ))}
      </div>
      {urgent.length > 0 && (
        <p className="section-hint">Urgent positions get a boost in the scores below.</p>
      )}
    </div>
  );
}
