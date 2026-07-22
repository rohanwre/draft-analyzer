import type { PickRecord } from "../api/types";

interface Props {
  myPicks: PickRecord[];
}

export default function RosterSummary({ myPicks }: Props) {
  return (
    <div className="panel-section roster-summary">
      <h3>Your roster</h3>
      {myPicks.length === 0 ? (
        <p className="section-hint">No picks yet.</p>
      ) : (
        <ul className="roster-list">
          {myPicks.map((p) => (
            <li key={`${p.round}-${p.name}`}>
              Round {p.round}: {p.name} ({p.position})
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
