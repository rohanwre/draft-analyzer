import type { RankedPlayerItem } from "../api/types";

interface Props {
  players: RankedPlayerItem[];
}

export default function RankedPlayersList({ players }: Props) {
  return (
    <div className="panel-section">
      <h3>Ranked picks</h3>
      <p className="section-hint">
        One blended score per player: historical trend % + roster-need adjustment + ADP-fall value.
        A player who's fallen far enough past their ADP can outrank roster need on its own.
      </p>
      <ul className="player-list">
        {players.map((p) => (
          <li key={`${p.position}-${p.name}`} className="ranked-player">
            <span className="ranked-score">{p.score.toFixed(1)}</span>
            <span className="ranked-player-name">
              {p.name} <span className="adp">({p.position}, ADP {p.rank})</span>
            </span>
            {p.need === "urgent" && <span className="tag tag-urgent">URGENT NEED</span>}
            {p.need === "need" && <span className="tag tag-need">NEED</span>}
            {(p.need === "surplus" || p.need === "filled") && (
              <span className="tag tag-surplus">HAVE ENOUGH</span>
            )}
            {p.value > 0 && <span className="value">+{p.value.toFixed(0)} past ADP</span>}
          </li>
        ))}
        {players.length === 0 && <li className="empty">No players left</li>}
      </ul>
    </div>
  );
}
