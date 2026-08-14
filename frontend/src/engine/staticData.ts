// Loads the pre-baked JSON snapshots of adp / draft_trend_stats / round1_trend_stats
// (see scripts/export_static_data.py) and indexes them for O(1) lookups, replacing the
// live MySQL queries in advisor.py. Fetched once per page load and cached.

export type Position = "QB" | "RB" | "WR" | "TE";
export type LeagueType = "standard" | "qb_premium";
export type LeagueFormat = "redraft" | "keeper" | "dynasty";
export type Bucket = "NONE" | "LIGHT" | "MODERATE" | "HEAVY";

export interface AdpRow {
  name: string;
  position: Position;
  adp: number;
  season: number;
  leagueType: LeagueType;
  tiebreakAdp: number | null;
}

export interface PositionStats {
  total: number;
  success: number;
}

interface Round1Json {
  leagueTypes: string[];
  leagueFormats: string[];
  positions: string[];
  rows: number[][];
}

interface DraftTrendJson {
  leagueTypes: string[];
  leagueFormats: string[];
  buckets: string[];
  positions: string[];
  rows: number[][];
}

export interface StaticData {
  adp: AdpRow[];
  // key: `${leagueFormat}|${leagueSize}|${leagueType}|${tePremium}|${round}|${qbBucket}|${rbBucket}|${wrBucket}|${teBucket}`
  trendFull: Map<string, Partial<Record<Position, PositionStats>>>;
  // key: `${leagueFormat}|${leagueSize}|${leagueType}|${round}|${qbBucket}|${rbBucket}|${wrBucket}|${teBucket}` (te_premium ignored)
  trendNoTep: Map<string, Partial<Record<Position, PositionStats>>>;
  // key: `${leagueFormat}|${leagueSize}|${leagueType}|${round}` (te_premium and buckets ignored)
  trendGeneral: Map<string, Partial<Record<Position, PositionStats>>>;
  // key: `${leagueFormat}|${draftSlot}|${leagueSize}|${leagueType}|${tePremium}`
  round1Full: Map<string, Partial<Record<Position, PositionStats>>>;
  // key: `${leagueFormat}|${draftSlot}|${leagueSize}|${leagueType}` (te_premium ignored)
  round1NoTep: Map<string, Partial<Record<Position, PositionStats>>>;
}

function accumulate(
  map: Map<string, Partial<Record<Position, PositionStats>>>,
  key: string,
  position: Position,
  total: number,
  success: number,
) {
  let entry = map.get(key);
  if (!entry) {
    entry = {};
    map.set(key, entry);
  }
  const stats = entry[position] ?? { total: 0, success: 0 };
  stats.total += total;
  stats.success += success;
  entry[position] = stats;
}

let loadPromise: Promise<StaticData> | null = null;

async function fetchJson<T>(name: string): Promise<T> {
  const res = await fetch(`${import.meta.env.BASE_URL}data/${name}`);
  if (!res.ok) throw new Error(`Failed to load ${name}: ${res.status}`);
  return res.json() as Promise<T>;
}

export interface RawStaticData {
  adp: Array<{
    name: string; position: Position; adp: number; season: number;
    leagueType: LeagueType; tiebreakAdp: number | null;
  }>;
  round1: Round1Json;
  trend: DraftTrendJson;
}

// Pure indexing step, split out from loadStaticData so it can be exercised outside a
// browser/Vite environment (see scripts/testEngine.ts) without needing fetch or
// import.meta.env.
export function buildStaticData({ adp, round1, trend }: RawStaticData): StaticData {
  const round1Full: StaticData["round1Full"] = new Map();
  const round1NoTep: StaticData["round1NoTep"] = new Map();
  for (const [slot, size, ltIdx, fmtIdx, tep, posIdx, total, success] of round1.rows) {
    const leagueType = round1.leagueTypes[ltIdx];
    const leagueFormat = round1.leagueFormats[fmtIdx];
    const position = round1.positions[posIdx] as Position;
    accumulate(round1Full, `${leagueFormat}|${slot}|${leagueType}|${size}|${tep}`, position, total, success);
    accumulate(round1NoTep, `${leagueFormat}|${slot}|${leagueType}|${size}`, position, total, success);
  }

  const trendFull: StaticData["trendFull"] = new Map();
  const trendNoTep: StaticData["trendNoTep"] = new Map();
  const trendGeneral: StaticData["trendGeneral"] = new Map();
  for (const row of trend.rows) {
    const [size, ltIdx, fmtIdx, tep, round, qbB, rbB, wrB, teB, posIdx, total, success] = row;
    const leagueType = trend.leagueTypes[ltIdx];
    const leagueFormat = trend.leagueFormats[fmtIdx];
    const position = trend.positions[posIdx] as Position;
    const bucketKey = `${trend.buckets[qbB]}|${trend.buckets[rbB]}|${trend.buckets[wrB]}|${trend.buckets[teB]}`;
    accumulate(trendFull, `${leagueFormat}|${size}|${leagueType}|${tep}|${round}|${bucketKey}`, position, total, success);
    accumulate(trendNoTep, `${leagueFormat}|${size}|${leagueType}|${round}|${bucketKey}`, position, total, success);
    accumulate(trendGeneral, `${leagueFormat}|${size}|${leagueType}|${round}`, position, total, success);
  }

  return { adp, trendFull, trendNoTep, trendGeneral, round1Full, round1NoTep };
}

export function loadStaticData(): Promise<StaticData> {
  if (!loadPromise) {
    loadPromise = (async () => {
      const [adp, round1, trend] = await Promise.all([
        fetchJson<RawStaticData["adp"]>("adp.json"),
        fetchJson<Round1Json>("round1_trend_stats.json"),
        fetchJson<DraftTrendJson>("draft_trend_stats.json"),
      ]);
      return buildStaticData({ adp, round1, trend });
    })();
  }
  return loadPromise;
}
