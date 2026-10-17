import type { Asset, Context, SideValue, TradePlayer } from "../engine/tradeEngine";
import { makePick } from "../engine/tradeEngine";

export function fmtValue(ctx: Context, v: SideValue): string {
  if (ctx.settings.format === "redraft") return `${v.perWeekNow >= 0 ? "+" : ""}${v.perWeekNow.toFixed(1)}`;
  return Math.round(v.weighted).toLocaleString();
}

export function valueUnit(ctx: Context): string {
  return ctx.settings.format === "redraft" ? "pts/wk" : "dyn pts";
}

export function pct(x: number, signed = false): string {
  const s = `${(x * 100).toFixed(1)}%`;
  return signed && x >= 0 ? `+${s}` : s;
}

export function playerMeta(p: TradePlayer): string {
  const parts = [p.team];
  if (p.age != null) parts.push(`${Math.floor(p.age)} yrs`);
  if (p.injury) parts.push(p.injury);
  return parts.join(" · ");
}

export function assetName(ctx: Context, a: Asset): string {
  if (a.kind === "pick") return a.label;
  return ctx.data.byId.get(a.id)?.name ?? "Unknown player";
}

// Shareable trades live in the URL hash: #/trade?a=4984,p2027.1.x&b=9509
export function encodeAssets(assets: Asset[]): string {
  return assets.map((a) => (a.kind === "player" ? a.id : `p${a.season}.${a.round}.${a.slot ?? "x"}`)).join(",");
}

export function decodeAssets(raw: string | null, teams: number): Asset[] {
  if (!raw) return [];
  return raw.split(",").filter(Boolean).map((tok): Asset => {
    if (tok.startsWith("p")) {
      const [season, round, slot] = tok.slice(1).split(".");
      return makePick(Number(season), Number(round), slot === "x" ? null : Number(slot), teams);
    }
    return { kind: "player", id: tok, key: tok };
  });
}
