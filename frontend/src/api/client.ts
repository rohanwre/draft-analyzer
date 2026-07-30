// Runs entirely client-side against pre-baked static data (see scripts/export_static_data.py
// and src/engine/) — no backend, no network calls beyond fetching the static JSON bundled
// with this same deployment. Kept as drop-in-compatible function signatures so components
// didn't need to change when this stopped calling a FastAPI backend.
import type {
  CreateDraftRequest, DraftState, PickLookupResult, PlayerSearchResult, PlayerAdpItem,
} from "./types";
import { loadStaticData } from "../engine/staticData";
import { getFullAdpList, resolveAdpLeagueType, searchPlayers as searchPlayersPure } from "../engine/advisor";
import {
  createDraft as createDraftEngine, getDraft as getDraftEngine, lookupPick as lookupPickEngine,
  commitPick as commitPickEngine, undoLastPick as undoLastPickEngine,
  simulateToUserTurn as simulateToUserTurnEngine, swapRosterSlots as swapRosterSlotsEngine,
} from "../engine/draftEngine";

export function createDraft(payload: CreateDraftRequest): Promise<DraftState> {
  return createDraftEngine(payload) as Promise<DraftState>;
}

export function getDraft(draftId: string): Promise<DraftState> {
  return getDraftEngine(draftId) as Promise<DraftState>;
}

export function lookupPick(draftId: string, playerName: string): Promise<PickLookupResult> {
  return lookupPickEngine(draftId, playerName);
}

export function commitPick(draftId: string, name: string, position: string): Promise<DraftState> {
  return commitPickEngine(draftId, name, position) as Promise<DraftState>;
}

export async function searchPlayers(query: string, season: number, limit = 8): Promise<PlayerSearchResult[]> {
  const data = await loadStaticData();
  return searchPlayersPure(data, query, season, limit);
}

export function undoLastPick(draftId: string): Promise<DraftState> {
  return undoLastPickEngine(draftId) as Promise<DraftState>;
}

export function simulateToUserTurn(draftId: string): Promise<DraftState> {
  return simulateToUserTurnEngine(draftId) as Promise<DraftState>;
}

export async function getFullAdp(season: number, leagueType: string): Promise<PlayerAdpItem[]> {
  const data = await loadStaticData();
  const adpLeagueType = resolveAdpLeagueType(data, season, leagueType);
  return getFullAdpList(data, season, adpLeagueType);
}

export function swapRosterSlots(draftId: string, nameA: string, nameB: string): Promise<DraftState> {
  return swapRosterSlotsEngine(draftId, nameA, nameB) as Promise<DraftState>;
}
