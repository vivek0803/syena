import { useState } from "react";
import { useApi } from "../lib/hooks";

/** Browse every candlestick pattern the rule language knows; clicking one adds it to the rule text. */
export default function PatternPicker({ onPick }: { onPick: (phrase: string) => void }) {
  const [open, setOpen] = useState(false);
  const [dir, setDir] = useState<"bullish" | "bearish" | "neutral">("bullish");
  const cat = useApi<any>("/api/algos/patterns", { enabled: open } as any);
  const rows = (cat.data?.patterns ?? []).filter((p: any) => p.direction === dir);
  const groups = ["reversal", "continuation", "group", "indecision", "gap"];
  return (
    <div className="rounded-lg border border-dusk-line bg-dusk/40">
      <button type="button" className="flex w-full items-center justify-between px-3 py-2 text-sm text-parch-dim hover:text-parch" onClick={() => setOpen(!open)}>
        <span>🕯️ Candlestick patterns ({cat.data?.patterns?.length ?? "50+"}) — click one to add it to your rule</span>
        <span className="text-mute">{open ? "hide" : "show"}</span>
      </button>
      {open && (
        <div className="space-y-3 border-t border-dusk-line px-3 py-3">
          <div className="flex gap-2">
            {(["bullish", "bearish", "neutral"] as const).map((d) => (
              <button type="button" key={d} onClick={() => setDir(d)}
                className={`rounded-md px-2.5 py-1 text-[13px] ${dir === d ? (d === "bullish" ? "bg-gain-soft text-gain" : d === "bearish" ? "bg-loss-soft text-loss" : "bg-dusk-raised text-parch") : "text-mute hover:text-parch"}`}>{d}</button>))}
          </div>
          {groups.map((g) => {
            const list = rows.filter((p: any) => p.kind === g);
            if (!list.length) return null;
            return (
              <div key={g}>
                <div className="mb-1 text-[11px] uppercase tracking-wide text-mute">{g === "group" ? "groups" : g}</div>
                <div className="grid gap-1.5 sm:grid-cols-2">{list.map((p: any) => (
                  <button type="button" key={p.name} title={p.meaning} onClick={() => onPick(p.phrase)}
                    className="rounded-md bg-dusk px-2.5 py-1.5 text-left hover:bg-dusk-raised">
                    <div className="text-sm text-parch">{p.label}</div>
                    <div className="text-[12px] leading-snug text-mute">{p.meaning}</div>
                  </button>))}</div>
              </div>);
          })}
          {cat.data?.modifiers && (
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wide text-mute">add context</div>
              <ul className="space-y-1 text-[13px]">{cat.data.modifiers.map((m: any) => (
                <li key={m.phrase}><button type="button" className="text-saffron hover:underline" onClick={() => onPick(m.phrase.replace(/^…, /, ", "))}>{m.phrase}</button>
                  <span className="text-mute"> — {m.meaning}</span></li>))}</ul>
            </div>)}
        </div>)}
    </div>
  );
}
