import { useState } from "react";
import DraftSetupForm from "./components/DraftSetupForm";
import DraftBoard from "./components/DraftBoard";
import type { DraftState } from "./api/types";
import "./App.css";

export default function App() {
  const [draftState, setDraftState] = useState<DraftState | null>(null);

  return (
    <div className="app">
      <header className="app-header">
        <h1>Fantasy Draft Advisor</h1>
      </header>
      <main>
        {draftState ? (
          <DraftBoard state={draftState} onStateChange={setDraftState} />
        ) : (
          <DraftSetupForm onCreated={setDraftState} />
        )}
      </main>
    </div>
  );
}
