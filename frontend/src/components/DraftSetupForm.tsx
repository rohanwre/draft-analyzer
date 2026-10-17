import { useState } from "react";
import type { FormEvent } from "react";
import { createDraft } from "../api/client";
import type { DraftState } from "../api/types";
import Segmented from "../ui/Segmented";

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
  const [leagueFormat, setLeagueFormat] = useState<"redraft" | "dynasty">("redraft");
  const [scoringType, setScoringType] = useState<"ppr" | "half_ppr" | "standard">("ppr");
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
        league_format: leagueFormat,
        scoring_type: scoringType,
      });
      onCreated(state);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create draft");
    } finally {
      setBusy(false);
    }
  }

  const slotFields: [string, number, (v: number) => void][] = [
    ["QB", qb, setQb], ["RB", rb, setRb], ["WR", wr, setWr], ["TE", te, setTe], ["FLEX", flex, setFlex], ["SFLEX", sflex, setSflex],
  ];

  return (
    <div className="room">
      <header className="masthead">
        <p className="kicker">Draft room · {season} season</p>
        <h1 className="headline">Draft like the teams that won.</h1>
        <p className="dek">
          Live pick advice from what actually worked in <b>95,000</b> real Sleeper leagues. Every pick
          is matched to your slot, the roster you've built so far, and your league's settings.
        </p>
      </header>

      <form className="setup-ticket" onSubmit={handleSubmit}>
        <section className="setup-col">
          <h2 className="section-title">Your league</h2>
          <div className="setup-grid">
            <label>
              <span className="cap">Teams</span>
              <input type="number" min={2} required placeholder="12" value={leagueSize}
                onChange={(e) => setLeagueSize(e.target.value === "" ? "" : Number(e.target.value))} />
            </label>
            <label>
              <span className="cap">Your slot{leagueSize !== "" ? ` (1-${leagueSize})` : ""}</span>
              <input type="number" min={1} max={leagueSize === "" ? undefined : leagueSize} required placeholder="4" value={draftSlot}
                onChange={(e) => setDraftSlot(e.target.value === "" ? "" : Number(e.target.value))} />
            </label>
            <label>
              <span className="cap">Rounds</span>
              <input type="number" min={1} value={totalRounds} onChange={(e) => setTotalRounds(Number(e.target.value))} />
            </label>
            <label>
              <span className="cap">Season</span>
              <input type="number" value={season} onChange={(e) => setSeason(Number(e.target.value))} />
            </label>
          </div>

          <div className="setup-row">
            <span className="cap">Draft format</span>
            <Segmented label="Draft format" value={leagueFormat} onChange={setLeagueFormat}
              options={[["redraft", "Redraft"], ["dynasty", "Dynasty startup"]]} />
          </div>
          {leagueFormat === "dynasty" && (
            <p className="hint-text">
              Dynasty advice comes from startup drafts only, judged by each team's success over the
              following seasons, not one year's result.
            </p>
          )}

          <div className="setup-row">
            <span className="cap">Scoring</span>
            <Segmented label="Scoring" value={scoringType} onChange={setScoringType}
              options={[["ppr", "PPR"], ["half_ppr", "Half PPR"], ["standard", "Standard"]]} />
          </div>
          {scoringType !== "ppr" && (
            <p className="hint-text">
              Trends come from real {scoringType === "half_ppr" ? "half-PPR" : "standard"} drafts. The ADP board
              is still averaged from full-PPR sources.
            </p>
          )}

          <label className="checkbox-label">
            <input type="checkbox" checked={tePremium} onChange={(e) => setTePremium(e.target.checked)} />
            TE premium scoring
          </label>
        </section>

        <section className="setup-col">
          <h2 className="section-title">Starting lineup</h2>
          <div className="slot-grid">
            {slotFields.map(([name, value, set]) => (
              <label key={name}>
                <input type="number" min={0} value={value} onChange={(e) => set(Number(e.target.value))} aria-label={`${name} starters`} />
                <span>{name}</span>
              </label>
            ))}
          </div>
          <p className="hint-text">FLEX takes RB/WR. SFLEX takes any position, QB included.</p>

          {error && <p className="error-text">{error}</p>}

          <button type="submit" className="primary start-btn" disabled={busy || !isValid}>
            {busy ? "Starting..." : "Start draft"}
          </button>
          {!isValid && <p className="hint-text">Enter your league size and draft slot to start.</p>}
        </section>
      </form>
    </div>
  );
}
