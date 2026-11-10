// Trade calculator engine. Runs entirely client-side on trade_values.json (built by
// build_trade_values.py).
//
// Unlike market calculators (KTC, Roster Audit, FantasyCalc), an asset's value here is
// what it does to a starting lineup, converted to playoff odds with a curve fit on
// ~65k real Sleeper leagues:
//   value = starter points the asset adds to a lineup, summed over the seasons that
//           matter for the format (ROS for redraft, ROS + 4 seasons for dynasty).
// Packages are evaluated jointly (two WR3s can't both start), so the "consolidation
// premium" falls out of the lineup math instead of being a hand-tuned fudge factor.
//
// A player's headline value blends that lineup model with the FantasyPros expert
// consensus dynasty rank (mapped onto the model's own value curve, so both are in lineup
// points). One projection source alone overrates whoever Sleeper happens to like this
// week; the experts see draft capital, role and talent the projection can't.

export type Pos = "QB" | "RB" | "WR" | "TE";
export type Format = "redraft" | "dynasty";
export type Scoring = "ppr" | "half_ppr" | "standard";
export type Strategy = "contend" | "balanced" | "rebuild";

export interface Slots {
  QB: number;
  RB: number;
  WR: number;
  TE: number;
  FLEX: number;        // RB/WR/TE
  WRRB_FLEX: number;   // RB/WR
  REC_FLEX: number;    // WR/TE
  SUPER_FLEX: number;  // any
}

export interface Settings {
  format: Format;
  teams: number;
  scoring: Scoring;
  tePremium: number;   // extra points per TE reception
  slots: Slots;
  strategy: Strategy;
}

export interface TradePlayer {
  id: string;
  name: string;
  pos: Pos;
  team: string;
  age: number | null;
  exp: number | null;
  injury: string | null;
  ros: number[];       // PPR projection per remaining week
  rosRec: number[];    // projected receptions per remaining week
  base: number;        // healthy per-game PPR projection this season
  baseRec: number;
  ytd: number | null;
  gamesPlayed: number;
  market: Record<string, number>;   // "redraft|standard" -> preseason ADP
  experts: [number, number] | null; // FantasyPros dynasty consensus rank: [1QB, superflex]
}

export interface PickAsset {
  kind: "pick";
  season: number;
  round: number;
  slot: number | null;     // null = unknown, averaged across the round
  label: string;
  key: string;
  originalRoster?: number;   // league mode: whose pick it originally was
}

export interface PlayerAsset {
  kind: "player";
  id: string;
  key: string;
}

export type Asset = PlayerAsset | PickAsset;

interface Calibration {
  playoffA: number;
  playoffB: number;
  winSlope: number;
  weeklySd: number;
  rosters: number;
  leagues: number;
  curve: [number, number, number][];
}

export interface TradeData {
  generated: string;
  season: number;
  week: number;
  rosWeeks: number[];
  players: TradePlayer[];
  byId: Map<string, TradePlayer>;
  calibration: Record<Format, Calibration>;
  ageCurves: { startAge: number; byPos: Record<Pos, number[]>; levelEdges: number[]; ageBandEdges: number[]; levelAdj: number[][] };
  picks: {
    trajectories: number[][];     // [posIdx, y1..y5] points/week
    byLeagueType: Record<"standard" | "qb_premium", [number, number][][]>;
    years: number;
  };
}

interface RawPlayer {
  id: string; n: string; p: Pos; t: string; a: number | null; x: number | null; inj: string | null;
  w: number[]; r: number[]; b: number; br: number; ytd: number | null; gp: number; m: Record<string, number>;
  e?: [number, number] | null;
}

const POSITIONS: Pos[] = ["QB", "RB", "WR", "TE"];
const FUTURE_SEASONS = 4;
const SEASON_WEEKS = 17;
// Per-game projection -> points/week over a full season (one bye + typical missed time),
// so future seasons are on the same footing as the historical age curves and pick outcomes.
const AVAILABILITY = 15 / 17;
const DRAWS = 40;
// Cap on projected growth over a player's current level. The age curves compound year
// over year, and for young TEs/WRs that ran to +70% in four seasons.
const MAX_GROWTH = 1.3;
// The curves are fit on every player with a role, so washouts dominate them, and four
// seasons of compounding turned that into young TEs ballooning and 26-year-old WR1s
// collapsing. Apply half of each year's fitted change.
const AGE_CURVE_STRENGTH = 0.5;
// Share of a player's value that comes from the expert consensus (the rest is the lineup
// model). Expert ranks are dynasty ranks, so they count for less in redraft.
export const EXPERT_WEIGHT: Record<Format, number> = { dynasty: 0.6, redraft: 0.2 };

// How much each season counts in dynasty. Index 0 = rest of this season.
export const STRATEGY_WEIGHTS: Record<Strategy, number[]> = {
  contend: [1, 0.6, 0.35, 0.2, 0.1],
  balanced: [1, 0.85, 0.7, 0.55, 0.4],
  rebuild: [0.35, 0.9, 0.9, 0.8, 0.65],
};

let loadPromise: Promise<TradeData> | null = null;

export function loadTradeData(): Promise<TradeData> {
  if (!loadPromise) {
    loadPromise = (async () => {
      const res = await fetch(`${import.meta.env.BASE_URL}data/trade_values.json`);
      if (!res.ok) throw new Error(`Failed to load trade values: ${res.status}`);
      return buildTradeData(await res.json());
    })();
  }
  return loadPromise;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function buildTradeData(raw: any): TradeData {
  const players: TradePlayer[] = (raw.players as RawPlayer[]).map((p) => ({
    id: p.id, name: p.n, pos: p.p, team: p.t, age: p.a, exp: p.x, injury: p.inj,
    ros: p.w, rosRec: p.r, base: p.b, baseRec: p.br, ytd: p.ytd, gamesPlayed: p.gp, market: p.m,
    experts: p.e ?? null,
  }));
  return { ...raw, players, byId: new Map(players.map((p) => [p.id, p])) };
}

// ---------------------------------------------------------------------------
// Scoring and per-season projections
// ---------------------------------------------------------------------------

function recPoints(scoring: Scoring): number {
  return scoring === "ppr" ? 1 : scoring === "half_ppr" ? 0.5 : 0;
}

function adjust(ppr: number, rec: number, pos: Pos, s: Settings): number {
  return ppr - (1 - recPoints(s.scoring)) * rec + (pos === "TE" ? s.tePremium * rec : 0);
}

/** Expected next-season / this-season points ratio, by age and by how good the player already is (PPR points/week). */
export function retention(data: TradeData, pos: Pos, age: number, level: number): number {
  const { byPos, startAge, levelEdges, ageBandEdges, levelAdj } = data.ageCurves;
  const arr = byPos[pos];
  const i = Math.max(0, Math.min(arr.length - 1, Math.floor(age) - startAge));
  const band = ageBandEdges.filter((e) => age >= e).length;
  const r = arr[i] * levelAdj[band][levelEdges.filter((e) => level >= e).length];
  return 1 + AGE_CURVE_STRENGTH * (r - 1);
}

export function seasonCount(s: Settings): number {
  return s.format === "dynasty" ? 1 + FUTURE_SEASONS : 1;
}

/** Points/week a player is expected to score in future season k (k >= 1). */
function futurePpg(data: TradeData, p: TradePlayer, k: number, s: Settings): number {
  // Age/level curves are fit on PPR points; track the PPR level and convert at the end.
  let ppr = p.base * AVAILABILITY;
  const age = p.age ?? 26;
  for (let j = 0; j < k; j++) ppr = Math.min(ppr * retention(data, p.pos, age + j, ppr), p.base * AVAILABILITY * MAX_GROWTH);
  return p.base > 0 ? adjust(p.base, p.baseRec, p.pos, s) * (ppr / p.base) : 0;
}

/** Weekly values for season k: the real ROS weeks (byes included) for k=0, else one representative week. */
function playerWeeks(data: TradeData, p: TradePlayer, k: number, s: Settings): number[] {
  if (k === 0) return p.ros.map((v, i) => adjust(v, p.rosRec[i], p.pos, s));
  return [futurePpg(data, p, k, s)];
}

function weeksIn(data: TradeData, k: number): number {
  return k === 0 ? data.rosWeeks.length : SEASON_WEEKS;
}

// ---------------------------------------------------------------------------
// Lineups
// ---------------------------------------------------------------------------

interface Piece { pos: Pos; v: number }

const FLEX_ORDER: [keyof Slots, Pos[]][] = [
  ["WRRB_FLEX", ["RB", "WR"]],
  ["REC_FLEX", ["WR", "TE"]],
  ["FLEX", ["RB", "WR", "TE"]],
  ["SUPER_FLEX", ["QB", "RB", "WR", "TE"]],
];

/** Best starting lineup total. Greedy is exact here: dedicated slots first, then flex slots from most to least restrictive. */
export function lineupTotal(pieces: Piece[], slots: Slots): number {
  const byPos: Record<Pos, number[]> = { QB: [], RB: [], WR: [], TE: [] };
  for (const pc of pieces) byPos[pc.pos].push(pc.v);
  let total = 0;
  const left: Piece[] = [];
  for (const pos of POSITIONS) {
    const vals = byPos[pos].sort((a, b) => b - a);
    const n = slots[pos];
    for (let i = 0; i < vals.length; i++) {
      if (i < n) total += vals[i];
      else left.push({ pos, v: vals[i] });
    }
  }
  left.sort((a, b) => b.v - a.v);
  for (const [slot, eligible] of FLEX_ORDER) {
    for (let n = 0; n < slots[slot]; n++) {
      const i = left.findIndex((pc) => eligible.includes(pc.pos));
      if (i < 0) break;
      total += left[i].v;
      left.splice(i, 1);
    }
  }
  return total;
}

export function startersPerTeam(slots: Slots): number {
  return Object.values(slots).reduce((a, b) => a + b, 0);
}

// ---------------------------------------------------------------------------
// Generic league context
// ---------------------------------------------------------------------------

export interface Context {
  data: TradeData;
  settings: Settings;
  // Per season, per simulated team: its starters (mean weekly values). A new asset is
  // worth its average lineup gain across these teams - "what it's worth to a team in
  // this league" - which is far more stable than any single median team.
  baseline: Piece[][][];
  // Every player's standalone value: lineup model alone and blended with the experts.
  values: Map<string, PlayerValue>;
}

export interface PlayerValue {
  model: SideValue;
  value: SideValue;            // headline: model blended with expert consensus
  modelRank: number;
  expertRank: number | null;   // order among players in the pool, by consensus rank
}

/** Snake-drafts the player pool into N lineups; each team only takes a player who'd start for it. */
function simulateLeague(data: TradeData, s: Settings, k: number): Piece[][] {
  const pool: Piece[] = data.players.map((p) => {
    const w = playerWeeks(data, p, k, s);
    return { pos: p.pos, v: w.reduce((a, b) => a + b, 0) / w.length };
  }).sort((a, b) => b.v - a.v);

  const teams: Piece[][] = Array.from({ length: s.teams }, () => []);
  const need = startersPerTeam(s.slots);
  let round = 0;
  const taken = new Set<number>();
  while (round < need) {
    const order = [...teams.keys()];
    if (round % 2) order.reverse();
    for (const t of order) {
      for (let i = 0; i < pool.length; i++) {
        if (taken.has(i)) continue;
        const trial = [...teams[t], pool[i]];
        // A player only helps if he'd start; check he raises the lineup.
        if (lineupTotal(trial, s.slots) > lineupTotal(teams[t], s.slots) + 1e-9) {
          teams[t].push(pool[i]);
          taken.add(i);
          break;
        }
      }
    }
    round++;
  }
  return teams;
}

export function buildContext(data: TradeData, settings: Settings): Context {
  const baseline: Piece[][][] = [];
  for (let k = 0; k < seasonCount(settings); k++) baseline.push(simulateLeague(data, settings, k));
  const ctx: Context = { data, settings, baseline, values: new Map() };
  ctx.values = blendValues(ctx);
  return ctx;
}

/**
 * Model value per player, plus the expert consensus mapped onto the model's own value
 * curve: the Nth player by consensus rank gets the Nth highest model value. The headline
 * value is a weighted mix of the two, split across seasons in the model's shape.
 */
function blendValues(ctx: Context): Map<string, PlayerValue> {
  const s = ctx.settings;
  const col = isSuperflex(s) ? 1 : 0;
  const w = EXPERT_WEIGHT[s.format];
  const seasonW = weightsFor(s);
  const model = ctx.data.players.map((p) => ({ p, v: summarize(ctx, packageGain(ctx, [{ kind: "player", id: p.id, key: p.id }], null)) }));
  const curve = model.map((m) => m.v.weighted).sort((a, b) => b - a);
  const modelRank = new Map([...model].sort((a, b) => b.v.weighted - a.v.weighted).map((m, i) => [m.p.id, i + 1]));
  const ranked = model.filter((m) => m.p.experts).sort((a, b) => a.p.experts![col] - b.p.experts![col]);
  const expertRank = new Map(ranked.map((m, i) => [m.p.id, i + 1]));
  // Unranked by the experts = below everyone they did rank.
  const floor = curve[Math.min(ranked.length, curve.length - 1)] ?? 0;

  const out = new Map<string, PlayerValue>();
  for (const { p, v } of model) {
    const er = expertRank.get(p.id) ?? null;
    const expert = er != null ? curve[er - 1] : floor;
    const shape = v.weighted > 1 ? v.bySeason.map((x) => x / v.weighted) : seasonW.map(() => 1 / seasonW.reduce((a, b) => a + b, 0));
    const bySeason = v.bySeason.map((x, k) => (1 - w) * x + w * expert * shape[k]);
    out.set(p.id, { model: v, value: summarize(ctx, bySeason), modelRank: modelRank.get(p.id)!, expertRank: er });
  }
  return out;
}

/** Per season, how much the expert blend moves these players off their pure lineup value. */
function blendDelta(ctx: Context, assets: Asset[]): number[] {
  const out = Array(seasonCount(ctx.settings)).fill(0);
  for (const a of assets) {
    const pv = a.kind === "player" ? ctx.values.get(a.id) : undefined;
    if (pv) pv.value.bySeason.forEach((v, k) => { out[k] += v - pv.model.bySeason[k]; });
  }
  return out;
}

/** Package value: joint lineup math (consolidation, positional overlap) plus each player's expert adjustment. */
function blendedGain(ctx: Context, assets: Asset[], roster: Piece[][][] | null, removed: Asset[] = []): number[] {
  const delta = blendDelta(ctx, assets);
  return packageGain(ctx, assets, roster, removed).map((v, k) => v + delta[k]);
}

// ---------------------------------------------------------------------------
// Picks
// ---------------------------------------------------------------------------

function isSuperflex(s: Settings): boolean {
  return s.slots.SUPER_FLEX > 0 || s.slots.QB >= 2;
}

/**
 * DRAWS representative outcomes for a rookie pick: players actually taken at that
 * rookie-draft position in real Sleeper dynasty leagues, weighted by how often.
 * Returned as [pos, points/week in draft year 1..5].
 */
const pickDrawCache = new Map<string, [Pos, number[]][]>();

export function pickDraws(data: TradeData, s: Settings, pick: PickAsset): [Pos, number[]][] {
  const lt = isSuperflex(s) ? "qb_premium" : "standard";
  const key = `${lt}|${s.teams}|${pick.round}|${pick.slot}`;
  const hit = pickDrawCache.get(key);
  if (hit) return hit;

  const slotRows = data.picks.byLeagueType[lt];
  const slotsInRound = pick.slot ? [pick.slot] : Array.from({ length: s.teams }, (_, i) => i + 1);
  const weighted: [number, number][] = [];   // [trajectory idx, weight]
  for (const slot of slotsInRound) {
    const overall = Math.min(slotRows.length, (pick.round - 1) * s.teams + slot);
    const row = slotRows[overall - 1];
    const n = row.reduce((a, [, c]) => a + c, 0);
    for (const [idx, c] of row) weighted.push([idx, c / n / slotsInRound.length]);
  }
  // Sort outcomes by total production and take DRAWS evenly spaced quantiles.
  const total = (idx: number) => data.picks.trajectories[idx].slice(1).reduce((a, b) => a + b, 0);
  weighted.sort((a, b) => total(b[0]) - total(a[0]));
  const draws: [Pos, number[]][] = [];
  let cum = 0;
  let j = 0;
  for (let d = 0; d < DRAWS; d++) {
    const target = (d + 0.5) / DRAWS;
    while (j < weighted.length - 1 && cum + weighted[j][1] < target) {
      cum += weighted[j][1];
      j++;
    }
    const t = data.picks.trajectories[weighted[j][0]];
    draws.push([POSITIONS[t[0]], t.slice(1)]);
  }
  pickDrawCache.set(key, draws);
  return draws;
}

function pickWeeks(data: TradeData, s: Settings, pick: PickAsset, draw: [Pos, number[]], k: number): Piece | null {
  const year = data.season + k - pick.season;   // 0 = the season the pick is used
  if (year < 0 || k === 0) return null;
  const [pos, traj] = draw;
  // Trajectories are PPR points/week; apply a rough reception share for other scoring.
  const recShare = { QB: 0, RB: 0.12, WR: 0.3, TE: 0.32 }[pos];
  const scale = 1 - (1 - recPoints(s.scoring)) * recShare + (pos === "TE" ? s.tePremium * recShare : 0);
  const v = (traj[Math.min(year, traj.length - 1)] ?? 0) * scale;
  return { pos, v };
}

// ---------------------------------------------------------------------------
// Valuation
// ---------------------------------------------------------------------------

export interface SideValue {
  bySeason: number[];      // lineup points added per season
  weighted: number;        // strategy-weighted total (the headline number)
  perWeekNow: number;      // this season, per remaining week
}

function weightsFor(s: Settings): number[] {
  return s.format === "dynasty" ? STRATEGY_WEIGHTS[s.strategy] : [1];
}

/**
 * Lineup points a set of assets adds to a roster, per season.
 * roster: per-season weekly pieces the team already has (null = generic median team).
 */
export function packageGain(ctx: Context, assets: Asset[], roster: Piece[][][] | null, removed: Asset[] = []): number[] {
  const { data, settings: s } = ctx;
  const players = assets.filter((a): a is PlayerAsset => a.kind === "player").map((a) => data.byId.get(a.id)).filter(Boolean) as TradePlayer[];
  const picks = assets.filter((a): a is PickAsset => a.kind === "pick");
  const removedIds = new Set(removed.filter((a) => a.kind === "player").map((a) => (a as PlayerAsset).id));
  const out: number[] = [];

  for (let k = 0; k < seasonCount(s); k++) {
    const weeks = weeksIn(data, k);
    const playerSeries = players.map((p) => ({ pos: p.pos, w: playerWeeks(data, p, k, s) }));
    const nWeekCols = k === 0 ? data.rosWeeks.length : 1;
    const drawsNeeded = picks.length && k > 0 ? DRAWS : 1;
    let gainSum = 0;

    for (let d = 0; d < drawsNeeded; d++) {
      const pickPieces: Piece[] = [];
      picks.forEach((pk, j) => {
        const draws = pickDraws(data, s, pk);
        // Offset each pick's draw so multiple picks don't all hit or all bust together.
        const piece = pickWeeks(data, s, pk, draws[(d + j * 17) % draws.length], k);
        if (piece) pickPieces.push(piece);
      });
      let gain = 0;
      for (let wk = 0; wk < nWeekCols; wk++) {
        const added = playerSeries.map((ps) => ({ pos: ps.pos, v: ps.w[wk] ?? 0 })).concat(pickPieces);
        if (roster) {
          const kept = roster[k][wk].filter((pc) => !removedIds.has((pc as Piece & { id?: string }).id ?? ""));
          gain += lineupTotal(kept.concat(added), s.slots) - lineupTotal(kept, s.slots);
        } else {
          const league = ctx.baseline[k];
          for (const team of league) gain += (lineupTotal(team.concat(added), s.slots) - lineupTotal(team, s.slots)) / league.length;
        }
      }
      gainSum += k === 0 ? gain : gain * weeks;
    }
    out.push(gainSum / drawsNeeded);
  }
  return out;
}

export function summarize(ctx: Context, bySeason: number[]): SideValue {
  const w = weightsFor(ctx.settings);
  return {
    bySeason,
    weighted: bySeason.reduce((a, v, k) => a + v * (w[k] ?? 0), 0),
    perWeekNow: bySeason[0] / ctx.data.rosWeeks.length,
  };
}

export function assetValue(ctx: Context, asset: Asset): SideValue {
  const pv = asset.kind === "player" ? ctx.values.get(asset.id) : undefined;
  return pv ? pv.value : summarize(ctx, packageGain(ctx, [asset], null));
}

// ---------------------------------------------------------------------------
// Outcomes
// ---------------------------------------------------------------------------

function logistic(x: number) {
  return 1 / (1 + Math.exp(-x));
}

/** Playoff probability for a team whose season lineup sits `z` league SDs from the mean. */
export function playoffOdds(ctx: Context, z: number): number {
  const c = ctx.data.calibration[ctx.settings.format];
  return logistic(c.playoffA + c.playoffB * z);
}

/**
 * Change in playoff odds for a median team from adding `points` lineup points to its
 * season. The calibration's SD is of full 17-week season totals, so ROS points dilute
 * naturally (weeks already played are locked in).
 */
export function playoffDelta(ctx: Context, points: number, z0 = 0): number {
  const c = ctx.data.calibration[ctx.settings.format];
  const dz = points / (c.weeklySd * SEASON_WEEKS);
  return playoffOdds(ctx, z0 + dz) - playoffOdds(ctx, z0);
}

export interface Verdict {
  a: SideValue;          // value Side A receives
  b: SideValue;
  diff: number;          // a.weighted - b.weighted
  share: number;         // a / (a + b), 0.5 = dead even
  ratio: number;         // bigger side / smaller side, 1 = dead even
  label: "even" | "leans" | "wins" | "lopsided" | "empty";
  winner: "A" | "B" | null;
}

export function verdictFrom(a: SideValue, b: SideValue): Verdict {
  const total = Math.max(0, a.weighted) + Math.max(0, b.weighted);
  if (total < 1) return { a, b, diff: 0, share: 0.5, ratio: 1, label: "empty", winner: null };
  const share = Math.max(0, a.weighted) / total;
  const hi = Math.max(a.weighted, b.weighted);
  const lo = Math.max(1, Math.min(a.weighted, b.weighted));
  const ratio = hi / lo;
  const label = ratio < 1.08 ? "even" : ratio < 1.2 ? "leans" : ratio < 1.5 ? "wins" : "lopsided";
  return { a, b, diff: a.weighted - b.weighted, share, ratio, label, winner: label === "even" ? null : share > 0.5 ? "A" : "B" };
}

/** Generic evaluation: what each package is worth to a median team in this league. */
export function evaluateGeneric(ctx: Context, aGets: Asset[], bGets: Asset[]): Verdict {
  return verdictFrom(summarize(ctx, blendedGain(ctx, aGets, null)), summarize(ctx, blendedGain(ctx, bGets, null)));
}

// ---------------------------------------------------------------------------
// Real-roster evaluation (Sleeper league import)
// ---------------------------------------------------------------------------

export interface RosterTeam {
  rosterId: number;
  name: string;
  owner: string;
  playerIds: string[];
  picks: PickAsset[];
  wins: number;
  losses: number;
}

type IdPiece = Piece & { id: string };

/** Per-season, per-week lineup pieces for a roster (ignores players without projections: K, DEF, IDP). */
export function rosterPieces(ctx: Context, playerIds: string[]): IdPiece[][][] {
  const { data, settings: s } = ctx;
  const players = playerIds.map((id) => data.byId.get(id)).filter(Boolean) as TradePlayer[];
  const out: IdPiece[][][] = [];
  for (let k = 0; k < seasonCount(s); k++) {
    const cols = k === 0 ? data.rosWeeks.length : 1;
    const series = players.map((p) => ({ p, w: playerWeeks(data, p, k, s) }));
    const weeks: IdPiece[][] = [];
    for (let wk = 0; wk < cols; wk++) weeks.push(series.map(({ p, w }) => ({ id: p.id, pos: p.pos, v: w[wk] ?? 0 })));
    out.push(weeks);
  }
  return out;
}

export function rosterSeasonTotals(ctx: Context, pieces: Piece[][][]): number[] {
  return pieces.map((weeks, k) => {
    const sum = weeks.reduce((a, w) => a + lineupTotal(w, ctx.settings.slots), 0);
    return k === 0 ? sum : sum * SEASON_WEEKS;
  });
}

export interface TeamImpact {
  before: number[];       // season lineup totals
  after: number[];
  oddsBefore: number[];   // playoff odds per season
  oddsAfter: number[];
  value: SideValue;       // net lineup change, strategy-weighted
}

/**
 * Applies the trade to both real rosters and re-ranks them against every other team in
 * the league. Playoff odds come from where each team's lineup sits in its own league,
 * run through the historical curve. (Current W-L record isn't modelled.)
 */
export function evaluateLeague(
  ctx: Context, teams: RosterTeam[], aId: number, bId: number, aGets: Asset[], bGets: Asset[],
): { a: TeamImpact; b: TeamImpact; verdict: Verdict } {
  const { settings: s } = ctx;
  const totals = new Map<number, number[]>();
  const pieces = new Map<number, IdPiece[][][]>();
  for (const t of teams) {
    const pc = rosterPieces(ctx, t.playerIds);
    pieces.set(t.rosterId, pc);
    totals.set(t.rosterId, rosterSeasonTotals(ctx, pc));
  }
  const nSeasons = seasonCount(s);

  const impact = (id: number, gets: Asset[], gives: Asset[]): { before: number[]; after: number[] } => {
    const team = teams.find((t) => t.rosterId === id)!;
    const giveIds = new Set(gives.filter((g): g is PlayerAsset => g.kind === "player").map((g) => g.id));
    const getIds = gets.filter((g): g is PlayerAsset => g.kind === "player").map((g) => g.id);
    const afterPieces = rosterPieces(ctx, team.playerIds.filter((pid) => !giveIds.has(pid)).concat(getIds));
    const before = totals.get(id)!;
    const after = rosterSeasonTotals(ctx, afterPieces);
    // Picks: expected lineup gain on top of the post-trade roster.
    const pickGain = (assets: Asset[], roster: Piece[][][]) =>
      assets.some((x) => x.kind === "pick") ? packageGain(ctx, assets.filter((x) => x.kind === "pick"), roster) : Array(nSeasons).fill(0);
    const gotPicks = pickGain(gets, afterPieces);
    const gavePicks = pickGain(gives, pieces.get(id)!);
    return { before, after: after.map((v, k) => v + gotPicks[k] - gavePicks[k]) };
  };

  const ra = impact(aId, aGets, bGets);
  const rb = impact(bId, bGets, aGets);

  const odds = (id: number, own: number[]) => own.map((v, k) => {
    const league = teams.map((t) => (t.rosterId === aId ? (id === aId ? own[k] : ra.before[k]) :
      t.rosterId === bId ? (id === bId ? own[k] : rb.before[k]) : totals.get(t.rosterId)![k]));
    const mean = league.reduce((a, x) => a + x, 0) / league.length;
    const sd = Math.sqrt(league.reduce((a, x) => a + (x - mean) ** 2, 0) / league.length) || 1;
    return playoffOdds(ctx, (v - mean) / sd);
  });

  const build = (id: number, r: { before: number[]; after: number[] }): TeamImpact => ({
    before: r.before,
    after: r.after,
    oddsBefore: odds(id, r.before),
    oddsAfter: odds(id, r.after),
    value: summarize(ctx, r.after.map((v, k) => v - r.before[k])),
  });
  const a = build(aId, ra);
  const b = build(bId, rb);
  // For the fairness call, compare how much each side's lineup improves relative to the other.
  const verdict = verdictFrom(
    summarize(ctx, blendedGain(ctx, aGets, pieces.get(aId)!, bGets)),
    summarize(ctx, blendedGain(ctx, bGets, pieces.get(bId)!, aGets)),
  );
  return { a, b, verdict };
}

// ---------------------------------------------------------------------------
// Board + helpers
// ---------------------------------------------------------------------------

export interface BoardRow {
  player: TradePlayer;
  value: SideValue;
  rank: number;
  modelRank: number;
  expertRank: number | null;
  marketRank: number | null;
}

export function marketKey(s: Settings): string {
  return `${s.format}|${isSuperflex(s) ? "qb_premium" : "standard"}`;
}

export function buildBoard(ctx: Context): BoardRow[] {
  const rows = ctx.data.players.map((p) => {
    const pv = ctx.values.get(p.id)!;
    return { player: p, value: pv.value, modelRank: pv.modelRank, expertRank: pv.expertRank };
  });
  rows.sort((a, b) => b.value.weighted - a.value.weighted);
  const mk = marketKey(ctx.settings);
  const fallbackKey = `redraft|${isSuperflex(ctx.settings) ? "qb_premium" : "standard"}`;
  const marketOrder = ctx.data.players
    .map((p) => ({ id: p.id, adp: p.market[mk] ?? p.market[fallbackKey] }))
    .filter((x) => x.adp != null)
    .sort((a, b) => a.adp! - b.adp!);
  const marketRank = new Map(marketOrder.map((x, i) => [x.id, i + 1]));
  return rows.map((r, i) => ({ ...r, rank: i + 1, marketRank: marketRank.get(r.player.id) ?? null }));
}

export function makePick(season: number, round: number, slot: number | null, teams: number, via?: string): PickAsset {
  const ord = ["1st", "2nd", "3rd", "4th", "5th"][round - 1] ?? `${round}th`;
  const slotLabel = slot ? ` ${round}.${String(slot).padStart(2, "0")}` : "";
  const label = `${season} ${ord}${slotLabel}${via ? ` (${via})` : ""}`;
  void teams;
  return { kind: "pick", season, round, slot, label, key: `pick|${season}|${round}|${slot ?? "x"}|${via ?? ""}` };
}

export function defaultSettings(): Settings {
  return {
    format: "dynasty",
    teams: 12,
    scoring: "ppr",
    tePremium: 0,
    slots: { QB: 1, RB: 2, WR: 3, TE: 1, FLEX: 2, WRRB_FLEX: 0, REC_FLEX: 0, SUPER_FLEX: 1 },
    strategy: "balanced",
  };
}
