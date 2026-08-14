// Client-side port of api/draft_state.py + api/routers/drafts.py. Sessions live in an
// in-memory Map (this is a single-tab client-side app, so there's no multi-request
// server to persist across — the session only needs to survive for the page's lifetime).
import { loadStaticData } from "./staticData";
import type { LeagueType, Position } from "./staticData";
import {
  buildRecommendation, deriveLeagueType, fetchAdpPool,
  pickOrderForRound, recordPick as recordPickPure, resolveAdpLeagueType, simulatePick,
} from "./advisor";
import type { MyPick, Recommendation } from "./advisor";

interface Session {
  draftId: string;
  leagueSize: number;
  draftSlot: number;
  season: number;
  totalRounds: number;
  leagueType: LeagueType;
  leagueFormat: string;
  tePremium: number;
  leagueSettings: {
    qb: number; rb: number; wr: number; te: number; flex: number; sflex: number;
    te_premium: number; league_size: number; total_rounds: number; [key: string]: number;
  };
  allPicks: Array<[string, string]>; // [position, name]
  myPicks: MyPick[];
  slotSwaps: Array<[string, string]>;
}

const sessions = new Map<string, Session>();

function genId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `draft-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export interface CreateDraftPayload {
  league_size: number;
  draft_slot: number;
  season: number;
  total_rounds: number;
  qb: number; rb: number; wr: number; te: number; flex: number; sflex: number;
  te_premium: boolean;
  // "redraft" (default) or "dynasty" - see engine/advisor.ts. Startup-draft dynasty
  // trends only (no live rookie-draft-only support yet, see build_trend_stats.py).
  league_format?: string;
}

function computeTurnState(allPicks: unknown[], leagueSize: number, totalRounds: number, draftSlot: number) {
  const totalPicksMade = allPicks.length;
  const totalPicksInDraft = leagueSize * totalRounds;

  if (totalPicksMade >= totalPicksInDraft) {
    return { draft_complete: true, current_round: null, current_pick_slot: null, current_global_pick: null, is_user_turn: false };
  }

  const currentRound = Math.floor(totalPicksMade / leagueSize) + 1;
  const positionWithinRound = totalPicksMade % leagueSize;
  const order = pickOrderForRound(currentRound, leagueSize);
  const currentPickSlot = order[positionWithinRound];

  return {
    draft_complete: false,
    current_round: currentRound,
    current_pick_slot: currentPickSlot,
    current_global_pick: totalPicksMade + 1,
    is_user_turn: currentPickSlot === draftSlot,
  };
}

export interface DraftState {
  draft_id: string;
  league_size: number;
  draft_slot: number;
  season: number;
  total_rounds: number;
  league_type: string;
  league_format: string;
  league_settings: Record<string, number | string>;
  all_picks: Array<{ round: number | null; pick_slot: number | null; position: string; name: string; is_user_pick: boolean }>;
  my_picks: Array<{ round: number; pick_slot: number; position: string; name: string; is_user_pick: true }>;
  slot_swaps: [string, string][];
  draft_complete: boolean;
  current_round: number | null;
  current_pick_slot: number | null;
  current_global_pick: number | null;
  is_user_turn: boolean;
  recommendation: Recommendation | null;
}

async function serializeState(session: Session): Promise<DraftState> {
  const data = await loadStaticData();
  const turn = computeTurnState(session.allPicks, session.leagueSize, session.totalRounds, session.draftSlot);

  const myPicksSet = new Set(session.myPicks.map((p) => `${p.position}|${p.name}`));
  const allPicksOut = session.allPicks.map(([position, name], idx) => {
    const roundNum = Math.floor(idx / session.leagueSize) + 1;
    const positionWithinRound = idx % session.leagueSize;
    const order = pickOrderForRound(roundNum, session.leagueSize);
    return {
      round: roundNum,
      pick_slot: order[positionWithinRound],
      position,
      name,
      is_user_pick: myPicksSet.has(`${position}|${name}`),
    };
  });
  const myPicksOut = session.myPicks.map((p) => ({
    round: p.round, pick_slot: session.draftSlot, position: p.position, name: p.name, is_user_pick: true as const,
  }));

  let recommendation: Recommendation | null = null;
  if (turn.is_user_turn && !turn.draft_complete && turn.current_round !== null && turn.current_global_pick !== null) {
    const positionSequence = session.myPicks.map((p) => p.position);
    recommendation = buildRecommendation(
      data, session.draftSlot, session.leagueSize, session.leagueType, session.tePremium,
      positionSequence, turn.current_round, session.allPicks, session.myPicks, session.season,
      session.leagueSettings, turn.current_global_pick, session.leagueFormat,
    );
  }

  return {
    draft_id: session.draftId,
    league_size: session.leagueSize,
    draft_slot: session.draftSlot,
    season: session.season,
    total_rounds: session.totalRounds,
    league_type: session.leagueType,
    league_format: session.leagueFormat,
    league_settings: session.leagueSettings,
    all_picks: allPicksOut,
    my_picks: myPicksOut,
    slot_swaps: session.slotSwaps,
    recommendation,
    ...turn,
  };
}

export async function createDraft(payload: CreateDraftPayload): Promise<DraftState> {
  const draftId = genId();
  const leagueType = deriveLeagueType(payload.qb, payload.sflex);
  const tePremium = payload.te_premium ? 1 : 0;
  const session: Session = {
    draftId,
    leagueSize: payload.league_size,
    draftSlot: payload.draft_slot,
    season: payload.season,
    totalRounds: payload.total_rounds,
    leagueType,
    leagueFormat: payload.league_format === "dynasty" ? "dynasty" : "redraft",
    tePremium,
    leagueSettings: {
      qb: payload.qb, rb: payload.rb, wr: payload.wr, te: payload.te,
      flex: payload.flex, sflex: payload.sflex, te_premium: tePremium,
      league_size: payload.league_size, total_rounds: payload.total_rounds,
    },
    allPicks: [],
    myPicks: [],
    slotSwaps: [],
  };
  sessions.set(draftId, session);
  return serializeState(session);
}

function getSessionOrThrow(draftId: string): Session {
  const session = sessions.get(draftId);
  if (!session) throw new Error("Draft not found");
  return session;
}

export async function getDraft(draftId: string): Promise<DraftState> {
  return serializeState(getSessionOrThrow(draftId));
}

export interface PickLookupResult {
  status: "invalid" | "already_taken" | "manual" | "matched" | "not_found";
  name?: string; position?: string; message?: string; query?: string;
}

export async function lookupPick(draftId: string, playerName: string): Promise<PickLookupResult> {
  const session = getSessionOrThrow(draftId);
  const data = await loadStaticData();
  return recordPickPure(data, session.allPicks, session.season, playerName);
}

export async function commitPick(draftId: string, name: string, position: string): Promise<DraftState> {
  const session = getSessionOrThrow(draftId);
  const data = await loadStaticData();

  const turn = computeTurnState(session.allPicks, session.leagueSize, session.totalRounds, session.draftSlot);
  if (turn.draft_complete) throw new Error("Draft is already complete");

  const result = recordPickPure(data, session.allPicks, session.season, name, position);
  if (result.status === "invalid") throw new Error(result.message);
  if (result.status === "already_taken") throw new Error(result.message);

  session.allPicks.push([result.position!, result.name!]);
  if (turn.is_user_turn && turn.current_round !== null) {
    session.myPicks.push({ round: turn.current_round, position: result.position!, name: result.name! });
  }

  return serializeState(session);
}

export async function undoLastPick(draftId: string): Promise<DraftState> {
  const session = getSessionOrThrow(draftId);
  if (session.allPicks.length === 0) throw new Error("No picks to undo");

  const popped = session.allPicks.pop()!;
  const last = session.myPicks[session.myPicks.length - 1];
  if (last && last.position === popped[0] && last.name === popped[1]) {
    session.myPicks.pop();
  }

  return serializeState(session);
}

export async function simulateToUserTurn(draftId: string): Promise<DraftState> {
  const session = getSessionOrThrow(draftId);
  const data = await loadStaticData();

  const adpLeagueType = resolveAdpLeagueType(data, session.season, session.leagueType);
  const pool = fetchAdpPool(data, session.season, adpLeagueType);

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const turn = computeTurnState(session.allPicks, session.leagueSize, session.totalRounds, session.draftSlot);
    if (turn.draft_complete || turn.is_user_turn) break;

    const pick = simulatePick(pool, session.allPicks, session.leagueSize, session.leagueSettings, turn.current_pick_slot!);
    if (!pick) break;

    session.allPicks.push([pick.position, pick.name]);
  }

  return serializeState(session);
}

export async function swapRosterSlots(draftId: string, nameA: string, nameB: string): Promise<DraftState> {
  const session = getSessionOrThrow(draftId);
  const myNames = new Set(session.myPicks.map((p) => p.name));
  if (!myNames.has(nameA) || !myNames.has(nameB)) {
    throw new Error("Can only swap slots between players on your own roster");
  }
  if (nameA === nameB) throw new Error("Can't swap a player with themselves");

  session.slotSwaps.push([nameA, nameB]);
  return serializeState(session);
}

export type { Position };
