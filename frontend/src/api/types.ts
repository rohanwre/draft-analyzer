export interface CreateDraftRequest {
  league_size: number;
  draft_slot: number;
  season: number;
  total_rounds: number;
  qb: number;
  rb: number;
  wr: number;
  te: number;
  flex: number;
  sflex: number;
  te_premium: boolean;
}

export interface Player {
  name: string;
  adp: number;
  rank: number | null;
}

export interface TrendItem {
  position: string;
  top_two_pct: number;
}

export interface PositionalNeedItem {
  position: string;
  urgency: string;
}

export interface AvailablePositionGroup {
  position: string;
  top_two_pct: number;
  need: string | null;
  players: Player[];
}

export interface ValuePickItem {
  name: string;
  position: string;
  adp: number;
  rank: number | null;
  value: number;
}

export interface RankedPlayerItem {
  name: string;
  position: string;
  adp: number;
  rank: number | null;
  score: number;
  trend_pct: number;
  need: string | null;
  value: number;
}

export interface ScarcityAlertItem {
  position: string;
  taken: number;
  expected: number;
  ratio: number;
  demand_pct: number;
  threshold: number;
}

export interface Recommendation {
  trend_source: string;
  sample_size: number | null;
  league_size: number;
  league_type: string;
  trends: TrendItem[];
  positional_needs: PositionalNeedItem[];
  top_available_by_position: AvailablePositionGroup[];
  value_picks: ValuePickItem[];
  scarcity_alerts: ScarcityAlertItem[];
  ranked_players: RankedPlayerItem[];
}

export interface PickRecord {
  round: number | null;
  pick_slot: number | null;
  position: string;
  name: string;
  is_user_pick: boolean;
}

export interface DraftState {
  draft_id: string;
  league_size: number;
  draft_slot: number;
  season: number;
  total_rounds: number;
  league_type: string;
  league_settings: Record<string, number | string>;
  all_picks: PickRecord[];
  my_picks: PickRecord[];
  draft_complete: boolean;
  current_round: number | null;
  current_pick_slot: number | null;
  current_global_pick: number | null;
  is_user_turn: boolean;
  recommendation: Recommendation | null;
}

export type PickLookupStatus = "invalid" | "already_taken" | "manual" | "matched" | "not_found";

export interface PickLookupResult {
  status: PickLookupStatus;
  name?: string;
  position?: string;
  message?: string;
  query?: string;
}

export interface PlayerSearchResult {
  name: string;
  position: string;
}

export interface PlayerAdpItem {
  name: string;
  position: string;
  adp: number;
  rank: number;
}
