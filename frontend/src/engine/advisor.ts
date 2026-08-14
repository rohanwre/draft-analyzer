// Client-side port of advisor.py's recommendation engine, operating on the pre-baked
// StaticData (see staticData.ts) instead of live MySQL queries. Keep this in sync with
// advisor.py — it is a line-for-line port, not a reinterpretation.
import type { AdpRow, LeagueFormat, LeagueType, Position, PositionStats, StaticData } from "./staticData";

const MIN_SAMPLE_SIZE = 50;
const SUFFIXES = new Set(["jr", "sr", "ii", "iii", "iv", "v"]);

const NAME_ALIASES: Record<string, string> = {
  "hollywood brown": "marquise brown",
  "cam ward": "cameron ward",
  "cam skattebo": "cameron skattebo",
  "kenny gainwell": "kenneth gainwell",
};

export function normalizeName(name: string | null | undefined): string {
  if (!name) return "";
  let n = name.trim().toLowerCase();
  n = n.replaceAll(".", "").replaceAll("'", "").replaceAll("-", " ");
  n = n.replace(/\s+/g, " ");
  const tokens = n.split(" ").filter((t) => t && !SUFFIXES.has(t));
  const normalized = tokens.join(" ");
  return NAME_ALIASES[normalized] ?? normalized;
}

export function deriveLeagueType(qbSlots: number, sflexSlots: number): LeagueType {
  if (sflexSlots && sflexSlots > 0) return "qb_premium";
  if (qbSlots && qbSlots >= 2) return "qb_premium";
  return "standard";
}

export function pickOrderForRound(roundNum: number, leagueSize: number): number[] {
  if (roundNum % 2 === 1) {
    return Array.from({ length: leagueSize }, (_, i) => i + 1);
  }
  return Array.from({ length: leagueSize }, (_, i) => leagueSize - i);
}

export function globalPickNumber(roundNum: number, pickSlot: number, leagueSize: number): number {
  const slotInRound = roundNum % 2 === 1 ? pickSlot : leagueSize - pickSlot + 1;
  return (roundNum - 1) * leagueSize + slotInRound;
}

function roundWeight(roundNum: number, totalRounds: number): number {
  return Math.max(1, totalRounds - roundNum + 1);
}

function bucketScore(score: number): string {
  if (score <= 0) return "NONE";
  if (score <= 10) return "LIGHT";
  if (score <= 25) return "MODERATE";
  return "HEAVY";
}

export interface MyPick {
  round: number;
  position: string;
  name: string;
}

function computeWeightedBuckets(myPicks: MyPick[], totalRounds: number): Record<Position, string> {
  const cumulative: Record<Position, number> = { QB: 0, RB: 0, WR: 0, TE: 0 };
  for (const { round, position } of myPicks) {
    if (position in cumulative) {
      cumulative[position as Position] += roundWeight(round, totalRounds);
    }
  }
  return {
    QB: bucketScore(cumulative.QB),
    RB: bucketScore(cumulative.RB),
    WR: bucketScore(cumulative.WR),
    TE: bucketScore(cumulative.TE),
  } as Record<Position, string>;
}

export function lookupPosition(data: StaticData, playerName: string, season: number): [string | null, Position | null] {
  const target = normalizeName(playerName);
  if (!target) return [null, null];

  for (const row of data.adp) {
    if (row.season === season && normalizeName(row.name) === target) {
      return [row.name, row.position];
    }
  }
  const bySeasonDesc = [...data.adp].sort((a, b) => b.season - a.season);
  for (const row of bySeasonDesc) {
    if (normalizeName(row.name) === target) {
      return [row.name, row.position];
    }
  }
  return [null, null];
}

export function searchPlayers(
  data: StaticData, query: string, season: number, limit = 10,
): Array<{ name: string; position: Position }> {
  const target = normalizeName(query);
  if (!target) return [];

  const results: Array<{ name: string; position: Position }> = [];
  const seen = new Set<string>();
  for (const row of data.adp) {
    if (row.season !== season) continue;
    const normalized = normalizeName(row.name);
    if (target && normalized.includes(target) && !seen.has(normalized)) {
      seen.add(normalized);
      results.push({ name: row.name, position: row.position });
    }
    if (results.length >= limit) break;
  }
  return results;
}

export type PickLookupStatus = "invalid" | "already_taken" | "manual" | "matched" | "not_found";
export interface PickLookupResult {
  status: PickLookupStatus;
  name?: string;
  position?: string;
  message?: string;
  query?: string;
}

export function recordPick(
  data: StaticData, allPicks: Array<[string, string]>, season: number,
  playerName: string, manualPosition?: string | null,
): PickLookupResult {
  const trimmed = (playerName ?? "").trim();
  if (!trimmed) return { status: "invalid", message: "Name can't be blank, try again." };

  const takenNormalized = new Set(allPicks.map(([, name]) => normalizeName(name)));
  if (takenNormalized.has(normalizeName(trimmed))) {
    return { status: "already_taken", message: `${trimmed} was already drafted — pick someone else.` };
  }

  if (manualPosition) {
    return { status: "manual", name: trimmed, position: manualPosition.trim().toUpperCase() };
  }

  const [matchedName, position] = lookupPosition(data, trimmed, season);
  if (position) {
    if (takenNormalized.has(normalizeName(matchedName))) {
      return { status: "already_taken", message: `${matchedName} was already drafted — pick someone else.` };
    }
    return { status: "matched", name: matchedName!, position };
  }

  return { status: "not_found", message: `No match found for '${trimmed}' in ADP data.`, query: trimmed };
}

function queryRound1Stats(
  data: StaticData, draftSlot: number, leagueSize: number, leagueType: LeagueType, tePremium: number | null,
  leagueFormat: string = "redraft",
): Partial<Record<Position, PositionStats>> {
  const key = tePremium !== null
    ? `${leagueFormat}|${draftSlot}|${leagueType}|${leagueSize}|${tePremium}`
    : `${leagueFormat}|${draftSlot}|${leagueType}|${leagueSize}`;
  const map = tePremium !== null ? data.round1Full : data.round1NoTep;
  return map.get(key) ?? {};
}

function queryTrendStats(
  data: StaticData, leagueSize: number, leagueType: LeagueType, tePremium: number | null,
  currentRound: number, buckets: Record<Position, string>, leagueFormat: string = "redraft",
): Partial<Record<Position, PositionStats>> {
  const bucketKey = `${buckets.QB}|${buckets.RB}|${buckets.WR}|${buckets.TE}`;
  const key = tePremium !== null
    ? `${leagueFormat}|${leagueSize}|${leagueType}|${tePremium}|${currentRound}|${bucketKey}`
    : `${leagueFormat}|${leagueSize}|${leagueType}|${currentRound}|${bucketKey}`;
  const map = tePremium !== null ? data.trendFull : data.trendNoTep;
  return map.get(key) ?? {};
}

function sumTotal(stats: Partial<Record<Position, PositionStats>>): number {
  return Object.values(stats).reduce((acc, s) => acc + (s?.total ?? 0), 0);
}

function findSimilarDrafts(
  data: StaticData, draftSlot: number, leagueSize: number, leagueType: LeagueType,
  tePremium: number | null, currentRound: number, buckets: Record<Position, string>,
  leagueFormat: string = "redraft",
): Partial<Record<Position, PositionStats>> {
  if (currentRound === 1) {
    let results = queryRound1Stats(data, draftSlot, leagueSize, leagueType, tePremium, leagueFormat);
    if (sumTotal(results) < MIN_SAMPLE_SIZE && tePremium !== null) {
      results = queryRound1Stats(data, draftSlot, leagueSize, leagueType, null, leagueFormat);
    }
    return results;
  }
  let results = queryTrendStats(data, leagueSize, leagueType, tePremium, currentRound, buckets, leagueFormat);
  if (sumTotal(results) < MIN_SAMPLE_SIZE && tePremium !== null) {
    results = queryTrendStats(data, leagueSize, leagueType, null, currentRound, buckets, leagueFormat);
  }
  return results;
}

export interface TrendItem {
  position: string;
  top_two_pct: number;
  sample_size?: number;
}

function calculateRecommendation(positionStats: Partial<Record<Position, PositionStats>>): TrendItem[] {
  const totalSuccess = Object.values(positionStats).reduce((acc, s) => acc + (s?.success ?? 0), 0);
  const recommendations: TrendItem[] = [];
  for (const [position, stats] of Object.entries(positionStats)) {
    if (!stats) continue;
    const topTwoPct = totalSuccess > 0 ? Math.round((stats.success / totalSuccess) * 1000) / 10 : 0;
    recommendations.push({ position, top_two_pct: topTwoPct, sample_size: stats.total });
  }
  recommendations.sort((a, b) => b.top_two_pct - a.top_two_pct);
  return recommendations;
}

function getGeneralRoundTrends(
  data: StaticData, leagueSize: number, leagueType: LeagueType, currentRound: number,
  leagueFormat: string = "redraft",
): TrendItem[] {
  const key = `${leagueFormat}|${leagueSize}|${leagueType}|${currentRound}`;
  const stats = data.trendGeneral.get(key) ?? {};
  return calculateRecommendation(stats).map(({ position, top_two_pct }) => ({ position, top_two_pct }));
}

// Falls back within the same leagueFormat first (dynasty ADP so far is superflex-only -
// a standard-type dynasty session should still see dynasty numbers for the closest
// available type, not silently jump to unrelated redraft data) and only crosses into
// redraft/standard as a last resort if this leagueFormat has no ADP data at all for the
// season. Mirrors advisor.py's resolve_adp_league_type exactly.
export function resolveAdpLeagueType(
  data: StaticData, season: number, leagueType: string, leagueFormat: string = "redraft",
): [LeagueType, LeagueFormat] {
  const hasRows = (lt: string, fmt: string) =>
    data.adp.some((r) => r.season === season && r.leagueType === lt && r.leagueFormat === fmt);

  if (hasRows(leagueType, leagueFormat)) return [leagueType as LeagueType, leagueFormat as LeagueFormat];

  const otherType = leagueType !== "standard" ? "standard" : "qb_premium";
  if (hasRows(otherType, leagueFormat)) return [otherType as LeagueType, leagueFormat as LeagueFormat];

  return ["standard", "redraft"];
}

export interface LeagueSettings {
  qb?: number; rb?: number; wr?: number; te?: number;
  flex?: number; sflex?: number; league_size?: number; total_rounds?: number;
}

export interface ScarcityAlert {
  position: string;
  taken: number;
  expected: number;
  ratio: number;
  demand_pct: number;
  threshold: number;
}

export function getScarcity(
  data: StaticData, allPicks: Array<[string, string]>, currentPickNumber: number,
  season: number, leagueSettings: LeagueSettings, adpLeagueType: LeagueType = "standard",
  adpLeagueFormat: LeagueFormat = "redraft",
): ScarcityAlert[] {
  const positionCounts: Partial<Record<Position, number>> = {};
  for (const [position] of allPicks) {
    if (["QB", "RB", "WR", "TE"].includes(position)) {
      positionCounts[position as Position] = (positionCounts[position as Position] ?? 0) + 1;
    }
  }

  const flex = leagueSettings.flex ?? 1;
  const sflex = leagueSettings.sflex ?? 0;
  const size = leagueSettings.league_size ?? 12;
  const flexShare = flex / 2;

  const demand: Record<Position, number> = {
    QB: ((leagueSettings.qb ?? 1) + sflex * 0.25) * size,
    RB: ((leagueSettings.rb ?? 2) + flexShare) * size,
    WR: ((leagueSettings.wr ?? 2) + flexShare) * size,
    TE: (leagueSettings.te ?? 1) * size,
  };
  const totalDemand = Object.values(demand).reduce((a, b) => a + b, 0);

  const alerts: ScarcityAlert[] = [];
  for (const [positionStr, count] of Object.entries(positionCounts)) {
    const position = positionStr as Position;
    const expected = data.adp.filter(
      (r) => r.season === season && r.position === position && r.adp <= currentPickNumber
        && r.leagueType === adpLeagueType && r.leagueFormat === adpLeagueFormat,
    ).length;
    if (expected === 0) continue;

    const demandRatio = totalDemand > 0 ? demand[position] / totalDemand : 0;
    const threshold = Math.max(1.05, 1.2 - demandRatio * 0.5);
    const actualRatio = (count ?? 0) / expected;
    if (actualRatio > threshold) {
      alerts.push({
        position,
        taken: count ?? 0,
        expected,
        ratio: Math.round(actualRatio * 100) / 100,
        demand_pct: Math.round(demandRatio * 1000) / 10,
        threshold: Math.round(threshold * 100) / 100,
      });
    }
  }
  alerts.sort((a, b) => b.ratio / b.threshold - a.ratio / a.threshold);
  return alerts;
}

function sortedAdpPool(
  data: StaticData, season: number, position: Position | null, adpLeagueType: LeagueType,
  adpLeagueFormat: LeagueFormat = "redraft",
) {
  return data.adp
    .filter((r) => r.season === season && r.leagueType === adpLeagueType && r.leagueFormat === adpLeagueFormat
      && (position === null || r.position === position))
    .sort((a, b) => {
      if (a.adp !== b.adp) return a.adp - b.adp;
      const aNull = a.tiebreakAdp === null ? 1 : 0;
      const bNull = b.tiebreakAdp === null ? 1 : 0;
      if (aNull !== bNull) return aNull - bNull;
      return (a.tiebreakAdp ?? 0) - (b.tiebreakAdp ?? 0);
    });
}

export function getAdpRankLookup(
  data: StaticData, season: number, adpLeagueType: LeagueType = "standard", adpLeagueFormat: LeagueFormat = "redraft",
): Map<string, number> {
  const pool = fetchAdpPool(data, season, adpLeagueType, adpLeagueFormat);
  const lookup = new Map<string, number>();
  pool.forEach((row, i) => lookup.set(normalizeName(row.name), i + 1));
  return lookup;
}

export type AvailablePlayer = [string, number, number | null]; // name, adp, rank

export function getAvailablePlayers(
  data: StaticData, allPicks: Array<[string, string]>, position: Position, season: number,
  rankLookup: Map<string, number>, adpLeagueType: LeagueType = "standard", limit = 5,
  adpLeagueFormat: LeagueFormat = "redraft",
): AvailablePlayer[] {
  const takenNormalized = new Set(allPicks.map(([, name]) => normalizeName(name)));
  const rows = sortedAdpPool(data, season, position, adpLeagueType, adpLeagueFormat);
  const available: AvailablePlayer[] = [];
  for (const row of rows) {
    if (takenNormalized.has(normalizeName(row.name))) continue;
    available.push([row.name, row.adp, rankLookup.get(normalizeName(row.name)) ?? null]);
    if (available.length >= limit) break;
  }
  return available;
}

export interface AdpPoolEntry { name: string; position: Position; adp: number }

export function fetchAdpPool(
  data: StaticData, season: number, adpLeagueType: LeagueType = "standard", adpLeagueFormat: LeagueFormat = "redraft",
): AdpPoolEntry[] {
  return sortedAdpPool(data, season, null, adpLeagueType, adpLeagueFormat)
    .filter((r) => r.adp > 0)
    .map((r) => ({ name: r.name, position: r.position, adp: r.adp }));
}

function getTeamPositionCounts(allPicks: Array<[string, string]>, leagueSize: number, targetSlot: number): Partial<Record<Position, number>> {
  const counts: Partial<Record<Position, number>> = {};
  allPicks.forEach(([position], i) => {
    const roundNum = Math.floor(i / leagueSize) + 1;
    const slot = pickOrderForRound(roundNum, leagueSize)[i % leagueSize];
    if (slot === targetSlot && ["QB", "RB", "WR", "TE"].includes(position)) {
      counts[position as Position] = (counts[position as Position] ?? 0) + 1;
    }
  });
  return counts;
}

const REQUIRED_STARTERS: Record<Position, keyof LeagueSettings> = { QB: "qb", RB: "rb", WR: "wr", TE: "te" };

function positionPickMultiplier(position: Position, teamCounts: Partial<Record<Position, number>>, leagueSettings: LeagueSettings): number {
  let required = leagueSettings[REQUIRED_STARTERS[position]] ?? 1;
  if (position === "QB") required += leagueSettings.sflex ?? 0;
  const count = teamCounts[position] ?? 0;
  if (count < required) return 1.0;
  const surplus = count - required + 1;
  if (position === "QB" || position === "TE") return Math.max(0.08, 0.35 ** surplus);
  return Math.max(0.35, 0.75 ** surplus);
}

const SIMULATE_POOL_SIZE = 12;
const SIMULATE_RANK_DECAY = 0.6;

function weightedChoice<T>(candidates: T[], weights: number[]): T {
  const total = weights.reduce((a, b) => a + b, 0);
  let r = Math.random() * total;
  for (let i = 0; i < candidates.length; i++) {
    r -= weights[i];
    if (r <= 0) return candidates[i];
  }
  return candidates[candidates.length - 1];
}

export function simulatePick(
  pool: AdpPoolEntry[], allPicks: Array<[string, string]>, leagueSize: number,
  leagueSettings: LeagueSettings, currentPickSlot: number,
): { name: string; position: Position; adp: number } | null {
  const takenNormalized = new Set(allPicks.map(([, name]) => normalizeName(name)));
  const candidates = pool.filter((p) => !takenNormalized.has(normalizeName(p.name))).slice(0, SIMULATE_POOL_SIZE);
  if (candidates.length === 0) return null;

  const teamCounts = getTeamPositionCounts(allPicks, leagueSize, currentPickSlot);
  const weights = candidates.map((c, rank) => SIMULATE_RANK_DECAY ** rank * positionPickMultiplier(c.position, teamCounts, leagueSettings));
  const chosen = weightedChoice(candidates, weights);
  return { name: chosen.name, position: chosen.position, adp: chosen.adp };
}

export function getFullAdpList(
  data: StaticData, season: number, adpLeagueType: LeagueType = "standard", adpLeagueFormat: LeagueFormat = "redraft",
) {
  const pool = fetchAdpPool(data, season, adpLeagueType, adpLeagueFormat);
  return pool.map((p, i) => ({ name: p.name, position: p.position, adp: p.adp, rank: i + 1 }));
}

export interface PositionalNeed { position: string; urgency: "urgent" | "need" }

export function getPositionalNeed(myPicks: MyPick[], leagueSettings: LeagueSettings, currentRound: number): PositionalNeed[] {
  const counts: Partial<Record<Position, number>> = {};
  for (const { position } of myPicks) counts[position as Position] = (counts[position as Position] ?? 0) + 1;

  const totalRounds = leagueSettings.total_rounds ?? 15;
  const roundsLeft = totalRounds - currentRound;
  const qbRequired = (leagueSettings.qb ?? 1) + (leagueSettings.sflex ?? 0);
  const needed: PositionalNeed[] = [];

  if ((counts.QB ?? 0) < qbRequired) needed.push({ position: "QB", urgency: roundsLeft <= 4 ? "urgent" : "need" });
  if ((counts.TE ?? 0) < (leagueSettings.te ?? 1)) needed.push({ position: "TE", urgency: roundsLeft <= 3 ? "urgent" : "need" });
  if ((counts.RB ?? 0) < (leagueSettings.rb ?? 2)) needed.push({ position: "RB", urgency: "need" });
  if ((counts.WR ?? 0) < (leagueSettings.wr ?? 2)) needed.push({ position: "WR", urgency: "need" });

  return needed;
}

const SFLEX_SOFT_NEED_BONUS = 6;

interface FillStatus { state: "urgent" | "need" | "filled" | "surplus"; need_bonus: number }

function getPositionFillStatus(myPicks: MyPick[], leagueSettings: LeagueSettings, currentRound: number): Record<Position, FillStatus> {
  const counts: Partial<Record<Position, number>> = {};
  for (const { position } of myPicks) counts[position as Position] = (counts[position as Position] ?? 0) + 1;

  const totalRounds = leagueSettings.total_rounds ?? 15;
  const roundsLeft = totalRounds - currentRound;
  const baseRequired: Record<Position, number> = {
    QB: leagueSettings.qb ?? 1, RB: leagueSettings.rb ?? 2, WR: leagueSettings.wr ?? 2, TE: leagueSettings.te ?? 1,
  };
  const extendedRequired: Record<Position, number> = { ...baseRequired, QB: baseRequired.QB + (leagueSettings.sflex ?? 0) };
  const urgentThresholds: Partial<Record<Position, number>> = { QB: 4, TE: 3 };

  const status = {} as Record<Position, FillStatus>;
  for (const position of Object.keys(baseRequired) as Position[]) {
    const count = counts[position] ?? 0;
    const baseDeficit = baseRequired[position] - count;
    const extendedDeficit = extendedRequired[position] - count;

    if (baseDeficit > 0) {
      const urgent = roundsLeft <= (urgentThresholds[position] ?? 0);
      status[position] = { state: urgent ? "urgent" : "need", need_bonus: urgent ? 30 : 10 };
    } else if (extendedDeficit > 0) {
      status[position] = { state: "need", need_bonus: SFLEX_SOFT_NEED_BONUS };
    } else {
      const over = count - extendedRequired[position];
      const state = over === 0 ? "filled" : "surplus";
      const needBonus = position === "QB" || position === "TE" ? -20 - 15 * over : -5 - 5 * over;
      status[position] = { state, need_bonus: needBonus };
    }
  }
  return status;
}

const VALUE_BONUS_PER_PICK = 1.5;
const ADP_QUALITY_SCALE = 1.0;
const ADP_QUALITY_HALFLIFE = 15;
const TREND_WEIGHT = 0.6;
const POSITIONAL_CLIFF_SCALE = 25;
const CLIFF_MIN_SAMPLE = 2;
const CLIFF_NEED_MULTIPLIER: Record<string, number> = { urgent: 1.0, need: 1.0, filled: 0.3, surplus: 0.0 };
const RANKED_PLAYERS_POOL_PER_POSITION = 50;

function getNextTurnPicks(currentRound: number, draftSlot: number, leagueSize: number, count = 2): number[] {
  return Array.from({ length: count }, (_, i) => globalPickNumber(currentRound + i + 1, draftSlot, leagueSize));
}

function getPositionalCliffBonus(
  playersByPosition: Record<Position, AvailablePlayer[]>,
  currentPick: number, nextTurnPick: number, nextNextTurnPick: number,
): Record<Position, number> {
  const tier1Width = Math.max(1, nextTurnPick - currentPick);
  const tier2Width = Math.max(1, nextNextTurnPick - nextTurnPick);

  const densities: Partial<Record<Position, number | null>> = {};
  for (const [position, players] of Object.entries(playersByPosition) as [Position, AvailablePlayer[]][]) {
    const tier1 = players.filter(([, adp]) => currentPick <= adp && adp < nextTurnPick).length;
    const tier2 = players.filter(([, adp]) => nextTurnPick <= adp && adp < nextNextTurnPick).length;
    if (tier1 < CLIFF_MIN_SAMPLE) {
      densities[position] = null;
      continue;
    }
    const rate1 = tier1 / tier1Width;
    const rate2 = tier2 / tier2Width;
    densities[position] = rate1 > 0 ? rate2 / rate1 : null;
  }

  const valid = Object.values(densities).filter((d): d is number => d !== null && d !== undefined);
  const bonus = {} as Record<Position, number>;
  if (valid.length < 2) {
    for (const position of Object.keys(playersByPosition) as Position[]) bonus[position] = 0;
    return bonus;
  }

  const safest = Math.max(...valid);
  for (const [position, density] of Object.entries(densities) as [Position, number | null][]) {
    if (density === null || density === undefined || safest <= 0) {
      bonus[position] = 0;
      continue;
    }
    const relativeDropoff = Math.max(0, 1 - density / safest);
    bonus[position] = POSITIONAL_CLIFF_SCALE * relativeDropoff;
  }
  return bonus;
}

export interface RankedPlayer {
  name: string; position: string; adp: number; rank: number | null;
  score: number; trend_pct: number; need: string | null; value: number; cliff_bonus: number;
}

function getRankedPlayers(
  data: StaticData, allPicks: Array<[string, string]>, myPicks: MyPick[], leagueSettings: LeagueSettings,
  currentRound: number, currentPick: number, season: number,
  positionPctLookup: Partial<Record<Position, number>>, rankLookup: Map<string, number>,
  draftSlot: number, adpLeagueType: LeagueType = "standard", limit = 10,
  adpLeagueFormat: LeagueFormat = "redraft",
): RankedPlayer[] {
  const fillStatus = getPositionFillStatus(myPicks, leagueSettings, currentRound);
  const leagueSize = leagueSettings.league_size ?? 12;
  const [nextTurnPick, nextNextTurnPick] = getNextTurnPicks(currentRound, draftSlot, leagueSize);

  const playersByPosition = {} as Record<Position, AvailablePlayer[]>;
  for (const position of ["QB", "RB", "WR", "TE"] as Position[]) {
    playersByPosition[position] = getAvailablePlayers(
      data, allPicks, position, season, rankLookup, adpLeagueType, RANKED_PLAYERS_POOL_PER_POSITION, adpLeagueFormat,
    );
  }
  const cliffBonusByPosition = getPositionalCliffBonus(playersByPosition, currentPick, nextTurnPick, nextNextTurnPick);

  const candidates: RankedPlayer[] = [];
  for (const [position, players] of Object.entries(playersByPosition) as [Position, AvailablePlayer[]][]) {
    const trendPct = positionPctLookup[position] ?? 0;
    const needInfo = fillStatus[position] ?? { state: null as unknown as FillStatus["state"], need_bonus: 0 };
    const positionCliffBonus = (cliffBonusByPosition[position] ?? 0) * (CLIFF_NEED_MULTIPLIER[needInfo.state] ?? 1.0);
    for (const [name, adp, rank] of players) {
      const reference = rank !== null ? rank : adp;
      const value = Math.max(0, currentPick - reference);
      const valueBonus = value * VALUE_BONUS_PER_PICK;
      const adpQualityBonus = (100 / (1 + adp / ADP_QUALITY_HALFLIFE)) * ADP_QUALITY_SCALE;
      const cliffBonus = reference < nextTurnPick ? positionCliffBonus : 0;
      const score = trendPct * TREND_WEIGHT + needInfo.need_bonus + valueBonus + adpQualityBonus + cliffBonus;
      candidates.push({
        name, position, adp, rank,
        score: Math.round(score * 10) / 10,
        trend_pct: trendPct,
        need: needInfo.state,
        value: Math.round(value * 10) / 10,
        cliff_bonus: Math.round(cliffBonus * 10) / 10,
      });
    }
  }
  candidates.sort((a, b) => b.score - a.score);
  return candidates.slice(0, limit);
}

export interface Recommendation {
  draft_slot: number;
  current_round: number;
  position_sequence: string[];
  trend_source: "similar_drafts" | "general_trends";
  sample_size: number | null;
  league_size: number;
  league_type: string;
  league_format: string;
  trends: TrendItem[];
  positional_needs: Array<{ position: string; urgency: string }>;
  top_available_by_position: Array<{
    position: string; top_two_pct: number; need: string | null;
    players: Array<{ name: string; adp: number; rank: number | null }>;
  }>;
  value_picks: Array<{ name: string; position: string; adp: number; rank: number | null; value: number }>;
  scarcity_alerts: ScarcityAlert[];
  ranked_players: RankedPlayer[];
}

export function buildRecommendation(
  data: StaticData, draftSlot: number, leagueSize: number, leagueType: LeagueType, tePremium: number | null,
  positionSequence: string[], currentRound: number, allPicks: Array<[string, string]>, myPicks: MyPick[],
  season: number, leagueSettings: LeagueSettings, currentPick: number,
  leagueFormat: string = "redraft",
): Recommendation {
  const totalRounds = leagueSettings.total_rounds ?? 15;
  const buckets = computeWeightedBuckets(myPicks, totalRounds);
  const similar = findSimilarDrafts(data, draftSlot, leagueSize, leagueType, tePremium, currentRound, buckets, leagueFormat);
  const [adpLeagueType, adpLeagueFormat] = resolveAdpLeagueType(data, season, leagueType, leagueFormat);
  const rankLookup = getAdpRankLookup(data, season, adpLeagueType, adpLeagueFormat);

  const positionPctLookup: Partial<Record<Position, number>> = {};
  let positionOrder: string[] = [];
  let trendSource: "similar_drafts" | "general_trends";
  let sampleSize: number | null;
  let trends: TrendItem[];

  const similarSampleSize = similar ? sumTotal(similar) : 0;

  if (similar && similarSampleSize >= MIN_SAMPLE_SIZE) {
    trendSource = "similar_drafts";
    sampleSize = similarSampleSize;
    const recommendations = calculateRecommendation(similar);
    for (const rec of recommendations) {
      if (["QB", "RB", "WR", "TE"].includes(rec.position)) {
        positionPctLookup[rec.position as Position] = rec.top_two_pct;
        positionOrder.push(rec.position);
      }
    }
    trends = positionOrder.map((p) => ({ position: p, top_two_pct: positionPctLookup[p as Position]! }));
  } else {
    trendSource = "general_trends";
    sampleSize = null;
    const rows = getGeneralRoundTrends(data, leagueSize, leagueType, currentRound, leagueFormat);
    trends = [];
    for (const { position, top_two_pct } of rows) {
      positionPctLookup[position as Position] = top_two_pct;
      positionOrder.push(position);
      trends.push({ position, top_two_pct });
    }
  }

  const needs = getPositionalNeed(myPicks, leagueSettings, currentRound);
  const needPositions = new Set(needs.map((n) => n.position));
  const urgentPositions = new Set(needs.filter((n) => n.urgency === "urgent").map((n) => n.position));

  const TOP_AVAILABLE_LIMIT = 5;
  const topAvailableByPosition = positionOrder.map((position) => {
    const needTag = urgentPositions.has(position) ? "urgent" : needPositions.has(position) ? "need" : null;
    const players = getAvailablePlayers(data, allPicks, position as Position, season, rankLookup, adpLeagueType, TOP_AVAILABLE_LIMIT, adpLeagueFormat);
    return {
      position,
      top_two_pct: positionPctLookup[position as Position] ?? 0,
      need: needTag,
      players: players.map(([name, adp, rank]) => ({ name, adp, rank })),
    };
  });

  const valuePicks: Recommendation["value_picks"] = [];
  for (const position of ["RB", "WR", "TE", "QB"] as Position[]) {
    const candidates = getAvailablePlayers(data, allPicks, position, season, rankLookup, adpLeagueType, 50, adpLeagueFormat);
    let shown = 0;
    for (const [name, adp, rank] of candidates) {
      const reference = rank !== null ? rank : adp;
      const diff = reference - currentPick;
      if (diff < -3) {
        valuePicks.push({ name, position, adp, rank, value: Math.abs(diff) });
        shown += 1;
      }
      if (shown >= 2) break;
    }
  }

  const scarcityAlerts = getScarcity(data, allPicks, currentPick, season, leagueSettings, adpLeagueType, adpLeagueFormat);
  const rankedPlayers = getRankedPlayers(
    data, allPicks, myPicks, leagueSettings, currentRound, currentPick, season,
    positionPctLookup, rankLookup, draftSlot, adpLeagueType, undefined, adpLeagueFormat,
  );

  return {
    draft_slot: draftSlot,
    current_round: currentRound,
    position_sequence: positionSequence,
    trend_source: trendSource,
    sample_size: sampleSize,
    league_size: leagueSize,
    league_type: leagueType,
    league_format: leagueFormat,
    trends,
    positional_needs: needs.map(({ position, urgency }) => ({ position, urgency })),
    top_available_by_position: topAvailableByPosition,
    value_picks: valuePicks,
    scarcity_alerts: scarcityAlerts,
    ranked_players: rankedPlayers,
  };
}

export type { AdpRow };
