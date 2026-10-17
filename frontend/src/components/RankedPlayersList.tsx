import type { RankedPlayerItem } from "../api/types";

const CLIFF_TAG_THRESHOLD = 10;

interface Props {
  players: RankedPlayerItem[];
  onSelectPlayer: (name: string, position: string) => void;
  disabled: boolean;
}

export default function RankedPlayersList({ players, onSelectPlayer, disabled }: Props) {
  // A tag every row shares (e.g. round 1, when every position is a "need") says nothing.
  const allSameNeed = players.length > 1 && players.every((p) => p.need === players[0].need);
  const allCliff = players.length > 1 && players.every((p) => p.cliff_bonus >= CLIFF_TAG_THRESHOLD);
  const showNeed = (need: string) => need === "urgent" || !allSameNeed;

  return (
    <div className="panel-section">
      <h3>Best picks right now</h3>
      <p className="section-hint">
        One score per player: what winning teams took here, plus your roster needs, how far he's fallen
        past ADP, and whether his position is about to dry up before your next turn.
      </p>
      <ul className="player-list">
        {players.map((p) => (
          <li key={`${p.position}-${p.name}`} className="ranked-player">
            <span className="ranked-score num">{p.score.toFixed(1)}</span>
            <span className={`pos pos-${p.position}`}>{p.position}</span>
            <span className="ranked-player-name">
              {p.name} <span className="adp">ADP {p.rank}</span>
            </span>
            {p.need === "urgent" && <span className="tag tag-urgent">Urgent need</span>}
            {p.need === "need" && showNeed(p.need) && <span className="tag tag-need">Need</span>}
            {(p.need === "surplus" || p.need === "filled") && showNeed(p.need) && (
              <span className="tag tag-surplus">Have enough</span>
            )}
            {p.cliff_bonus >= CLIFF_TAG_THRESHOLD && !allCliff && (
              <span className="tag tag-cliff">Scarce soon</span>
            )}
            {p.value > 0 && <span className="value num">+{p.value.toFixed(0)} past ADP</span>}
            <button
              type="button"
              className="select-btn"
              disabled={disabled}
              onClick={() => onSelectPlayer(p.name, p.position)}
            >
              Select
            </button>
          </li>
        ))}
        {players.length === 0 && <li className="empty">No players left</li>}
      </ul>
    </div>
  );
}
