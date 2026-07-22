export type RosterSlotKey = "QB" | "RB" | "WR" | "TE" | "FLEX" | "SFLEX" | "BENCH";

export const ROSTER_SLOT_ORDER: RosterSlotKey[] = ["QB", "RB", "WR", "TE", "FLEX", "SFLEX", "BENCH"];

export interface SlottedPick {
  position: string;
  name: string;
}

export interface RosterSlotsResult {
  slots: Record<RosterSlotKey, SlottedPick[]>;
  slotCounts: Record<RosterSlotKey, number>;
}

/**
 * Assigns a team's picks (in draft order) to roster slots: dedicated starters first
 * (QB/RB/WR/TE up to each requirement), then FLEX (RB/WR-eligible only — this league's
 * FLEX doesn't take TE), then SFLEX (any position), then bench for the rest.
 */
export function assignRosterSlots(
  picks: SlottedPick[],
  leagueSettings: Record<string, number | string>,
  totalRounds: number,
): RosterSlotsResult {
  const required: Record<"QB" | "RB" | "WR" | "TE", number> = {
    QB: Number(leagueSettings.qb ?? 1),
    RB: Number(leagueSettings.rb ?? 2),
    WR: Number(leagueSettings.wr ?? 2),
    TE: Number(leagueSettings.te ?? 1),
  };
  const flexSlots = Number(leagueSettings.flex ?? 0);
  const sflexSlots = Number(leagueSettings.sflex ?? 0);
  const benchSlots = Math.max(
    0,
    totalRounds - required.QB - required.RB - required.WR - required.TE - flexSlots - sflexSlots,
  );

  const slots: Record<RosterSlotKey, SlottedPick[]> = {
    QB: [], RB: [], WR: [], TE: [], FLEX: [], SFLEX: [], BENCH: [],
  };

  for (const pick of picks) {
    const pos = pick.position;
    if ((pos === "QB" || pos === "RB" || pos === "WR" || pos === "TE") && slots[pos].length < required[pos]) {
      slots[pos].push(pick);
    } else if ((pos === "RB" || pos === "WR") && slots.FLEX.length < flexSlots) {
      slots.FLEX.push(pick);
    } else if (slots.SFLEX.length < sflexSlots) {
      slots.SFLEX.push(pick);
    } else {
      slots.BENCH.push(pick);
    }
  }

  return {
    slots,
    slotCounts: {
      QB: required.QB, RB: required.RB, WR: required.WR, TE: required.TE,
      FLEX: flexSlots, SFLEX: sflexSlots, BENCH: benchSlots,
    },
  };
}

/**
 * Applies manual swaps (e.g. move a bench QB into SFLEX and bump whoever was there to
 * the bench) on top of the auto-assigned result from assignRosterSlots. Swaps are
 * applied in order, so later swaps see the result of earlier ones. A swap referencing a
 * player no longer on the roster (e.g. undone since the swap was made) is skipped rather
 * than throwing — it's just stale, not an error.
 */
export function applySlotSwaps(result: RosterSlotsResult, swaps: [string, string][]): RosterSlotsResult {
  const location = new Map<string, { slot: RosterSlotKey; index: number }>();
  for (const slot of ROSTER_SLOT_ORDER) {
    result.slots[slot].forEach((pick, index) => location.set(pick.name, { slot, index }));
  }

  const newSlots: Record<RosterSlotKey, SlottedPick[]> = {
    QB: [...result.slots.QB], RB: [...result.slots.RB], WR: [...result.slots.WR],
    TE: [...result.slots.TE], FLEX: [...result.slots.FLEX], SFLEX: [...result.slots.SFLEX],
    BENCH: [...result.slots.BENCH],
  };

  for (const [nameA, nameB] of swaps) {
    const locA = location.get(nameA);
    const locB = location.get(nameB);
    if (!locA || !locB) continue;

    const pickA = newSlots[locA.slot][locA.index];
    const pickB = newSlots[locB.slot][locB.index];
    newSlots[locA.slot][locA.index] = pickB;
    newSlots[locB.slot][locB.index] = pickA;

    location.set(nameA, locB);
    location.set(nameB, locA);
  }

  return { slots: newSlots, slotCounts: result.slotCounts };
}
