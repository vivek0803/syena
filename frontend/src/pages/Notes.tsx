import { ArrowRight, History, Pause, Pencil, Pin, Play, RefreshCw, Search, Sparkles, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorNote, Loading, Page, Panel, Pill, Tabs, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAct, useApi } from "../lib/hooks";
import { when } from "../lib/format";

type Note = {
  id: number; title: string; body: string; kind: string; pinned: boolean; active: boolean; applies: string[];
  summary: string | null; effects: { when: string; then: string; direction: string }[]; keywords: string[]; sectors: string[]; symbols: string[];
  digest_status: string; used_count: number; last_used_at: string | null; created_at: string; updated_at: string;
  outcomes: { trades: number; closed: number; wins: number; pnl: number } | null;
};
const AREAS: Record<string, string> = { news: "News trading", ask: "Syena AI", compare: "Compare stocks", invest: "Manual invest", review: "Trade reviews" };
const KINDS: [string, string][] = [["general", "General"], ["macro", "Macro"], ["sector", "Sector"], ["stock", "Stock"], ["rule", "Trading rule"]];
const EXAMPLES = [
  "When crude oil falls, paint, tyre and aviation companies gain: crude derivatives and fuel are their biggest costs.",
  "A weaker rupee helps IT services and pharma exporters (they earn in dollars) and hurts oil marketing companies.",
  "RBI rate cuts help banks, NBFCs, autos and real estate first; the move usually starts a few days before the meeting.",
  "Don't buy a stock on news if it has already run up more than 8% that day: the move is usually priced in.",
];
const DIR: Record<string, string> = { positive: "text-gain", negative: "text-loss", mixed: "text-saffron" };
const blank = { id: 0, title: "", body: "", kind: "general", pinned: false, applies: Object.keys(AREAS) };

export default function Notes() {
  const list = useApi<Note[]>("/api/notes");
  const toast = useToast();
  const [draft, setDraft] = useState(blank);
  const [filter, setFilter] = useState("all");
  const [q, setQ] = useState("");
  const box = useRef<HTMLTextAreaElement>(null);
  const save = useAct((d: typeof blank) => api<Note>(d.id ? `/api/notes/${d.id}` : "/api/notes",
    { method: d.id ? "PUT" : "POST", body: { body: d.body, title: d.title, kind: d.kind, pinned: d.pinned, applies: d.applies } }), ["/api/notes"]);
  const submit = () => {
    if (!draft.body.trim()) return;
    save.mutate(draft, {
      onSuccess: (n) => { setDraft(blank); toast.show(n.summary ? `Saved. Syena reads it as: ${n.summary}` : "Saved. Syena will use it from the next analysis."); },
      onError: (e) => toast.show(e.message, "loss"),
    });
  };
  const edit = (n: Note) => { setDraft({ id: n.id, title: n.title, body: n.body, kind: n.kind, pinned: n.pinned, applies: n.applies }); window.scrollTo({ top: 0, behavior: "smooth" }); setTimeout(() => box.current?.focus(), 300); };
  useEffect(() => { // links like /notes#note-12 from an AI answer
    const id = window.location.hash.match(/^#note-(\d+)/)?.[1];
    if (id && list.data) document.getElementById(`note-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [list.data]);
  const shown = useMemo(() => (list.data ?? []).filter((n) =>
    (filter === "all" ? true : filter === "paused" ? !n.active : n.kind === filter && n.active) &&
    (!q.trim() || `${n.title} ${n.body} ${n.symbols.join(" ")} ${n.sectors.join(" ")}`.toLowerCase().includes(q.trim().toLowerCase()))), [list.data, filter, q]);
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: list.data?.length ?? 0, paused: 0 };
    for (const n of list.data ?? []) { if (!n.active) c.paused++; else c[n.kind] = (c[n.kind] ?? 0) + 1; }
    return c;
  }, [list.data]);

  return (
    <Page title="My notes" intro="Write down what you learn about markets: macro links, sector behaviour, your own rules. Syena's AI reads the notes that relate to what it is looking at, and tells you when one changed its view.">
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_340px]">
        <Panel title={draft.id ? "Edit note" : "What did you learn?"} aside={draft.id ? <button type="button" className="btn-ghost" onClick={() => setDraft(blank)}><X size={14} /> Cancel edit</button> : undefined}>
          <textarea ref={box} className="input min-h-[150px] w-full resize-y text-[15px] leading-relaxed" value={draft.body} maxLength={8000}
            onChange={(e) => setDraft({ ...draft, body: e.target.value })} aria-label="Your note"
            onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(); }}
            placeholder={"e.g. " + EXAMPLES[0] + "\n\nWrite it the way you'd explain it to a friend: what happens, to which companies, and why."} />
          {!draft.body && !draft.id && (
            <div className="mt-2 flex flex-wrap gap-1.5 text-[12px]"><span className="py-1 text-mute">Examples:</span>
              {EXAMPLES.slice(1).map((x) => <button key={x} type="button" onClick={() => setDraft({ ...draft, body: x })}
                className="max-w-[260px] truncate rounded-md border border-dusk-line px-2 py-1 text-left text-parch-dim hover:border-[#A98BF5]/50 hover:text-parch" title={x}>{x}</button>)}
            </div>)}
          <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_170px]">
            <input className="input" placeholder="Title (optional: Syena writes one)" value={draft.title} maxLength={200} onChange={(e) => setDraft({ ...draft, title: e.target.value })} aria-label="Title" />
            <select className="input" value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value })} aria-label="Kind of note">
              {KINDS.map(([k, l]) => <option key={k} value={k}>{k === "general" ? "Kind: auto" : l}</option>)}
            </select>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-1.5 text-[13px]">
            <span className="mr-1 text-mute">Use in</span>
            {Object.entries(AREAS).map(([k, l]) => {
              const on = draft.applies.includes(k);
              return <button key={k} type="button" aria-pressed={on} onClick={() => setDraft({ ...draft, applies: on ? draft.applies.filter((a) => a !== k) : [...draft.applies, k] })}
                className={`rounded-full border px-2.5 py-0.5 ${on ? "border-[#A98BF5]/50 bg-[#A98BF5]/10 text-parch" : "border-dusk-line text-mute"}`}>{l}</button>;
            })}
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            <label className="flex items-center gap-2 text-[13px] text-parch-dim" title="Given to the AI every time, not only when related">
              <input type="checkbox" checked={draft.pinned} onChange={(e) => setDraft({ ...draft, pinned: e.target.checked })} /> Always consider this (a core belief or rule)
            </label>
            <button type="button" className="btn-primary" disabled={!draft.body.trim() || !draft.applies.length || save.isPending} onClick={submit}>
              {save.isPending ? "Reading your note…" : draft.id ? "Save changes" : "Teach Syena"}</button>
          </div>
        </Panel>

        <div className="space-y-6">
          <TryIt />
          <Panel title="How Syena uses them">
            <ol className="space-y-2 text-[13px] leading-relaxed text-parch-dim">
              <li><b className="text-parch">1.</b> On save, the AI reads the note and works out the cause and effect, the industries and stocks it touches, and the words a related headline would use.</li>
              <li><b className="text-parch">2.</b> When news, a question or a stock review relates to a note (same stock or industry, matching words, similar wording), the note goes to the AI with Syena's data. Notes marked "always" go every time.</li>
              <li><b className="text-parch">3.</b> The AI weighs your note against live data and marks it <span className="text-parch">★</span> when it changed the verdict. Where the data clearly disagrees, the data wins.</li>
              <li><b className="text-parch">4.</b> Notes never bypass the trade gate, risk limits or paper/live rules.</li>
            </ol>
          </Panel>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center gap-3">
        <Tabs value={filter} onChange={setFilter} tabs={[["all", "All"], ...KINDS.filter(([k]) => k !== "general").concat([["general", "General"]]), ["paused", "Paused"]]
          .filter(([k]) => k === "all" || counts[k]).map(([k, l]) => ({ id: k, label: <>{l} <span className="text-mute">{counts[k] ?? 0}</span></> }))} />
        <label className="relative ml-auto w-full sm:w-64">
          <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-mute" />
          <input className="input w-full pl-8" placeholder="Search notes, stocks, industries" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search notes" />
        </label>
      </div>
      <ErrorNote error={list.error} />
      <div className="mt-4 space-y-3">
        {list.isLoading ? <Loading what="Loading notes" />
          : !list.data?.length ? <Empty title="No notes yet">Your first note could be something you read today: how a policy, a commodity price or a currency move tends to hit certain companies.</Empty>
          : !shown.length ? <p className="text-parch-dim">No notes match.</p>
          : shown.map((n) => <NoteCard key={n.id} n={n} onEdit={() => edit(n)} toast={toast.show} />)}
      </div>
      {toast.node}
    </Page>
  );
}

function NoteCard({ n, onEdit, toast }: { n: Note; onEdit: () => void; toast: (t: string, tone?: string) => void }) {
  const [more, setMore] = useState(false);
  const [hist, setHist] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const patch = useAct((b: any) => api(`/api/notes/${n.id}`, { method: "PATCH", body: b }), ["/api/notes"]);
  const del = useAct(() => api(`/api/notes/${n.id}`, { method: "DELETE" }), ["/api/notes"]);
  const reindex = useAct(() => api(`/api/notes/${n.id}/reindex`, { method: "POST" }), ["/api/notes"]);
  const long = n.body.length > 320;
  const o = n.outcomes;
  return (
    <article id={`note-${n.id}`} className={`panel scroll-mt-24 p-4 ${n.active ? "" : "opacity-60"} ${window.location.hash === `#note-${n.id}` ? "ring-1 ring-[#A98BF5]/60" : ""}`}>
      <header className="flex flex-wrap items-center gap-2">
        <h3 className="font-display text-[17px] font-semibold text-parch">{n.title}</h3>
        <Pill tone="info" dot={false}>{KINDS.find(([k]) => k === n.kind)?.[1] ?? n.kind}</Pill>
        {n.pinned && <Pill tone="warn" dot={false}><Pin size={11} /> always</Pill>}
        {!n.active && <Pill dot={false}>paused</Pill>}
        <span className="ml-auto flex items-center gap-0.5 text-mute">
          <IconBtn label="Edit" onClick={onEdit}><Pencil size={15} /></IconBtn>
          <IconBtn label={n.pinned ? "Only when related" : "Always consider"} onClick={() => patch.mutate({ pinned: !n.pinned })}><Pin size={15} className={n.pinned ? "text-saffron" : ""} /></IconBtn>
          <IconBtn label={n.active ? "Pause: the AI stops reading it" : "Resume"} onClick={() => patch.mutate({ active: !n.active })}>{n.active ? <Pause size={15} /> : <Play size={15} />}</IconBtn>
          {confirm ? <span className="ml-1 flex items-center gap-1 text-[12px]">Delete?
            <button type="button" className="rounded bg-loss/20 px-2 py-0.5 text-loss" onClick={() => del.mutate(undefined, { onSuccess: () => toast("Note deleted") })}>Yes</button>
            <button type="button" className="px-1 hover:text-parch" onClick={() => setConfirm(false)}>No</button></span>
            : <IconBtn label="Delete" onClick={() => setConfirm(true)}><Trash2 size={15} /></IconBtn>}
        </span>
      </header>
      <p className="mt-2 whitespace-pre-wrap text-[15px] leading-relaxed text-parch-dim">{long && !more ? n.body.slice(0, 320) + "…" : n.body}
        {long && <button type="button" className="ml-1 text-[13px] text-saffron hover:underline" onClick={() => setMore(!more)}>{more ? "less" : "more"}</button>}</p>

      {(n.summary || n.effects.length > 0) && (
        <div className="mt-3 rounded-lg border border-[#A98BF5]/25 bg-[#A98BF5]/[0.06] px-3 py-2 text-[13px]">
          <div className="mb-1 flex items-center gap-1.5 text-[12px] font-medium text-[#A98BF5]"><Sparkles size={12} /> How Syena reads it</div>
          {n.summary && <p className="text-parch">{n.summary}</p>}
          {n.effects.length > 0 && <ul className="mt-1 space-y-0.5">{n.effects.map((e, i) => (
            <li key={i} className="flex flex-wrap items-center gap-1.5 text-parch-dim">{e.when} <ArrowRight size={12} className="text-mute" /> <span className={DIR[e.direction] ?? ""}>{e.then}</span></li>))}</ul>}
        </div>)}

      <div className="mt-3 flex flex-wrap gap-1.5 text-[12px]">
        {n.symbols.map((s) => <Link key={s} to={`/stock/${s}`} className="rounded-md bg-sky/10 px-1.5 py-0.5 font-medium text-sky hover:underline">{s}</Link>)}
        {n.sectors.map((s) => <span key={s} className="rounded-md bg-gain/10 px-1.5 py-0.5 text-gain">{s}</span>)}
        {n.keywords.slice(0, more ? 30 : 8).map((k) => <span key={k} className="rounded-md border border-dusk-line px-1.5 py-0.5 text-mute">{k}</span>)}
        {!more && n.keywords.length > 8 && <button type="button" className="px-1 text-mute hover:text-parch" onClick={() => setMore(true)}>+{n.keywords.length - 8}</button>}
        {n.digest_status === "basic" && <button type="button" className="inline-flex items-center gap-1 rounded-md border border-saffron/30 px-1.5 py-0.5 text-saffron" title="Indexed by words only (AI was unavailable). Ask the AI to read it again."
          onClick={() => reindex.mutate(undefined)}><RefreshCw size={11} className={reindex.isPending ? "animate-spin" : ""} /> words only: let the AI read it</button>}
      </div>

      <footer className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-dusk-line/60 pt-2 text-[12px] text-mute">
        <span>{n.used_count ? <>Used <b className="text-parch">{n.used_count}</b> time{n.used_count === 1 ? "" : "s"}{n.last_used_at && <>, last {when(n.last_used_at)}</>}</> : "Not used yet"}</span>
        {o && o.trades > 0 && <span>News trades it informed: <b className="text-parch">{o.trades}</b>{o.closed > 0 && <> · won {o.wins}/{o.closed} · P&L <b className={o.pnl >= 0 ? "text-gain" : "text-loss"}>₹{o.pnl.toLocaleString("en-IN")}</b></>}</span>}
        <span>Used in: {n.applies.length === 5 ? "everything" : n.applies.map((a) => AREAS[a]).join(", ")}</span>
        <span>Updated {when(n.updated_at)}</span>
        {n.used_count > 0 && <button type="button" className="ml-auto inline-flex items-center gap-1 hover:text-parch" onClick={() => setHist(!hist)}><History size={12} /> {hist ? "Hide" : "Where it was used"}</button>}
      </footer>
      {hist && <Uses id={n.id} />}
    </article>
  );
}

function Uses({ id }: { id: number }) {
  const u = useApi<any>(`/api/notes/${id}/uses`);
  if (u.isLoading) return <Loading />;
  return (
    <ul className="mt-2 divide-y divide-dusk-line/60 text-[13px]">
      {(u.data?.uses ?? []).map((x: any, i: number) => (
        <li key={i} className="flex flex-wrap items-center gap-2 py-1.5">
          <span className="w-28 shrink-0 text-mute">{when(x.ts)}</span>
          <span className="rounded bg-dusk px-1.5 py-0.5 text-[12px] text-parch-dim">{x.label}</span>
          <span className="min-w-0 flex-1 truncate text-parch-dim" title={x.ref}>{x.ref}</span>
          {x.influenced && <span className="text-[12px] text-[#A98BF5]" title="The AI said this note changed its view">★ changed the view</span>}
        </li>))}
    </ul>);
}

function TryIt() {
  const [text, setText] = useState("");
  const [area, setArea] = useState("news");
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    if (!text.trim()) return;
    setBusy(true);
    try { setRes(await api("/api/notes/try", { body: { text, area } })); } finally { setBusy(false); }
  };
  return (
    <Panel title="Try it" aside={<span className="text-[12px] text-mute">no AI cost</span>}>
      <p className="mb-2 text-[13px] text-parch-dim">Paste a headline or question to see which notes Syena would read.</p>
      <textarea className="input min-h-[70px] w-full text-[14px]" value={text} onChange={(e) => setText(e.target.value)} placeholder="Brent crude falls 5% as OPEC+ raises output" aria-label="Headline to test"
        onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); run(); } }} />
      <div className="mt-2 flex gap-2">
        <select className="input flex-1" value={area} onChange={(e) => setArea(e.target.value)} aria-label="Where">
          {Object.entries(AREAS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <button type="button" className="btn-ghost" onClick={run} disabled={busy || !text.trim()}>{busy ? "…" : "Check"}</button>
      </div>
      {res && (
        <div className="mt-3 text-[13px]">
          {res.symbols?.length > 0 && <p className="mb-1 text-mute">Stocks found: {res.symbols.join(", ")}</p>}
          {!res.matches.length ? <p className="text-parch-dim">No note relates to this. Add keywords or a stock name to a note if it should.</p>
            : <ul className="space-y-1.5">{res.matches.map((m: any) => (
              <li key={m.id}><a href={`#note-${m.id}`} className="font-medium text-parch hover:underline">{m.title}</a><div className="text-[12px] text-mute">{m.why}</div></li>))}</ul>}
        </div>)}
    </Panel>
  );
}

function IconBtn({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return <button type="button" title={label} aria-label={label} onClick={onClick} className="rounded-md p-1.5 hover:bg-dusk-raised hover:text-parch">{children}</button>;
}
