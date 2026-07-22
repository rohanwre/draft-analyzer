import { useEffect, useState } from "react";
import type { PlayerAdpItem } from "../api/types";
import { getFullAdp } from "../api/client";

interface Props {
  season: number;
  leagueType: string;
  takenNames: string[];
  onClose: () => void;
}

function nameKey(name: string) {
  return name.trim().toLowerCase();
}

export default function AdpWatchlist({ season, leagueType, takenNames, onClose }: Props) {
  const [players, setPlayers] = useState<PlayerAdpItem[]>([]);
  const [query, setQuery] = useState("");
  const [hideDrafted, setHideDrafted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getFullAdp(season, leagueType)
      .then((data) => {
        if (!cancelled) setPlayers(data);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load ADP");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [season, leagueType]);

  const takenSet = new Set(takenNames.map(nameKey));
  const trimmedQuery = query.trim().toLowerCase();
  const filtered = players
    .filter((p) => !trimmedQuery || nameKey(p.name).includes(trimmedQuery))
    .filter((p) => !hideDrafted || !takenSet.has(nameKey(p.name)));

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content adp-watchlist-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>ADP board</h2>
          <button type="button" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="adp-watchlist-controls">
          <input
            type="text"
            className="adp-watchlist-search"
            placeholder="Search players..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
          <label className="adp-watchlist-hide-drafted">
            <input
              type="checkbox"
              checked={hideDrafted}
              onChange={(e) => setHideDrafted(e.target.checked)}
            />
            Hide drafted players
          </label>
        </div>
        {loading && <p className="section-hint">Loading...</p>}
        {error && <p className="error-text">{error}</p>}
        {!loading && !error && (
          <ul className="adp-watchlist-list">
            {filtered.map((p) => {
              const taken = takenSet.has(nameKey(p.name));
              return (
                <li
                  key={`${p.position}-${p.name}`}
                  className={taken ? "adp-watchlist-taken" : ""}
                >
                  <span className="adp-watchlist-rank">{p.rank}</span>
                  <span className="adp-watchlist-name">{p.name}</span>
                  <span className="adp-watchlist-pos">{p.position}</span>
                </li>
              );
            })}
            {filtered.length === 0 && (
              <li className="empty">
                {trimmedQuery ? `No players match "${query}"` : "No players left"}
              </li>
            )}
          </ul>
        )}
      </div>
    </div>
  );
}
