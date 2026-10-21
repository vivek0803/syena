import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";

/** Pick an algo's stocks: search by symbol/name, add a whole index, market-cap group, sector or screen, or paste a list. */
export default function StockPicker({ value, onChange, disabled }: { value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  const cat = useApi<any>("/api/screener/lists");
  const max: number = cat.data?.max ?? Infinity;  // no limit unless the server sets one
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<any[]>([]);
  const [hi, setHi] = useState(0);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState<{ text: string; bad?: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = q.trim();
    if (!t || /[,\s]/.test(t)) { setHits([]); return; }
    const ctl = new AbortController();
    const id = setTimeout(() => api<any[]>(`/api/screener/search?q=${encodeURIComponent(t)}`, { signal: ctl.signal })
      .then((r) => { setHits(r); setHi(0); }).catch(() => {}), 150);
    return () => { clearTimeout(id); ctl.abort(); };
  }, [q]);

  useEffect(() => {
    const close = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const add = (syms: string[], label?: string) => {
    const have = new Set(value);
    const fresh = syms.map((s) => s.trim().toUpperCase()).filter((s) => s && !have.has(s) && (have.add(s), true));
    const room = max === Infinity ? fresh.length : Math.max(0, max - value.length);
    const taken = fresh.slice(0, room);
    onChange([...value, ...taken]);
    if (fresh.length > room) setNote({ text: `Added ${taken.length} of ${fresh.length}${label ? ` from ${label}` : ""}: an algo can watch up to ${max} stocks (biggest companies were added first).`, bad: true });
    else if (label) setNote({ text: `Added ${taken.length} stocks from ${label}${syms.length > taken.length ? ` (${syms.length - taken.length} were already in the list)` : ""}.` });
    else setNote(null);
  };

  const commit = (sym?: string) => {
    const t = q.trim();
    if (sym) add([sym]);
    else if (/[,\s]/.test(t)) add(t.split(/[,\s]+/));  // pasted list
    else if (hits[hi]) add([hits[hi].symbol]);
    else if (t) add([t]);
    setQ(""); setHits([]); setOpen(false);
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || (e.key === "," && q.trim())) { e.preventDefault(); commit(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(h + 1, hits.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)); }
    else if (e.key === "Backspace" && !q && value.length) onChange(value.slice(0, -1));
    else if (e.key === "Escape") setOpen(false);
  };

  const addGroup = async (v: string) => {
    if (!v) return;
    const [kind, ...rest] = v.split(":");
    const key = rest.join(":");
    const label = kind === "index" ? cat.data.indices.find((x: any) => x.key === key)?.label
      : kind === "cap" ? cat.data.caps.find((x: any) => x.key === key)?.label
      : kind === "screen" ? `screen “${cat.data.screens.find((x: any) => x.key === key)?.label}”` : `the ${key} sector`;
    setBusy(true); setNote(null);
    try {
      const r = await api<any>(`/api/screener/list?kind=${kind}&key=${encodeURIComponent(key)}`);
      if (!r.symbols.length) setNote({ text: `${label} has no stocks yet.`, bad: true });
      else add(r.symbols, label);
    } catch (e: any) {
      setNote({ text: e.message, bad: true });
    } finally { setBusy(false); }
  };

  const c = cat.data;
  return (
    <div className="space-y-2" ref={box}>
      <div className={`input flex min-h-[44px] flex-wrap items-center gap-1.5 ${disabled ? "opacity-60" : ""}`} onClick={() => !disabled && box.current?.querySelector("input")?.focus()}>
        {value.map((s) => (
          <span key={s} className="inline-flex items-center gap-1 rounded bg-dusk-raised px-2 py-0.5 text-sm">
            {s}{!disabled && <button type="button" aria-label={`Remove ${s}`} className="text-mute hover:text-loss" onClick={(e) => { e.stopPropagation(); onChange(value.filter((x) => x !== s)); }}>×</button>}
          </span>
        ))}
        {!disabled && <div className="relative min-w-[140px] flex-1">
          <input className="w-full bg-transparent outline-none" value={q} placeholder={value.length ? "Add another…" : "Type a symbol or company name"}
            onChange={(e) => { setQ(e.target.value.toUpperCase()); setOpen(true); }} onKeyDown={onKey} onFocus={() => setOpen(true)}
            onPaste={(e) => { const t = e.clipboardData.getData("text"); if (/[,\s]/.test(t.trim())) { e.preventDefault(); add(t.split(/[,\s]+/)); } }}
            aria-label="Search stocks" role="combobox" aria-expanded={open && hits.length > 0} />
          {open && hits.length > 0 && (
            <ul className="absolute left-0 top-full z-20 mt-1 max-h-64 w-72 overflow-auto rounded-md border border-dusk-line bg-dusk-raised py-1 shadow-lg" role="listbox">
              {hits.map((h, i) => (
                <li key={h.symbol} role="option" aria-selected={i === hi} onMouseDown={(e) => { e.preventDefault(); commit(h.symbol); }} onMouseEnter={() => setHi(i)}
                  className={`cursor-pointer px-3 py-1.5 text-sm ${i === hi ? "bg-dusk-line" : ""} ${value.includes(h.symbol) ? "opacity-50" : ""}`}>
                  <b>{h.symbol}</b>{h.name && <span className="ml-2 text-mute">{h.name}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>}
      </div>
      {!disabled && <div className="flex flex-wrap items-center gap-2 text-sm">
        <select className="input w-auto py-1.5" value="" disabled={busy || !c} onChange={(e) => addGroup(e.target.value)} aria-label="Add a group of stocks">
          <option value="">{busy ? "Adding…" : "+ Add a group…"}</option>
          {c && <>
            {["Index", "Sectoral index", "Thematic index"].map((g) => (
              <optgroup key={g} label={g}>{c.indices.filter((x: any) => (x.group ?? "Index") === g).map((x: any) => <option key={x.key} value={`index:${x.key}`}>{x.label}</option>)}</optgroup>))}
            <optgroup label="Market cap">{c.caps.map((x: any) => <option key={x.key} value={`cap:${x.key}`}>{x.label}</option>)}</optgroup>
            {c.screens.length > 0 && <optgroup label="My screens">{c.screens.map((x: any) => <option key={x.key} value={`screen:${x.key}`}>{x.label} ({x.count})</option>)}</optgroup>}
            {c.sectors.length > 0 && <optgroup label="Sector (all listed stocks)">{c.sectors.map((x: any) => <option key={x.key} value={`sector:${x.key}`}>{x.label} ({x.count})</option>)}</optgroup>}
          </>}
        </select>
        <span className="text-mute">{value.length}{max !== Infinity ? ` / ${max}` : ""} stock{value.length === 1 ? "" : "s"}</span>
        {value.length > 0 && <button type="button" className="text-mute hover:text-loss" onClick={() => { onChange([]); setNote(null); }}>Clear all</button>}
      </div>}
      {!disabled && c?.unclassified > 0 && c.sectors.length > 0 && <p className="text-[12px] text-mute">{c.unclassified} smaller stocks have no sector yet: they get one as their company figures arrive (each night).</p>}
      {note && <p className={`text-[13px] ${note.bad ? "text-saffron" : "text-mute"}`}>{note.text}</p>}
    </div>
  );
}
