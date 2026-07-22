import type { PickRecord } from "../api/types";

interface Props {
  allPicks: PickRecord[];
  leagueSize: number;
  totalRounds: number;
  draftSlot: number;
  onClose: () => void;
}

export default function FullDraftBoard({ allPicks, leagueSize, totalRounds, draftSlot, onClose }: Props) {
  const lookup = new Map<string, PickRecord>();
  allPicks.forEach((p) => {
    if (p.round != null && p.pick_slot != null) {
      lookup.set(`${p.round}-${p.pick_slot}`, p);
    }
  });

  const rounds = Array.from({ length: totalRounds }, (_, i) => i + 1);
  const slots = Array.from({ length: leagueSize }, (_, i) => i + 1);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Full draft board</h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="board-grid-wrapper">
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
        </div>
      </div>
    </div>
  );
}
