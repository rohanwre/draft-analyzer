import { useState } from "react";
import type { PickRecord } from "../api/types";
import { assignRosterSlots, ROSTER_SLOT_ORDER } from "../rosterSlots";

interface Props {
  myPicks: PickRecord[];
  leagueSettings: Record<string, number | string>;
  totalRounds: number;
}

export default function RosterSummary({ myPicks, leagueSettings, totalRounds }: Props) {
  const [byRosterView, setByRosterView] = useState(false);

  const { slots, slotCounts } = assignRosterSlots(myPicks, leagueSettings, totalRounds);

  return (
    <div className="panel-section roster-summary">
      <div className="roster-summary-header">
        <h3>Your roster</h3>
        <label className="roster-view-toggle">
          <input
            type="checkbox"
            checked={byRosterView}
            onChange={(e) => setByRosterView(e.target.checked)}
          />
          Sort by roster position
        </label>
      </div>
      {myPicks.length === 0 ? (
        <p className="section-hint">No picks yet.</p>
      ) : byRosterView ? (
        <div className="roster-slots">
          {ROSTER_SLOT_ORDER.filter((slotKey) => slotCounts[slotKey] > 0 || slots[slotKey].length > 0).map((slotKey) => (
            <div key={slotKey} className="roster-slot-group">
              <div className="roster-slot-label">{slotKey}</div>
              <ul className="roster-list">
                {Array.from({ length: Math.max(slotCounts[slotKey], slots[slotKey].length) }, (_, i) => {
                  const pick = slots[slotKey][i];
                  return (
                    <li key={i} className={pick ? "" : "roster-slot-empty"}>
                      {pick ? `${pick.name} (${pick.position})` : "—"}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
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
