import { useState } from "react";
import type { DraftState } from "../api/types";
import { undoLastPick, simulateToUserTurn, commitPick } from "../api/client";
import RecommendationPanel from "./RecommendationPanel";
import PickEntry from "./PickEntry";
import RosterSummary from "./RosterSummary";
import FullDraftBoard from "./FullDraftBoard";
import AdpWatchlist from "./AdpWatchlist";

interface Props {
  state: DraftState;
  onStateChange: (state: DraftState) => void;
}

export default function DraftBoard({ state, onStateChange }: Props) {
  const [showFullBoard, setShowFullBoard] = useState(false);
  const [showAdpBoard, setShowAdpBoard] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleUndo() {
    setBusy(true);
    setError(null);
    try {
      const next = await undoLastPick(state.draft_id);
      onStateChange(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Nothing to undo");
    } finally {
      setBusy(false);
    }
  }

  async function handleSimulate() {
    setBusy(true);
    setError(null);
    try {
      const next = await simulateToUserTurn(state.draft_id);
      onStateChange(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to simulate");
    } finally {
      setBusy(false);
    }
  }

  async function handleSelectPlayer(name: string, position: string) {
    setBusy(true);
    setError(null);
    try {
      const next = await commitPick(state.draft_id, name, position);
      onStateChange(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to draft player");
    } finally {
      setBusy(false);
    }
  }

  const toolbar = (
    <div className="board-toolbar">
      <button type="button" onClick={handleUndo} disabled={busy || state.all_picks.length === 0}>
        Undo pick
      </button>
      {!state.draft_complete && (
        <button type="button" onClick={handleSimulate} disabled={busy}>
          Simulate opponent picks
        </button>
      )}
      <button type="button" onClick={() => setShowFullBoard(true)}>
        Full board
      </button>
      <button type="button" onClick={() => setShowAdpBoard(true)}>
        ADP board
      </button>
    </div>
  );

  const adpBoardModal = showAdpBoard && (
    <AdpWatchlist
      season={state.season}
      leagueType={state.league_type}
      leagueFormat={state.league_format}
      scoringType={state.scoring_type}
      takenNames={state.all_picks.map((p) => p.name)}
      onClose={() => setShowAdpBoard(false)}
    />
  );

  if (state.draft_complete) {
    return (
      <div className="draft-board">
        <header className="draft-header">
          <div>
            <p className="kicker">{state.league_size} teams · {state.total_rounds} rounds</p>
            <h1 className="headline">Draft complete.</h1>
          </div>
        </header>
        {toolbar}
        {error && <p className="error-text">{error}</p>}
        <RosterSummary
          draftId={state.draft_id}
          myPicks={state.my_picks}
          leagueSettings={state.league_settings}
          totalRounds={state.total_rounds}
          slotSwaps={state.slot_swaps}
          onStateChange={onStateChange}
        />
        {showFullBoard && (
          <FullDraftBoard
            allPicks={state.all_picks}
            leagueSize={state.league_size}
            totalRounds={state.total_rounds}
            draftSlot={state.draft_slot}
            leagueSettings={state.league_settings}
            slotSwaps={state.slot_swaps}
            onClose={() => setShowFullBoard(false)}
          />
        )}
        {adpBoardModal}
      </div>
    );
  }

  const label = state.is_user_turn
    ? "Who did you draft?"
    : `Pick ${state.current_global_pick}, slot ${state.current_pick_slot} (opponent)`;

  const scoringLabel = state.scoring_type === "half_ppr" ? "Half PPR" : state.scoring_type === "standard" ? "Standard" : "PPR";

  return (
    <div className="draft-board">
      <header className="draft-header">
        <div>
          <p className="kicker">
            {state.league_size} teams · {scoringLabel} · {state.league_format === "dynasty" ? "Dynasty startup" : "Redraft"} · slot {state.draft_slot}
          </p>
          <h1 className="draft-clock">
            <span className="cap">Round</span><span className="num">{state.current_round}</span>
            <span className="cap">Pick</span><span className="num">{state.current_global_pick}</span>
          </h1>
        </div>
        <p className={state.is_user_turn ? "turn-flag your-turn" : "turn-flag waiting-turn"}>
          {state.is_user_turn ? "You're on the clock" : `Waiting on slot ${state.current_pick_slot}`}
        </p>
      </header>

      {toolbar}
      {error && <p className="error-text">{error}</p>}

      <div className="draft-grid">
        <div className="draft-main">
          {state.is_user_turn && state.recommendation ? (
            <RecommendationPanel
              recommendation={state.recommendation}
              onSelectPlayer={handleSelectPlayer}
              disabled={busy}
            />
          ) : (
            <div className="panel-section waiting-panel">
              <h3>Opponents are picking</h3>
              <p className="section-hint">
                Enter each pick as it happens, or use "Simulate opponent picks" to jump to your next turn.
                Recommendations appear when you're on the clock.
              </p>
            </div>
          )}
        </div>
        <aside className="draft-side">
          <PickEntry
            draftId={state.draft_id}
            season={state.season}
            label={label}
            onPickCommitted={onStateChange}
          />
          <RosterSummary
            draftId={state.draft_id}
            myPicks={state.my_picks}
            leagueSettings={state.league_settings}
            totalRounds={state.total_rounds}
            slotSwaps={state.slot_swaps}
            onStateChange={onStateChange}
          />
        </aside>
      </div>

      {showFullBoard && (
        <FullDraftBoard
          allPicks={state.all_picks}
          leagueSize={state.league_size}
          totalRounds={state.total_rounds}
          draftSlot={state.draft_slot}
          leagueSettings={state.league_settings}
          slotSwaps={state.slot_swaps}
          onClose={() => setShowFullBoard(false)}
        />
      )}
      {adpBoardModal}
    </div>
  );
}
