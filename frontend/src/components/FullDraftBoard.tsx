import { useState } from "react";
import type { PickRecord } from "../api/types";
import { assignRosterSlots, ROSTER_SLOT_ORDER } from "../rosterSlots";

interface Props {
  allPicks: PickRecord[];
  leagueSize: number;
  totalRounds: number;
  draftSlot: number;
  leagueSettings: Record<string, number | string>;
  onClose: () => void;
}

export default function FullDraftBoard({
  allPicks, leagueSize, totalRounds, draftSlot, leagueSettings, onClose,
}: Props) {
  const [byRosterView, setByRosterView] = useState(false);

  const rounds = Array.from({ length: totalRounds }, (_, i) => i + 1);
  const slots = Array.from({ length: leagueSize }, (_, i) => i + 1);

  const lookup = new Map<string, PickRecord>();
  allPicks.forEach((p) => {
    if (p.round != null && p.pick_slot != null) {
      lookup.set(`${p.round}-${p.pick_slot}`, p);
    }
  });

  const rosterByTeam = new Map<number, ReturnType<typeof assignRosterSlots>>();
  if (byRosterView) {
    for (const slot of slots) {
      const teamPicks = allPicks
        .filter((p) => p.pick_slot === slot)
        .map((p) => ({ position: p.position, name: p.name }));
      rosterByTeam.set(slot, assignRosterSlots(teamPicks, leagueSettings, totalRounds));
    }
  }

  const maxBenchSlots = byRosterView
    ? Math.max(0, ...slots.map((s) => rosterByTeam.get(s)?.slotCounts.BENCH ?? 0))
    : 0;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Full draft board</h2>
          <label className="roster-view-toggle">
            <input
              type="checkbox"
              checked={byRosterView}
              onChange={(e) => setByRosterView(e.target.checked)}
            />
            Sort by roster position
          </label>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="board-grid-wrapper">
          {byRosterView ? (
            <table className="board-grid">
              <thead>
                <tr>
                  <th>Slot</th>
                  {slots.map((s) => (
                    <th key={s} className={s === draftSlot ? "board-my-slot" : ""}>
                      Slot {s}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ROSTER_SLOT_ORDER.flatMap((slotKey) => {
                  const rowCount = slotKey === "BENCH"
                    ? maxBenchSlots
                    : Math.max(...slots.map((s) => rosterByTeam.get(s)?.slotCounts[slotKey] ?? 0));
                  if (rowCount === 0) return [];
                  return Array.from({ length: rowCount }, (_, i) => (
                    <tr key={`${slotKey}-${i}`}>
                      <td className="board-round-label">
                        {slotKey}
                        {rowCount > 1 ? i + 1 : ""}
                      </td>
                      {slots.map((s) => {
                        const pick = rosterByTeam.get(s)?.slots[slotKey][i];
                        return (
                          <td key={s} className={s === draftSlot ? "board-my-slot" : ""}>
                            {pick ? (
                              <>
                                <div className="board-cell-name">{pick.name}</div>
                                <div className="board-cell-pos">{pick.position}</div>
                              </>
                            ) : (
                              <span className="board-cell-empty">—</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ));
                })}
              </tbody>
            </table>
          ) : (
            <table className="board-grid">
              <thead>
                <tr>
                  <th>Rd</th>
                  {slots.map((s) => (
                    <th key={s} className={s === draftSlot ? "board-my-slot" : ""}>
                      Slot {s}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rounds.map((r) => (
                  <tr key={r}>
                    <td className="board-round-label">{r}</td>
                    {slots.map((s) => {
                      const pick = lookup.get(`${r}-${s}`);
                      return (
                        <td key={s} className={s === draftSlot ? "board-my-slot" : ""}>
                          {pick ? (
                            <>
                              <div className="board-cell-name">{pick.name}</div>
                              <div className="board-cell-pos">{pick.position}</div>
                            </>
                          ) : (
                            <span className="board-cell-empty">—</span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
