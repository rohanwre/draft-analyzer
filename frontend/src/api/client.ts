import type {
  CreateDraftRequest, DraftState, PickLookupResult, PlayerSearchResult, PlayerAdpItem,
} from "./types";

const API_URL = import.meta.env.VITE_API_URL as string;

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function createDraft(payload: CreateDraftRequest): Promise<DraftState> {
  return request("/drafts", { method: "POST", body: JSON.stringify(payload) });
}

export function getDraft(draftId: string): Promise<DraftState> {
  return request(`/drafts/${draftId}`);
}

export function lookupPick(draftId: string, playerName: string): Promise<PickLookupResult> {
  return request(`/drafts/${draftId}/picks/lookup`, {
    method: "POST",
    body: JSON.stringify({ player_name: playerName }),
  });
}

export function commitPick(draftId: string, name: string, position: string): Promise<DraftState> {
  return request(`/drafts/${draftId}/picks`, {
    method: "POST",
    body: JSON.stringify({ name, position }),
  });
}

export function searchPlayers(query: string, season: number, limit = 8): Promise<PlayerSearchResult[]> {
  const params = new URLSearchParams({ q: query, season: String(season), limit: String(limit) });
  return request(`/players/search?${params.toString()}`);
}

export function undoLastPick(draftId: string): Promise<DraftState> {
  return request(`/drafts/${draftId}/undo`, { method: "POST" });
}

export function simulateToUserTurn(draftId: string): Promise<DraftState> {
  return request(`/drafts/${draftId}/simulate`, { method: "POST" });
}

export function getFullAdp(season: number, leagueType: string): Promise<PlayerAdpItem[]> {
  const params = new URLSearchParams({ season: String(season), league_type: leagueType });
  return request(`/players/adp?${params.toString()}`);
}

export function swapRosterSlots(draftId: string, nameA: string, nameB: string): Promise<DraftState> {
  return request(`/drafts/${draftId}/roster/swap`, {
    method: "POST",
    body: JSON.stringify({ name_a: nameA, name_b: nameB }),
  });
}
