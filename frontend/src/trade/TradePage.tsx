import { useEffect, useMemo, useState } from "react";
import {
  assetValue, buildBoard, buildContext, defaultSettings, evaluateGeneric, evaluateLeague, loadTradeData,
  makePick, playoffDelta, playoffOdds, rosterPieces, rosterSeasonTotals, seasonCount,
} from "../engine/tradeEngine";
import type { Asset, BoardRow, Context, RosterTeam, Settings, SideValue, Slots, TeamImpact, TradeData, Verdict } from "../engine/tradeEngine";
import { importLeague, leaguesForUser } from "../engine/sleeperImport";
import type { ImportedLeague, LeagueSummary } from "../engine/sleeperImport";
import AssetPicker from "./AssetPicker";
import { AgeCurves, PlayoffCurve, SeasonBars } from "./Charts";
import { assetName, decodeAssets, encodeAssets, fmtValue, pct, playerMeta, valueUnit } from "./format";
import "./trade.css";

const SETTINGS_KEY = "trade-desk-settings";

function loadSettings(): Settings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (raw) return { ...defaultSettings(), ...JSON.parse(raw) };
  } catch { /* storage blocked - defaults are fine */ }
  return defaultSettings();
}

function readHash(teams: number): { a: Asset[]; b: Asset[] } {
  const q = new URLSearchParams(window.location.hash.split("?")[1] ?? "");
  return { a: decodeAssets(q.get("a"), teams), b: decodeAssets(q.get("b"), teams) };
}

export default function TradePage() {
  const [data, setData] = useState<TradeData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<Settings>(loadSettings);
  const [aGets, setAGets] = useState<Asset[]>(() => readHash(settings.teams).a);
  const [bGets, setBGets] = useState<Asset[]>(() => readHash(settings.teams).b);
  const [league, setLeague] = useState<ImportedLeague | null>(null);
  const [teamA, setTeamA] = useState<number | null>(null);
  const [teamB, setTeamB] = useState<number | null>(null);
  const [tab, setTab] = useState<"board" | "method">("board");

  useEffect(() => { loadTradeData().then(setData, (e) => setError(String(e))); }, []);

  useEffect(() => {
    try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { /* ignore */ }
  }, [settings]);

  useEffect(() => {
    const q = new URLSearchParams();
    if (aGets.length) q.set("a", encodeAssets(aGets));
    if (bGets.length) q.set("b", encodeAssets(bGets));
    const s = q.toString();
    window.history.replaceState(null, "", `#/trade${s ? `?${s}` : ""}`);
  }, [aGets, bGets]);

  const ctx = useMemo(() => (data ? buildContext(data, settings) : null), [data, settings]);
  const board = useMemo(() => (ctx ? buildBoard(ctx) : []), [ctx]);
  const values = useMemo(() => new Map<string, SideValue>(board.map((r) => [r.player.id, r.value])), [board]);

  // League mode: next year's picks get a projected slot from how strong each lineup is now.
  const leagueTeams = useMemo<RosterTeam[] | null>(() => {
    if (!league || !ctx) return null;
    const strength = league.teams.map((t) => ({ id: t.rosterId, v: rosterSeasonTotals(ctx, rosterPieces(ctx, t.playerIds))[0] + (t.wins - t.losses) * 25 }));
    strength.sort((x, y) => x.v - y.v);
    const slotOf = new Map(strength.map((s, i) => [s.id, i + 1]));
    return league.teams.map((t) => ({
      ...t,
      picks: t.picks.map((pk) => {
        if (pk.season !== ctx.data.season + 1 || pk.originalRoster == null) return pk;
        const slot = slotOf.get(pk.originalRoster)!;
        const via = pk.originalRoster !== t.rosterId ? league.teams.find((x) => x.rosterId === pk.originalRoster)?.name : undefined;
        const proj = makePick(pk.season, pk.round, slot, ctx.settings.teams, via);
        return { ...proj, key: pk.key, originalRoster: pk.originalRoster, label: proj.label.replace(` ${pk.round}.`, ` proj ${pk.round}.`) };
      }),
    }));
  }, [league, ctx]);

  const tA = leagueTeams?.find((t) => t.rosterId === teamA) ?? null;
  const tB = leagueTeams?.find((t) => t.rosterId === teamB) ?? null;
  const leagueMode = !!(leagueTeams && tA && tB && teamA !== teamB);

  const result = useMemo(() => {
    if (!ctx) return null;
    if (leagueMode) return evaluateLeague(ctx, leagueTeams!, teamA!, teamB!, aGets, bGets);
    return { verdict: evaluateGeneric(ctx, aGets, bGets), a: null, b: null };
  }, [ctx, leagueMode, leagueTeams, teamA, teamB, aGets, bGets]);

  if (error) return <div className="trade-desk"><p className="desk-error">Couldn't load trade values: {error}</p></div>;
  if (!data || !ctx || !result) return <div className="trade-desk"><p className="desk-loading">Loading values…</p></div>;

  const labelA = tA && leagueMode ? tA.name : "Side A";
  const labelB = tB && leagueMode ? tB.name : "Side B";
  const inTrade = new Set([...aGets, ...bGets].map((x) => x.key));

  const update = (patch: Partial<Settings>) => setSettings((s) => ({ ...s, ...patch }));

  function onLeague(l: ImportedLeague | null) {
    setLeague(l);
    setTeamA(null);
    setTeamB(null);
    setAGets([]);
    setBGets([]);
    if (l) setSettings((s) => ({ ...s, ...l.settings }));
  }

  return (
    <div className="trade-desk">
      <header className="masthead">
        <div>
          <p className="kicker">Trade desk · {data.season} week {data.week}</p>
          <h1>Who wins the trade?</h1>
          <p className="dek">
            Every value here is what a player actually adds to a starting lineup, priced by how
            much lineup strength turned into playoff berths across{" "}
            <b>{(ctx.data.calibration.redraft.leagues + ctx.data.calibration.dynasty.leagues).toLocaleString()}</b> real
            Sleeper leagues. Not by what people are currently trading for.
          </p>
        </div>
      </header>

      <SettingsStrip settings={settings} update={update} locked={!!league} />

      <LeagueBar
        season={data.season}
        league={league}
        teams={leagueTeams}
        teamA={teamA}
        teamB={teamB}
        onLeague={onLeague}
        onTeams={(a, b) => { setTeamA(a); setTeamB(b); setAGets([]); setBGets([]); }}
      />

      <section className="ticket" aria-label="Trade">
        <SideColumn
          side="A" label={labelA} ctx={ctx} values={values} assets={aGets} setAssets={setAGets}
          exclude={inTrade} team={leagueMode ? tB : null}
          impact={result.a}
        />
        <div className="ticket-perf" aria-hidden><span>⇄</span></div>
        <SideColumn
          side="B" label={labelB} ctx={ctx} values={values} assets={bGets} setAssets={setBGets}
          exclude={inTrade} team={leagueMode ? tA : null}
          impact={result.b}
        />
      </section>

      <VerdictPanel
        ctx={ctx} verdict={result.verdict} labelA={labelA} labelB={labelB}
        impactA={result.a} impactB={result.b}
      />

      <Balancer
        ctx={ctx} verdict={result.verdict} board={board} exclude={inTrade}
        labelA={labelA} labelB={labelB}
        poolA={leagueMode ? tB : null} poolB={leagueMode ? tA : null}
        onAdd={(side, asset) => (side === "A" ? setAGets : setBGets)((xs) => [...xs, asset])}
      />

      <nav className="desk-tabs" role="tablist">
        <button role="tab" aria-selected={tab === "board"} className={tab === "board" ? "on" : ""} onClick={() => setTab("board")}>Value board</button>
        <button role="tab" aria-selected={tab === "method"} className={tab === "method" ? "on" : ""} onClick={() => setTab("method")}>How it's calculated</button>
      </nav>
      {tab === "board" ? (
        <ValueBoard
          ctx={ctx} rows={board}
          onAdd={(side, id) => (side === "A" ? setAGets : setBGets)((xs) => (inTrade.has(id) ? xs : [...xs, { kind: "player", id, key: id }]))}
        />
      ) : (
        <Method ctx={ctx} board={board} />
      )}

      <footer className="desk-foot">
        Projections: Sleeper, refreshed {data.generated}. Outcome data: {ctx.data.calibration.redraft.rosters.toLocaleString()} redraft
        and {ctx.data.calibration.dynasty.rosters.toLocaleString()} dynasty rosters, 2020–2025.
      </footer>
    </div>
  );
}

// ---------------------------------------------------------------------------

function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: [T, string][]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div className="seg" role="radiogroup" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} className={value === v ? "on" : ""} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

const SLOT_FIELDS: [keyof Slots, string][] = [
  ["QB", "QB"], ["RB", "RB"], ["WR", "WR"], ["TE", "TE"], ["FLEX", "FLX"], ["SUPER_FLEX", "SF"],
];

function SettingsStrip({ settings, update, locked }: { settings: Settings; update: (p: Partial<Settings>) => void; locked: boolean }) {
  return (
    <section className="strip" aria-label="League settings">
      <div className="strip-cell">
        <span className="cap">Format</span>
        <Segmented label="Format" value={settings.format} onChange={(format) => update({ format })}
          options={[["redraft", "Redraft"], ["dynasty", "Dynasty"]]} />
      </div>
      <div className="strip-cell">
        <span className="cap">Scoring</span>
        <Segmented label="Scoring" value={settings.scoring} onChange={(scoring) => update({ scoring })}
          options={[["ppr", "PPR"], ["half_ppr", "½"], ["standard", "Std"]]} />
      </div>
      <label className="strip-cell">
        <span className="cap">Teams</span>
        <input type="number" min={6} max={16} value={settings.teams} disabled={locked}
          onChange={(e) => update({ teams: Math.max(6, Math.min(16, Number(e.target.value) || 12)) })} />
      </label>
      <label className="strip-cell">
        <span className="cap">TE prem.</span>
        <select value={settings.tePremium} onChange={(e) => update({ tePremium: Number(e.target.value) })}>
          {[0, 0.25, 0.5, 0.75, 1].map((v) => <option key={v} value={v}>{v ? `+${v}` : "None"}</option>)}
        </select>
      </label>
      <div className="strip-cell strip-lineup">
        <span className="cap">Starters</span>
        <div className="lineup-inputs">
          {SLOT_FIELDS.map(([k, short]) => (
            <label key={k}>
              <input type="number" min={0} max={k === "QB" ? 2 : 4} value={settings.slots[k]} disabled={locked}
                onChange={(e) => update({ slots: { ...settings.slots, [k]: Math.max(0, Number(e.target.value) || 0) } })} />
              <span>{short}</span>
            </label>
          ))}
        </div>
      </div>
      {settings.format === "dynasty" && (
        <div className="strip-cell">
          <span className="cap">Window</span>
          <Segmented label="Contention window" value={settings.strategy} onChange={(strategy) => update({ strategy })}
            options={[["contend", "Win now"], ["balanced", "Balanced"], ["rebuild", "Rebuild"]]} />
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

function LeagueBar({ season, league, teams, teamA, teamB, onLeague, onTeams }: {
  season: number;
  league: ImportedLeague | null;
  teams: RosterTeam[] | null;
  teamA: number | null;
  teamB: number | null;
  onLeague: (l: ImportedLeague | null) => void;
  onTeams: (a: number | null, b: number | null) => void;
}) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [choices, setChoices] = useState<LeagueSummary[] | null>(null);

  async function go() {
    const v = input.trim();
    if (!v) return;
    setBusy(true);
    setErr(null);
    try {
      if (/^\d{10,}$/.test(v)) {
        onLeague(await importLeague(v, season));
        setChoices(null);
      } else {
        const ls = await leaguesForUser(v, season);
        if (!ls.length) throw new Error(`No ${season} leagues for ${v}`);
        if (ls.length === 1) onLeague(await importLeague(ls[0].id, season));
        else setChoices(ls);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function choose(id: string) {
    setBusy(true);
    try {
      onLeague(await importLeague(id, season));
      setChoices(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (league && teams) {
    return (
      <section className="league-bar on">
        <div className="league-name">
          <span className="cap">Sleeper league</span>
          <b>{league.name}</b>
          <button type="button" className="link" onClick={() => onLeague(null)}>disconnect</button>
        </div>
        <label>
          <span className="cap">Side A (you)</span>
          <select value={teamA ?? ""} onChange={(e) => onTeams(e.target.value ? Number(e.target.value) : null, teamB)}>
            <option value="">Pick a team</option>
            {teams.map((t) => <option key={t.rosterId} value={t.rosterId}>{t.name} ({t.wins}-{t.losses})</option>)}
          </select>
        </label>
        <label>
          <span className="cap">Side B</span>
          <select value={teamB ?? ""} onChange={(e) => onTeams(teamA, e.target.value ? Number(e.target.value) : null)}>
            <option value="">Trade partner</option>
            {teams.filter((t) => t.rosterId !== teamA).map((t) => <option key={t.rosterId} value={t.rosterId}>{t.name} ({t.wins}-{t.losses})</option>)}
          </select>
        </label>
        {(teamA == null || teamB == null) && <p className="hint">Pick both teams to judge the trade against your real rosters.</p>}
      </section>
    );
  }

  return (
    <section className="league-bar">
      <label className="league-input">
        <span className="cap">Use your Sleeper league (optional)</span>
        <span className="row">
          <input placeholder="Sleeper username or league ID" value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && go()} />
          <button type="button" onClick={go} disabled={busy}>{busy ? "Loading…" : "Connect"}</button>
        </span>
      </label>
      <p className="hint">Without a league, each side is valued for an average team in a league with these settings.</p>
      {err && <p className="desk-error">{err}</p>}
      {choices && (
        <ul className="league-choices">
          {choices.map((l) => (
            <li key={l.id}><button type="button" className="link" onClick={() => choose(l.id)}>{l.name}</button> <span className="muted">{l.teams} teams · {l.dynasty ? "dynasty" : "redraft"}</span></li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

function SideColumn({ side, label, ctx, values, assets, setAssets, exclude, team, impact }: {
  side: "A" | "B";
  label: string;
  ctx: Context;
  values: Map<string, SideValue>;
  assets: Asset[];
  setAssets: (f: (xs: Asset[]) => Asset[]) => void;
  exclude: Set<string>;
  team: RosterTeam | null;   // the roster this side receives from
  impact: TeamImpact | null;
}) {
  const valueOf = (a: Asset) => (a.kind === "player" ? values.get(a.id) : undefined) ?? assetValue(ctx, a);
  return (
    <div className={`side side-${side}`}>
      <h2><span className="side-mark">{side}</span>{label} <span className="gets">gets</span></h2>
      <ul className="assets">
        {assets.length === 0 && <li className="empty">Nothing yet. Add players{ctx.settings.format === "dynasty" ? " or picks" : ""} below.</li>}
        {assets.map((a) => {
          const p = a.kind === "player" ? ctx.data.byId.get(a.id) : undefined;
          const v = valueOf(a);
          return (
            <li key={a.key} className="asset">
              <span className={`pos pos-${p?.pos ?? "PK"}`}>{p?.pos ?? "PK"}</span>
              <span className="asset-name">
                {assetName(ctx, a)}
                <small>{p ? playerMeta(p) : a.kind === "pick" && a.slot == null ? "slot unknown, averaged across the round" : "rookie pick"}</small>
              </span>
              <span className="num asset-val">{fmtValue(ctx, v)}</span>
              <button type="button" className="x" aria-label={`Remove ${assetName(ctx, a)}`} onClick={() => setAssets((xs) => xs.filter((x) => x.key !== a.key))}>×</button>
            </li>
          );
        })}
      </ul>
      <AssetPicker ctx={ctx} values={values} exclude={exclude} team={team} onAdd={(a) => setAssets((xs) => [...xs, a])} />
      {impact && <ImpactTable ctx={ctx} impact={impact} />}
    </div>
  );
}

function seasonLabels(ctx: Context): string[] {
  return Array.from({ length: seasonCount(ctx.settings) }, (_, k) => (k === 0 ? `${ctx.data.season} ROS` : String(ctx.data.season + k)));
}

function ImpactTable({ ctx, impact }: { ctx: Context; impact: TeamImpact }) {
  const labels = seasonLabels(ctx);
  return (
    <table className="impact">
      <caption>Playoff odds from lineup strength (record not included)</caption>
      <thead><tr><th>Season</th><th>Before</th><th>After</th><th>Change</th></tr></thead>
      <tbody>
        {labels.map((l, k) => {
          const d = impact.oddsAfter[k] - impact.oddsBefore[k];
          return (
            <tr key={l}>
              <td>{l}</td>
              <td className="num">{pct(impact.oddsBefore[k])}</td>
              <td className="num">{pct(impact.oddsAfter[k])}</td>
              <td className={`num ${d > 0.005 ? "up" : d < -0.005 ? "down" : ""}`}>{pct(d, true)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ---------------------------------------------------------------------------

function stampText(v: Verdict, labelA: string, labelB: string): string {
  const w = v.winner === "A" ? labelA : labelB;
  switch (v.label) {
    case "empty": return "Awaiting offer";
    case "even": return "Fair deal";
    case "leans": return `Leans ${w}`;
    case "wins": return `${w} wins`;
    case "lopsided": return `Lopsided · ${w}`;
  }
}

function VerdictPanel({ ctx, verdict, labelA, labelB, impactA, impactB }: {
  ctx: Context; verdict: Verdict; labelA: string; labelB: string; impactA: TeamImpact | null; impactB: TeamImpact | null;
}) {
  const dyn = ctx.settings.format === "dynasty";
  const labels = seasonLabels(ctx);
  // Playoff odds are net: each side gains what it gets and loses what it sends.
  const k = dyn ? 1 : 0;
  const sideStat = (gets: SideValue, gives: SideValue, impact: TeamImpact | null) => ({
    odds: impact ? impact.oddsAfter[k] - impact.oddsBefore[k] : playoffDelta(ctx, (gets.bySeason[k] ?? 0) - (gives.bySeason[k] ?? 0)),
    when: labels[k],
  });
  const sa = sideStat(verdict.a, verdict.b, impactA);
  const sb = sideStat(verdict.b, verdict.a, impactB);

  return (
    <section className={`verdict v-${verdict.label} ${verdict.winner ? `win-${verdict.winner}` : ""}`} aria-live="polite">
      <div className="stamp-wrap"><div className="stamp">{stampText(verdict, labelA, labelB)}</div></div>

      <div className="tape" aria-label={`${labelA} share of value ${Math.round(verdict.share * 100)} percent`}>
        <div className="tape-a" style={{ width: `${verdict.share * 100}%` }} />
        <div className="tape-b" style={{ width: `${(1 - verdict.share) * 100}%` }} />
        <div className="tape-mid" />
        <div className="tape-ticks"><span>{labelA}</span><span>even</span><span>{labelB}</span></div>
      </div>

      <div className="verdict-figures">
        {([["A", labelA, verdict.a, sa], ["B", labelB, verdict.b, sb]] as const).map(([s, l, v, st]) => (
          <div key={s} className={`fig fig-${s}`}>
            <span className="cap">{l} gets</span>
            <span className="big num">{fmtValue(ctx, v)}<small>{valueUnit(ctx)}</small></span>
            <span className="sub">
              {dyn ? <>{Math.round(v.bySeason.slice(1).reduce((x, y) => x + y, 0) / 4 / 17 * 10) / 10} starter pts/wk, avg of next 4 seasons</> :
                <>{Math.round(v.bySeason[0])} lineup pts over {ctx.data.rosWeeks.length} weeks</>}
            </span>
            <span className="sub">
              Net <b className={st.odds > 0.002 ? "up" : st.odds < -0.002 ? "down" : ""}>{pct(st.odds, true)}</b> playoff odds, {st.when}
              {!impactA && " (avg team)"}
            </span>
          </div>
        ))}
      </div>

      {dyn && verdict.label !== "empty" && (
        <SeasonBars seasons={labels} a={verdict.a.bySeason} b={verdict.b.bySeason} labelA={labelA} labelB={labelB} />
      )}
      {verdict.label !== "empty" && <p className="verdict-note">{explain(ctx, verdict, labelA, labelB)}</p>}
    </section>
  );
}

function explain(ctx: Context, v: Verdict, labelA: string, labelB: string): string {
  if (v.label === "even") return "Within 8% of each other. Both sides add about the same to a starting lineup.";
  const w = v.winner === "A" ? v.a : v.b;
  const l = v.winner === "A" ? v.b : v.a;
  const wl = v.winner === "A" ? labelA : labelB;
  if (ctx.settings.format === "dynasty") {
    const nowEdge = w.bySeason[0] + w.bySeason[1] - l.bySeason[0] - l.bySeason[1];
    const laterEdge = w.bySeason.slice(2).reduce((a, b) => a + b, 0) - l.bySeason.slice(2).reduce((a, b) => a + b, 0);
    if (nowEdge > 0 && laterEdge < 0) return `${wl} wins the next two seasons; the other side wins the years after. Your contention window decides it.`;
    if (nowEdge < 0 && laterEdge > 0) return `${wl} gives up points now for more in later seasons. A rebuild-style win.`;
  }
  const more = v.ratio >= 2 ? `${v.ratio >= 10 ? Math.round(v.ratio) : v.ratio.toFixed(1)}× the` : `${Math.round((v.ratio - 1) * 100)}% more`;
  return `${wl} gets ${more} lineup value of the other side.`;
}

// ---------------------------------------------------------------------------

function Balancer({ ctx, verdict, board, exclude, labelA, labelB, poolA, poolB, onAdd }: {
  ctx: Context; verdict: Verdict; board: BoardRow[]; exclude: Set<string>;
  labelA: string; labelB: string; poolA: RosterTeam | null; poolB: RosterTeam | null;
  onAdd: (side: "A" | "B", a: Asset) => void;
}) {
  if (!verdict.winner || verdict.label === "leans") return null;
  const short: "A" | "B" = verdict.winner === "A" ? "B" : "A";
  const gap = Math.abs(verdict.diff);
  const pool = short === "A" ? poolA : poolB;
  const allowed = pool ? new Set(pool.playerIds) : null;
  const cands: { asset: Asset; v: number }[] = board
    .filter((r) => !exclude.has(r.player.id) && (!allowed || allowed.has(r.player.id)))
    .map((r) => ({ asset: { kind: "player", id: r.player.id, key: r.player.id } as Asset, v: r.value.weighted }));
  if (ctx.settings.format === "dynasty") {
    const picks = pool ? pool.picks : [1, 2, 3].map((rd) => makePick(ctx.data.season + 1, rd, null, ctx.settings.teams));
    for (const pk of picks) if (!exclude.has(pk.key)) cands.push({ asset: pk, v: assetValue(ctx, pk).weighted });
  }
  const fits = cands
    .filter((c) => c.v >= gap * 0.5 && c.v <= gap * 1.5)
    .sort((a, b) => Math.abs(a.v - gap) - Math.abs(b.v - gap))
    .slice(0, 6);
  if (!fits.length) return null;
  return (
    <section className="balancer">
      <span className="cap">To even it out, {short === "A" ? labelA : labelB} could also get one of</span>
      <div className="chips">
        {fits.map((c) => (
          <button key={c.asset.key} type="button" className="chip" onClick={() => onAdd(short, c.asset)}>
            {assetName(ctx, c.asset)} <span className="num">{fmtValue(ctx, assetValue(ctx, c.asset))}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------

function ValueBoard({ ctx, rows, onAdd }: { ctx: Context; rows: BoardRow[]; onAdd: (side: "A" | "B", id: string) => void }) {
  const [pos, setPos] = useState<"ALL" | "QB" | "RB" | "WR" | "TE">("ALL");
  const [q, setQ] = useState("");
  const dyn = ctx.settings.format === "dynasty";
  const shown = rows
    .filter((r) => (pos === "ALL" || r.player.pos === pos) && (!q || r.player.name.toLowerCase().includes(q.toLowerCase())))
    .slice(0, 150);
  const maxSeason = Math.max(1, ...rows.slice(0, 50).flatMap((r) => r.value.bySeason));

  return (
    <section className="board">
      <div className="board-filters">
        <Segmented label="Position" value={pos} onChange={setPos}
          options={[["ALL", "All"], ["QB", "QB"], ["RB", "RB"], ["WR", "WR"], ["TE", "TE"]]} />
        <input type="search" placeholder="Filter" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Filter players" />
      </div>
      <div className="board-scroll">
        <table>
          <thead>
            <tr>
              <th className="num">#</th>
              <th>Player</th>
              <th className="num">{dyn ? "Dyn pts" : "Pts/wk"}</th>
              {dyn && <th className="hide-sm">By season</th>}
              <th className="num hide-sm" title="Rank by preseason ADP in this format">Market</th>
              <th className="hide-sm">vs. market</th>
              <th><span className="sr">Add</span></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const gap = r.marketRank != null ? r.marketRank - r.rank : null;
              const signal = gap == null ? "" : gap >= 15 ? "buy" : gap <= -15 ? "sell" : "";
              return (
                <tr key={r.player.id}>
                  <td className="num muted">{r.rank}</td>
                  <td>
                    <span className={`pos pos-${r.player.pos}`}>{r.player.pos}</span>{" "}
                    <b>{r.player.name}</b> <small className="muted">{playerMeta(r.player)}</small>
                  </td>
                  <td className="num">{fmtValue(ctx, r.value)}</td>
                  {dyn && (
                    <td className="hide-sm">
                      <svg className="spark" viewBox="0 0 60 18" aria-label={`By season: ${r.value.bySeason.map((v) => Math.round(v)).join(", ")}`}>
                        {r.value.bySeason.map((v, k) => {
                          const h = Math.max(1, (Math.max(0, v) / maxSeason) * 16);
                          return <rect key={k} x={k * 12} y={18 - h} width={10} height={h} rx={1.5} />;
                        })}
                      </svg>
                    </td>
                  )}
                  <td className="num hide-sm muted">{r.marketRank ?? "–"}</td>
                  <td className="hide-sm">{signal && <span className={`signal ${signal}`}>{signal === "buy" ? "Buy" : "Sell"} {Math.abs(gap!)}</span>}</td>
                  <td className="add-cell">
                    <button type="button" className="mini" onClick={() => onAdd("A", r.player.id)} aria-label={`Give ${r.player.name} to side A`}>A</button>
                    <button type="button" className="mini" onClick={() => onAdd("B", r.player.id)} aria-label={`Give ${r.player.name} to side B`}>B</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="hint">
        "Market" is preseason ADP rank in this format. <b>Buy</b> = our lineup model ranks the player
        well above where drafters took him; <b>Sell</b> = the reverse.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------

function Method({ ctx, board }: { ctx: Context; board: BoardRow[] }) {
  const cal = ctx.data.calibration[ctx.settings.format];
  const perPoint = playoffOdds(ctx, 1 / cal.weeklySd) - playoffOdds(ctx, 0);
  const pickRows = ctx.settings.format === "dynasty"
    ? [[1, 1], [1, Math.ceil(ctx.settings.teams / 2)], [1, ctx.settings.teams], [2, Math.ceil(ctx.settings.teams / 2)], [3, Math.ceil(ctx.settings.teams / 2)]].map(([rd, sl]) => {
        const pk = makePick(ctx.data.season + 1, rd, sl, ctx.settings.teams);
        const v = assetValue(ctx, pk).weighted;
        const near = board.find((r) => r.value.weighted <= v);
        return { pk, v, near };
      })
    : [];

  return (
    <section className="method">
      <div className="method-col">
        <h3>1 · Value is lineup points, not trade demand</h3>
        <p>
          KTC, FantasyCalc and Roster Audit price players by what people are trading for or voting on.
          Here, each player's Sleeper rest-of-season projection (week by week, byes included) is dropped
          into simulated {ctx.settings.teams}-team lineups with your starter settings. His value is how many
          starter points he adds on average. Packages are valued together, so two WR3s who can't both start
          aren't worth one WR1. The consolidation premium comes from the lineup math, not a fudge factor.
        </p>
        <h3>2 · Points priced by real outcomes</h3>
        <p>
          Across <b>{cal.leagues.toLocaleString()}</b> {ctx.settings.format} leagues
          ({cal.rosters.toLocaleString()} rosters), teams whose drafted lineup was stronger relative to their league made
          the playoffs this often. One extra starter point per week is worth about <b>{pct(perPoint)}</b> in
          playoff odds for an average team.
        </p>
        <PlayoffCurve points={cal.curve} fit={(z) => playoffOdds(ctx, z)} />
      </div>
      <div className="method-col">
        <h3>3 · Aging from 6 seasons of projections</h3>
        <p>
          Dynasty values project each player 4 more seasons using how players of the same position and age
          actually held their projected output year to year (2020–2026), relative to the league as a whole.
          Elite young players get less growth than mid-tier young players. Future seasons are weighted by your
          window: win now, balanced, or rebuild.
        </p>
        <AgeCurves startAge={ctx.data.ageCurves.startAge} byPos={ctx.data.ageCurves.byPos} />
        <h3>4 · Picks are what that pick actually became</h3>
        <p>
          A rookie pick's value is the average lineup impact of the players really taken at that spot in
          thousands of crawled Sleeper rookie drafts ({ctx.settings.slots.SUPER_FLEX ? "superflex" : "1QB"}),
          including the busts.
        </p>
        {pickRows.length > 0 && (
          <table className="pick-table">
            <thead><tr><th>Pick</th><th className="num">Dyn pts</th><th>Comparable player</th></tr></thead>
            <tbody>
              {pickRows.map(({ pk, v, near }) => (
                <tr key={pk.key}><td>{pk.label}</td><td className="num">{Math.round(v)}</td><td>{near ? `${near.player.name} (#${near.rank})` : "–"}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        <h3>What it doesn't know</h3>
        <p className="muted">
          Current win-loss record, injuries beyond what Sleeper's projections reflect, team needs beyond your
          starting lineup, and kickers, defenses and IDP. Market rank is preseason ADP, so it's stale by midseason.
        </p>
      </div>
    </section>
  );
}
