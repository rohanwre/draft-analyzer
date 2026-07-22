import { useState } from "react";
import type { PickRecord } from "../api/types";
import { assignRosterSlots, applySlotSwaps, ROSTER_SLOT_ORDER } from "../rosterSlots";
import { swapRosterSlots } from "../api/client";
import type { DraftState } from "../api/types";

interface Props {
  draftId: string;
  myPicks: PickRecord[];
  leagueSettings: Record<string, number | string>;
  totalRounds: number;
  slotSwaps: [string, string][];
  onStateChange: (state: DraftState) => void;
}

export default function RosterSummary({
  draftId, myPicks, leagueSettings, totalRounds, slotSwaps, onStateChange,
}: Props) {
  const [byRosterView, setByRosterView] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const defaultResult = assignRosterSlots(myPicks, leagueSettings, totalRounds);
  const { slots, slotCounts } = applySlotSwaps(defaultResult, slotSwaps);

  const allPicksFlat = ROSTER_SLOT_ORDER.flatMap((slotKey) =>
    slots[slotKey].map((pick) => ({ ...pick, slotKey })),
  );

  async function handleSwap(nameA: string, nameB: string) {
    setBusy(true);
    setError(null);
    try {
      const next = await swapRosterSlots(draftId, nameA, nameB);
      onStateChange(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to swap");
    } finally {
      setBusy(false);
    }
  }

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
      {error && <p className="error-text">{error}</p>}
      {myPicks.length === 0 ? (
        <p className="section-hint">No picks yet.</p>
      ) : byRosterView ? (
        <div className="roster-slots">
          <p className="section-hint">
            Use the swap dropdown to move a player to a different slot — e.g. a QB in
            SFLEX can swap with a QB on the bench.
          </p>
          {ROSTER_SLOT_ORDER.filter((slotKey) => slotCounts[slotKey] > 0 || slots[slotKey].length > 0).map((slotKey) => (
            <div key={slotKey} className="roster-slot-group">
              <div className="roster-slot-label">{slotKey}</div>
              <ul className="roster-list">
                {Array.from({ length: Math.max(slotCounts[slotKey], slots[slotKey].length) }, (_, i) => {
                  const pick = slots[slotKey][i];
                  return (
                    <li key={i} className={pick ? "roster-slot-filled" : "roster-slot-empty"}>
                      <span>{pick ? `${pick.name} (${pick.position})` : "—"}</span>
                      {pick && (
                        <select
                          className="slot-swap-select"
                          value=""
                          disabled={busy}
                          onChange={(e) => {
                            if (e.target.value) handleSwap(pick.name, e.target.value);
                          }}
                        >
                          <option value="">⇄ swap with...</option>
                          {allPicksFlat
                            .filter((other) => other.name !== pick.name)
                            .map((other) => (
                              <option key={other.name} value={other.name}>
                                {other.name} ({other.slotKey})
                              </option>
                            ))}
                        </select>
                      )}
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
