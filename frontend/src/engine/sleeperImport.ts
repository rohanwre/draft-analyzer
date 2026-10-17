// Pulls a real league straight from Sleeper's public API (CORS-open, no key) so the
// trade can be judged against both teams' actual rosters instead of a median team.

import { makePick } from "./tradeEngine";
import type { PickAsset, RosterTeam, Scoring, Settings, Slots } from "./tradeEngine";

const API = "https://api.sleeper.app/v1";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) throw new Error(`Sleeper returned ${res.status} for ${path}`);
  const body = await res.json();
  if (body == null) throw new Error("Not found on Sleeper");
  return body as T;
}

export interface LeagueSummary {
  id: string;
  name: string;
  teams: number;
  dynasty: boolean;
}

interface SleeperLeague {
  league_id: string;
  name: string;
  season: string;
  total_rosters: number;
  roster_positions: string[];
  scoring_settings: Record<string, number>;
  settings: { type?: number; draft_rounds?: number };
}

export async function leaguesForUser(username: string, season: number): Promise<LeagueSummary[]> {
  const user = await get<{ user_id: string }>(`/user/${encodeURIComponent(username.trim())}`);
  const leagues = await get<SleeperLeague[]>(`/user/${user.user_id}/leagues/nfl/${season}`);
  return leagues.map((l) => ({ id: l.league_id, name: l.name, teams: l.total_rosters, dynasty: l.settings.type === 2 }));
}

export interface ImportedLeague {
  name: string;
  settings: Omit<Settings, "strategy">;
  teams: RosterTeam[];
}

function slotsFrom(positions: string[]): Slots {
  const slots: Slots = { QB: 0, RB: 0, WR: 0, TE: 0, FLEX: 0, WRRB_FLEX: 0, REC_FLEX: 0, SUPER_FLEX: 0 };
  for (const p of positions) if (p in slots) slots[p as keyof Slots]++;
  return slots;
}

function scoringFrom(rec: number | undefined): Scoring {
  if (!rec) return "standard";
  return rec < 0.75 ? "half_ppr" : "ppr";
}

export async function importLeague(leagueId: string, season: number): Promise<ImportedLeague> {
  const id = leagueId.trim();
  const [league, rosters, users, traded] = await Promise.all([
    get<SleeperLeague>(`/league/${id}`),
    get<Array<{ roster_id: number; owner_id: string | null; players: string[] | null; settings: { wins?: number; losses?: number } }>>(`/league/${id}/rosters`),
    get<Array<{ user_id: string; display_name: string; metadata?: { team_name?: string } }>>(`/league/${id}/users`),
    get<Array<{ season: string; round: number; roster_id: number; owner_id: number }>>(`/league/${id}/traded_picks`),
  ]);

  const userById = new Map(users.map((u) => [u.user_id, u]));
  const nameOf = (rosterId: number) => {
    const r = rosters.find((x) => x.roster_id === rosterId);
    const u = r?.owner_id ? userById.get(r.owner_id) : undefined;
    return u?.metadata?.team_name || u?.display_name || `Team ${rosterId}`;
  };

  const dynasty = league.settings.type === 2;
  const n = league.total_rosters;
  const rounds = league.settings.draft_rounds || 4;

  // Every team owns its own future rookie picks unless traded_picks says otherwise.
  // The current draft (this season's) is done mid-season, so start at next year.
  const picksByRoster = new Map<number, PickAsset[]>();
  if (dynasty) {
    for (let yr = season + 1; yr <= season + 3; yr++) {
      for (let rd = 1; rd <= Math.min(rounds, 5); rd++) {
        for (const r of rosters) {
          const t = traded.find((x) => Number(x.season) === yr && x.round === rd && x.roster_id === r.roster_id);
          const holder = t ? t.owner_id : r.roster_id;
          const via = holder !== r.roster_id ? nameOf(r.roster_id) : undefined;
          const base = makePick(yr, rd, null, n, via);
          // Keyed by original owner so two teams' own 2027 1sts never collide.
          const pick: PickAsset = { ...base, key: `${base.key}|r${r.roster_id}`, originalRoster: r.roster_id };
          const list = picksByRoster.get(holder) ?? [];
          list.push(pick);
          picksByRoster.set(holder, list);
        }
      }
    }
  }

  const teams: RosterTeam[] = rosters.map((r) => ({
    rosterId: r.roster_id,
    name: nameOf(r.roster_id),
    owner: (r.owner_id && userById.get(r.owner_id)?.display_name) || "",
    playerIds: r.players ?? [],
    picks: picksByRoster.get(r.roster_id) ?? [],
    wins: r.settings.wins ?? 0,
    losses: r.settings.losses ?? 0,
  }));

  return {
    name: league.name,
    settings: {
      format: dynasty ? "dynasty" : "redraft",
      teams: n,
      scoring: scoringFrom(league.scoring_settings.rec),
      tePremium: league.scoring_settings.bonus_rec_te ?? 0,
      slots: slotsFrom(league.roster_positions),
    },
    teams,
  };
}
