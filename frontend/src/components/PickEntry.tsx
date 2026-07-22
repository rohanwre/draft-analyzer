import { useEffect, useRef, useState } from "react";
import { searchPlayers, lookupPick, commitPick } from "../api/client";
import type { PlayerSearchResult, PickLookupResult, DraftState } from "../api/types";

interface Props {
  draftId: string;
  season: number;
  label: string;
  onPickCommitted: (state: DraftState) => void;
}

const POSITIONS = ["QB", "RB", "WR", "TE"];

export default function PickEntry({ draftId, season, label, onPickCommitted }: Props) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<PlayerSearchResult[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [lookupResult, setLookupResult] = useState<PickLookupResult | null>(null);
  const [manualPosition, setManualPosition] = useState(POSITIONS[0]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const debounceRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (query.trim().length < 2) {
      setSuggestions([]);
      setSelectedIndex(-1);
      return;
    }
    window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => {
      searchPlayers(query, season, 8)
        .then((results) => {
          setSuggestions(results);
          setSelectedIndex(-1);
        })
        .catch(() => setSuggestions([]));
    }, 250);
    return () => window.clearTimeout(debounceRef.current);
  }, [query, season]);

  function reset() {
    setQuery("");
    setSuggestions([]);
    setSelectedIndex(-1);
    setLookupResult(null);
    setManualPosition(POSITIONS[0]);
  }

  async function commit(name: string, position: string) {
    setBusy(true);
    setError(null);
    try {
      const state = await commitPick(draftId, name, position);
      reset();
      onPickCommitted(state);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to commit pick");
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmitTyped() {
    if (!query.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const result = await lookupPick(draftId, query);
      setLookupResult(result);
      if (result.status === "already_taken" || result.status === "invalid") {
        setError(result.message ?? "Invalid pick");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lookup failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pick-entry">
      <label className="pick-entry-label">{label}</label>
      <div className="pick-entry-input-row">
        <input
          type="text"
          value={query}
          disabled={busy}
          placeholder="Type a player name..."
          onChange={(e) => {
            setQuery(e.target.value);
            setLookupResult(null);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && suggestions.length > 0) {
              e.preventDefault();
              setSelectedIndex((i) => Math.min(i + 1, suggestions.length - 1));
            } else if (e.key === "ArrowUp" && suggestions.length > 0) {
              e.preventDefault();
              setSelectedIndex((i) => Math.max(i - 1, -1));
            } else if (e.key === "Enter") {
              e.preventDefault();
              if (selectedIndex >= 0 && suggestions[selectedIndex]) {
                const s = suggestions[selectedIndex];
                commit(s.name, s.position);
              } else {
                handleSubmitTyped();
              }
            } else if (e.key === "Escape") {
              setSuggestions([]);
              setSelectedIndex(-1);
            }
          }}
        />
        <button type="button" onClick={handleSubmitTyped} disabled={busy || !query.trim()}>
          Find
        </button>
      </div>

      {suggestions.length > 0 && !lookupResult && (
        <ul className="suggestion-list">
          {suggestions.map((s, i) => (
            <li key={s.name}>
              <button
                type="button"
                className={`suggestion-btn${i === selectedIndex ? " suggestion-selected" : ""}`}
                onClick={() => commit(s.name, s.position)}
                onMouseEnter={() => setSelectedIndex(i)}
                disabled={busy}
              >
                {s.name} <span className="pos-tag">{s.position}</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {lookupResult?.status === "matched" && (
        <div className="lookup-confirm">
          <span>
            Found: <strong>{lookupResult.name}</strong> ({lookupResult.position}) — correct?
          </span>
          <button type="button" onClick={() => commit(lookupResult.name!, lookupResult.position!)} disabled={busy}>
            Yes, draft this player
          </button>
          <button type="button" onClick={() => setLookupResult(null)} disabled={busy}>
            No, try again
          </button>
        </div>
      )}

      {lookupResult?.status === "not_found" && (
        <div className="lookup-manual">
          <span>No match found for "{lookupResult.query}". Enter position manually:</span>
          <select value={manualPosition} onChange={(e) => setManualPosition(e.target.value)}>
            {POSITIONS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <button type="button" onClick={() => commit(lookupResult.query!, manualPosition)} disabled={busy}>
            Draft as {manualPosition}
          </button>
        </div>
      )}

      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
