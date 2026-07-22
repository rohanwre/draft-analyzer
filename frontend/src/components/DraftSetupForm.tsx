import { useState } from "react";
import type { FormEvent } from "react";
import { createDraft } from "../api/client";
import type { DraftState } from "../api/types";

interface Props {
  onCreated: (state: DraftState) => void;
}

export default function DraftSetupForm({ onCreated }: Props) {
  const [leagueSize, setLeagueSize] = useState<number | "">("");
  const [draftSlot, setDraftSlot] = useState<number | "">("");
  const [season, setSeason] = useState(2026);
  const [totalRounds, setTotalRounds] = useState(15);
  const [qb, setQb] = useState(1);
  const [rb, setRb] = useState(2);
  const [wr, setWr] = useState(2);
  const [te, setTe] = useState(1);
  const [flex, setFlex] = useState(1);
  const [sflex, setSflex] = useState(0);
  const [tePremium, setTePremium] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isValid = leagueSize !== "" && draftSlot !== "" && draftSlot >= 1 && draftSlot <= leagueSize;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!isValid) return;
    setBusy(true);
    setError(null);
    try {
      const state = await createDraft({
        league_size: leagueSize,
        draft_slot: draftSlot,
        season,
        total_rounds: totalRounds,
        qb,
        rb,
        wr,
        te,
        flex,
        sflex,
        te_premium: tePremium,
      });
      onCreated(state);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create draft");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="setup-form" onSubmit={handleSubmit}>
      <h2>Set up your draft</h2>

      <label>
        Teams in your league (required)
        <input
          type="number"
          min={2}
          required
          placeholder="e.g. 12"
          value={leagueSize}
          onChange={(e) => setLeagueSize(e.target.value === "" ? "" : Number(e.target.value))}
        />
      </label>
      <label>
        Your draft slot (required{leagueSize !== "" ? `, 1-${leagueSize}` : ""})
        <input
          type="number"
          min={1}
          max={leagueSize === "" ? undefined : leagueSize}
          required
          placeholder="e.g. 4"
          value={draftSlot}
          onChange={(e) => setDraftSlot(e.target.value === "" ? "" : Number(e.target.value))}
        />
      </label>
      <label>
        Season
        <input type="number" value={season} onChange={(e) => setSeason(Number(e.target.value))} />
      </label>
      <label>
        Rounds
        <input type="number" min={1} value={totalRounds} onChange={(e) => setTotalRounds(Number(e.target.value))} />
      </label>

      <h3>League settings</h3>
      <div className="settings-grid">
        <label>
          QB starters
          <input type="number" min={0} value={qb} onChange={(e) => setQb(Number(e.target.value))} />
        </label>
        <label>
          RB starters
          <input type="number" min={0} value={rb} onChange={(e) => setRb(Number(e.target.value))} />
        </label>
        <label>
          WR starters
          <input type="number" min={0} value={wr} onChange={(e) => setWr(Number(e.target.value))} />
        </label>
        <label>
          TE starters
          <input type="number" min={0} value={te} onChange={(e) => setTe(Number(e.target.value))} />
        </label>
        <label>
          FLEX spots
          <input type="number" min={0} value={flex} onChange={(e) => setFlex(Number(e.target.value))} />
        </label>
        <label>
          SuperFlex spots
          <input type="number" min={0} value={sflex} onChange={(e) => setSflex(Number(e.target.value))} />
        </label>
      </div>

      <label className="checkbox-label">
        <input type="checkbox" checked={tePremium} onChange={(e) => setTePremium(e.target.checked)} />
        TE Premium scoring
      </label>

      {error && <p className="error-text">{error}</p>}

      <button type="submit" disabled={busy || !isValid}>
        {busy ? "Starting..." : "Start draft"}
      </button>
    </form>
  );
}
