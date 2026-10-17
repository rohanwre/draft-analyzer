import { useEffect, useState } from "react";
import DraftSetupForm from "./components/DraftSetupForm";
import DraftBoard from "./components/DraftBoard";
import TradePage from "./trade/TradePage";
import type { DraftState } from "./api/types";
import "./App.css";

type Route = "draft" | "trade";

// Hash routing so the static GitHub Pages / Vercel builds need no rewrite rules.
function currentRoute(): Route {
  return window.location.hash.startsWith("#/trade") ? "trade" : "draft";
}

export default function App() {
  const [draftState, setDraftState] = useState<DraftState | null>(null);
  const [route, setRoute] = useState<Route>(currentRoute);

  useEffect(() => {
    const onHash = () => setRoute(currentRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  return (
    <>
      <nav className="app-nav" aria-label="Tools">
        <span className="app-nav-brand">Draft Analyzer</span>
        <a href="#/" className={route === "draft" ? "on" : ""}>Draft room</a>
        <a href="#/trade" className={route === "trade" ? "on" : ""}>Trade desk</a>
      </nav>
      {route === "trade" ? (
        <TradePage />
      ) : (
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
      )}
    </>
  );
}
