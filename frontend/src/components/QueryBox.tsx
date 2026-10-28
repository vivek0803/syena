import { useMemo, useRef, useState } from "react";
import { useApi } from "../lib/hooks";

/** screener.in-style query box: type conditions, get field suggestions for the word you're typing. */
export default function QueryBox({ value, onChange, onRun, placeholder }: { value: string; onChange: (v: string) => void; onRun?: () => void; placeholder?: string }) {
  const fields = useApi<any[]>("/api/screener/fields");
  const ref = useRef<HTMLTextAreaElement>(null);
  const [focus, setFocus] = useState(false);
  const [hi, setHi] = useState(0);
  const word = (() => {
    const el = ref.current;
    const pos = el?.selectionStart ?? value.length;
    const m = value.slice(0, pos).match(/([a-z_][a-z0-9_ /]*)$/i);
    const w = (m?.[1] ?? "").replace(/^(and|or|not)\s+/i, "").trimStart();
    return /\b(and|or|not)$/i.test(w) ? "" : w;
  })();
  const sugg = useMemo(() => {
    const w = word.toLowerCase().trim();
    if (w.length < 2 || !fields.data) return [];
    return fields.data.filter((f: any) => f.key.includes(w.replace(/ /g, "_")) || f.label.toLowerCase().includes(w) || f.aliases.some((a: string) => a.includes(w))).slice(0, 8);
  }, [word, fields.data]);
  const pick = (f: any) => {
    const el = ref.current;
    const pos = el?.selectionStart ?? value.length;
    const before = value.slice(0, pos).replace(new RegExp(`${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`, "i"), "");
    const next = `${before}${f.key} `;
    onChange(next + value.slice(pos));
    setTimeout(() => { el?.focus(); el?.setSelectionRange(next.length, next.length); }, 0);
  };
  return (
    <div className="relative">
      <textarea ref={ref} className="input min-h-[96px] font-mono text-[14px] leading-relaxed" value={value} placeholder={placeholder}
        onChange={(e) => { onChange(e.target.value); setHi(0); }} onFocus={() => setFocus(true)} onBlur={() => setTimeout(() => setFocus(false), 150)}
        onKeyDown={(e) => {
          if (sugg.length && focus) {
            if (e.key === "ArrowDown") { e.preventDefault(); setHi((hi + 1) % sugg.length); return; }
            if (e.key === "ArrowUp") { e.preventDefault(); setHi((hi - 1 + sugg.length) % sugg.length); return; }
            if (e.key === "Tab" || (e.key === "Enter" && !e.ctrlKey)) { e.preventDefault(); pick(sugg[hi]); return; }
          }
          if (e.key === "Enter" && e.ctrlKey && onRun) { e.preventDefault(); onRun(); }
        }} aria-label="Query" />
      {focus && sugg.length > 0 && (
        <ul className="absolute left-0 right-0 top-full z-20 mt-1 max-h-72 overflow-auto rounded-lg border border-dusk-line bg-dusk-raised py-1 shadow-xl">
          {sugg.map((f: any, i: number) => (
            <li key={f.key}><button type="button" onMouseDown={(e) => { e.preventDefault(); pick(f); }}
              className={`flex w-full items-baseline justify-between gap-3 px-3 py-1.5 text-left text-sm ${i === hi ? "bg-saffron/15 text-parch" : "text-parch-dim hover:bg-dusk"}`}>
              <span>{f.label}</span><span className="font-mono text-[12px] text-mute">{f.key}</span></button></li>))}
        </ul>)}
    </div>);
}
