import { useMemo, useState } from "react";
import type { Asset, Context, PickAsset, RosterTeam, SideValue } from "../engine/tradeEngine";
import { makePick } from "../engine/tradeEngine";
import { fmtValue, playerMeta } from "./format";

interface Props {
  ctx: Context;
  values: Map<string, SideValue>;
  exclude: Set<string>;
  team: RosterTeam | null;      // league mode: search this roster first
  onAdd: (a: Asset) => void;
}

function normalize(s: string) {
  return s.toLowerCase().normalize("NFD").replace(/[^a-z ]/g, "");
}

export default function AssetPicker({ ctx, values, exclude, team, onAdd }: Props) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [anyPlayer, setAnyPlayer] = useState(false);
  const [pickYear, setPickYear] = useState(ctx.data.season + 1);
  const [pickRound, setPickRound] = useState(1);
  const [pickSlot, setPickSlot] = useState<"early" | "mid" | "late" | "any">("any");
  const dynasty = ctx.settings.format === "dynasty";
  const n = ctx.settings.teams;

  const results = useMemo(() => {
    const needle = normalize(q.trim());
    const restrict = team && !anyPlayer ? new Set(team.playerIds) : null;
    const pool = ctx.data.players.filter((p) => !exclude.has(p.id) && (!restrict || restrict.has(p.id)));
    const hits = needle ? pool.filter((p) => normalize(p.name).includes(needle)) : pool;
    return hits
      .map((p) => ({ p, v: values.get(p.id)?.weighted ?? 0 }))
      .sort((a, b) => b.v - a.v)
      .slice(0, needle ? 8 : restrict ? 30 : 8);
  }, [q, ctx, values, exclude, team, anyPlayer]);

  const teamPicks: PickAsset[] = team ? team.picks.filter((pk) => !exclude.has(pk.key)) : [];

  function add(a: Asset) {
    onAdd(a);
    setQ("");
    setOpen(false);
  }

  function addPick() {
    const slot = pickSlot === "any" ? null : pickSlot === "early" ? Math.ceil(n / 6) : pickSlot === "mid" ? Math.ceil(n / 2) : n - Math.floor(n / 6);
    add(makePick(pickYear, pickRound, slot, n));
  }

  return (
    <div className="picker">
      <div className="picker-field">
        <input
          type="search"
          placeholder={team && !anyPlayer ? `Search ${team.name}'s roster` : "Add a player"}
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          aria-label="Search players"
        />
        {open && results.length > 0 && (
          <ul className="picker-results" role="listbox">
            {results.map(({ p }) => (
              <li key={p.id}>
                <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => add({ kind: "player", id: p.id, key: p.id })}>
                  <span className={`pos pos-${p.pos}`}>{p.pos}</span>
                  <span className="picker-name">{p.name}</span>
                  <span className="picker-meta">{playerMeta(p)}</span>
                  <span className="num">{values.has(p.id) ? fmtValue(ctx, values.get(p.id)!) : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      {team && (
        <label className="picker-toggle">
          <input type="checkbox" checked={anyPlayer} onChange={(e) => setAnyPlayer(e.target.checked)} />
          any player
        </label>
      )}

      {dynasty && team && teamPicks.length > 0 && (
        <div className="picker-picks">
          {teamPicks.slice(0, 12).map((pk) => (
            <button type="button" key={pk.key} className="chip" onClick={() => add(pk)}>{pk.label}</button>
          ))}
        </div>
      )}

      {dynasty && !team && (
        <div className="pick-builder">
          <select value={pickYear} onChange={(e) => setPickYear(Number(e.target.value))} aria-label="Pick year">
            {[1, 2, 3].map((d) => <option key={d} value={ctx.data.season + d}>{ctx.data.season + d}</option>)}
          </select>
          <select value={pickRound} onChange={(e) => setPickRound(Number(e.target.value))} aria-label="Pick round">
            {[1, 2, 3, 4].map((r) => <option key={r} value={r}>Round {r}</option>)}
          </select>
          <select value={pickSlot} onChange={(e) => setPickSlot(e.target.value as typeof pickSlot)} aria-label="Pick slot">
            <option value="any">Slot unknown</option>
            <option value="early">Early</option>
            <option value="mid">Mid</option>
            <option value="late">Late</option>
          </select>
          <button type="button" className="ghost" onClick={addPick}>+ Pick</button>
        </div>
      )}
    </div>
  );
}
