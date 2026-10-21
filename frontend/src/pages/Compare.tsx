import StockLink from "../components/StockLink";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import StockPicker from "../components/StockPicker";
import { Empty, ErrorNote, Field, Loading, Modal, Page, Panel, Pill, Tabs, useToast } from "../components/ui";
import { api } from "../lib/api";
import { day, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

// Categorical palette validated for Syena's dark surface (#1B2130): fixed order, colour follows the stock.
const PALETTE = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"];
const MAX_CHART = 8;

type Dir = "high" | "low" | null;
type Col = { key: string; label: string; dir: Dir; d?: number; tech?: boolean };
const GROUPS: { name: string; cols: Col[] }[] = [
  { name: "Price", cols: [
    { key: "price", label: "Price ₹", dir: null, d: 2 }, { key: "change", label: "Day %", dir: null, d: 2 },
    { key: "market_cap", label: "Mcap ₹cr", dir: null, d: 0 }, { key: "pct_from_52w_high", label: "vs 52w high %", dir: null }] },
  { name: "Valuation", cols: [
    { key: "pe", label: "P/E", dir: "low" }, { key: "pb", label: "P/B", dir: "low" }, { key: "ps", label: "P/S", dir: "low" },
    { key: "ev_ebitda", label: "EV/EBITDA", dir: "low" }, { key: "earnings_yield", label: "Earn. yld %", dir: "high" },
    { key: "dividend_yield", label: "Div. yld %", dir: "high", d: 2 }] },
  { name: "Quality", cols: [
    { key: "roe", label: "ROE %", dir: "high" }, { key: "roa", label: "ROA %", dir: "high" }, { key: "opm", label: "Op. margin %", dir: "high" },
    { key: "npm", label: "Net margin %", dir: "high" }, { key: "debt_to_equity", label: "Debt/Eq", dir: "low", d: 2 },
    { key: "current_ratio", label: "Current ratio", dir: "high", d: 2 }, { key: "promoter_holding", label: "Promoter %", dir: "high" }] },
  { name: "Growth (YoY)", cols: [{ key: "sales_growth", label: "Sales %", dir: "high" }, { key: "profit_growth", label: "Profit %", dir: "high" }] },
  { name: "Technicals", cols: [
    { key: "ret_1m", label: "1M %", dir: "high", tech: true }, { key: "ret_3m", label: "3M %", dir: "high", tech: true },
    { key: "ret_6m", label: "6M %", dir: "high", tech: true }, { key: "ret_1y", label: "1Y %", dir: "high", tech: true },
    { key: "rsi14", label: "RSI 14", dir: null, tech: true }, { key: "vs_dma50", label: "vs 50 DMA %", dir: null, tech: true },
    { key: "vs_dma200", label: "vs 200 DMA %", dir: null, tech: true }, { key: "volatility", label: "Volatility %", dir: "low", tech: true },
    { key: "max_drawdown", label: "Max fall %", dir: "high", tech: true }] },
];
const ALL_COLS = GROUPS.flatMap((g) => g.cols);
const hash = (t: string) => { let h = 5381; for (let i = 0; i < t.length; i++) h = ((h << 5) + h + t.charCodeAt(i)) | 0; return (h >>> 0).toString(36); };
const fmt = (v: any, d = 1) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));

export default function Compare() {
  const { id } = useParams();
  const nav = useNavigate();
  const lists = useApi<any[]>("/api/compare/lists");
  const [newOpen, setNewOpen] = useState(false);
  const current = id ? Number(id) : lists.data?.[0]?.id;
  useEffect(() => { if (!id && lists.data?.length) nav(`/compare/${lists.data[0].id}`, { replace: true }); }, [id, lists.data, nav]);
  return (
    <Page title="Compare stocks" intro="Pick any stocks, see every metric side by side, and ask the AI which one looks strongest."
      actions={<button className="btn-primary" onClick={() => setNewOpen(true)}>New list</button>}>
      <ErrorNote error={lists.error} />
      {lists.isLoading ? <Loading /> : !lists.data?.length ? (
        <Panel><Empty title="No lists yet" action={<button className="btn-primary" onClick={() => setNewOpen(true)}>Create a list</button>}>
          Make a list like “IT majors” or “My bank picks”, add stocks by name, a whole index or a screen, and compare them.</Empty></Panel>
      ) : (
        <div className="space-y-6">
          {lists.data.length > 1 && <Tabs value={String(current)} onChange={(v) => nav(`/compare/${v}`)}
            tabs={lists.data.map((l) => ({ id: String(l.id), label: <>{l.name} <span className="text-mute">{l.count}</span></> }))} />}
          {current && <ListView key={current} id={current} />}
        </div>
      )}
      <Modal open={newOpen} onClose={() => setNewOpen(false)} title="New list"><NewList onDone={(l) => { setNewOpen(false); nav(`/compare/${l.id}`); }} /></Modal>
    </Page>
  );
}

function NewList({ onDone }: { onDone: (l: any) => void }) {
  const [name, setName] = useState("");
  const [syms, setSyms] = useState<string[]>([]);
  const make = useAct((b: any) => api("/api/compare/lists", { body: b }), ["/api/compare"]);
  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); make.mutate({ name, symbols: syms }, { onSuccess: onDone }); }}>
      <Field label="Name"><input className="input" required value={name} onChange={(e) => setName(e.target.value)} placeholder="IT majors" /></Field>
      <Field label="Stocks"><StockPicker value={syms} onChange={setSyms} /></Field>
      <ErrorNote error={make.error} />
      <button className="btn-primary" disabled={make.isPending}>Create</button>
    </form>
  );
}

function ListView({ id }: { id: number }) {
  const toast = useToast();
  const nav = useNavigate();
  const det = useApi<any>(`/api/compare/lists/${id}`, { refetchInterval: (q: any) => (q.state.data?.job === "running" ? 3000 : false) } as any);
  const tech = useApi<any>(det.data?.list?.symbols?.length ? `/api/compare/lists/${id}/technicals?v=${hash(det.data.list.symbols.join(","))}` : null, { staleTime: 5 * 60_000 } as any);
  const save = useAct((b: any) => api(`/api/compare/lists/${id}`, { method: "PUT", body: b }), ["/api/compare"]);
  const del = useAct(() => api(`/api/compare/lists/${id}`, { method: "DELETE" }), ["/api/compare"]);
  const refresh = useAct(() => api(`/api/compare/lists/${id}/refresh`, { method: "POST" }), [`/api/compare/lists/${id}`]);
  const [syms, setSyms] = useState<string[] | null>(null);
  const [name, setName] = useState<string | null>(null);
  const [prevJob, setPrevJob] = useState<string | null>(null);

  const job: string | null = det.data?.job ?? null;
  useEffect(() => {  // finished updating: re-read figures and technicals once
    if (prevJob === "running" && job && job !== "running") { toast.show(job.replace(/^done: /, "Updated: ")); tech.refetch(); }
    setPrevJob(job);
  }, [job]); // eslint-disable-line react-hooks/exhaustive-deps

  if (det.isLoading) return <Loading />;
  if (det.error || !det.data) return <ErrorNote error={det.error} />;
  const l = det.data.list;
  const list: string[] = syms ?? l.symbols;
  const dirty = syms !== null && syms.join(",") !== l.symbols.join(",") || (name !== null && name !== l.name);
  const miss = det.data.missing;
  const gaps = miss.no_fundamentals.length + miss.stale_prices.length + miss.unknown.length;
  const t = tech.data ?? {};
  const notLoaded = Object.values(t).filter((x: any) => x?.error?.includes("Update data") || x?.stale).length;
  const rows = det.data.rows.map((r: any) => ({ ...r, ...(t[r.symbol] ?? {}) }));
  const color = (sym: string) => PALETTE[l.symbols.indexOf(sym) % PALETTE.length];

  return (
    <div className="space-y-6">
      {toast.node}
      <Panel title={<input className="bg-transparent font-display text-lg font-semibold outline-none" aria-label="List name" value={name ?? l.name} onChange={(e) => setName(e.target.value)} />}
        aside={<div className="flex gap-2">
          <button className="btn-ghost py-1.5" disabled={job === "running" || !l.symbols.length} onClick={() => refresh.mutate(undefined)}>{job === "running" ? "Updating…" : "Update data"}</button>
          <button className="btn-ghost py-1.5 text-loss" onClick={() => del.mutate(undefined, { onSuccess: () => nav("/compare") })}>Delete list</button></div>}>
        <StockPicker value={list} onChange={setSyms} />
        {dirty && <div className="mt-3 flex gap-2">
          <button className="btn-primary" disabled={save.isPending} onClick={() => save.mutate({ name: name ?? l.name, symbols: list }, { onSuccess: () => { setSyms(null); setName(null); } })}>Save changes</button>
          <button className="btn-ghost" onClick={() => { setSyms(null); setName(null); }}>Undo</button></div>}
        <ErrorNote error={save.error || refresh.error || del.error} />
        {gaps > 0 && !dirty && <p className="mt-3 text-sm text-parch-dim">
          {miss.unknown.length > 0 && <>Not found on NSE yet: {miss.unknown.join(", ")}. </>}
          {miss.no_fundamentals.length > 0 && <>No company figures yet for {miss.no_fundamentals.length} stock{miss.no_fundamentals.length > 1 ? "s" : ""}. </>}
          {miss.stale_prices.length > 0 && <>Prices older than a few days for {miss.stale_prices.length}. </>}
          Click <b>Update data</b> to fetch them (about a second per stock).</p>}
        {notLoaded > 0 && !dirty && job !== "running" && <p className="mt-2 text-sm text-parch-dim">Price history isn't loaded yet for {notLoaded} stock{notLoaded > 1 ? "s" : ""} (big lists only load the first few when opened). Click <b>Update data</b> to load all of them: about {Math.max(1, Math.round(notLoaded / 1.4 / 60))} min the first time.</p>}
        {job && job !== "running" && job.startsWith("failed") && <p className="mt-2 text-sm text-loss">{job}</p>}
      </Panel>

      {l.symbols.length >= 2 && <AiPanel id={id} list={l} />}
      {l.symbols.length > 0 && <Performance rows={rows} color={color} loading={tech.isLoading} error={tech.error} />}
      {l.symbols.length > 0 && <MetricsTable rows={rows} color={color} techLoading={tech.isLoading} />}
    </div>
  );
}

function AiPanel({ id, list }: { id: number; list: any }) {
  const [horizon, setHorizon] = useState(list.ai_result?.horizon ?? "LONG_TERM");
  const [note, setNote] = useState("");
  const ask = useAct((b: any) => api(`/api/compare/lists/${id}/ai`, { body: b }), [`/api/compare/lists/${id}`, "/api/compare/lists"]);
  const r = ask.data ?? list.ai_result;
  const changed = r && r.stocks && r.stocks.join(",") !== list.symbols.join(",");
  const top = r?.ranking?.find((x: any) => x.symbol === r.top_pick);
  return (
    <Panel title="AI pick" aside={<span className="text-[13px] text-mute">Uses only the figures on this page</span>}>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); ask.mutate({ horizon, note: note || null }); }}>
        <Field label="For"><select className="input w-auto" value={horizon} onChange={(e) => setHorizon(e.target.value)}>
          <option value="LONG_TERM">Long-term investing</option><option value="SWING">Swing trade (days–weeks)</option></select></Field>
        <div className="min-w-[220px] flex-1"><Field label="Anything to favour? (optional)">
          <input className="input" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. low debt, steady dividends" /></Field></div>
        <button className="btn-primary" disabled={ask.isPending}>{ask.isPending ? (list.symbols.length > 40 ? `Comparing in ${Math.ceil(list.symbols.length / 40) + 1} rounds…` : "Comparing…") : r ? "Ask again" : "Ask AI for the best pick"}</button>
      </form>
      <ErrorNote error={ask.error} />
      {r && r.top_pick && (
        <div className="mt-5 space-y-4">
          {r.rounds > 1 && <p className="text-sm text-parch-dim">{r.stocks.length} stocks were compared in groups of 40; the best 5 of each group met in a final ({r.finalists?.length} finalists). Stocks marked “group round” were judged against their group only.</p>}
          {ask.data?.reused && <p className="text-sm text-parch-dim">Nothing changed since the last answer (same stocks, figures, horizon and preference), so this is that answer. It updates when prices or company figures change.</p>}
          {changed && <p className="text-sm text-saffron">The list changed since this answer: ask again to include the new stocks.</p>}
          <div className="rounded-panel border border-saffron/40 bg-saffron-soft p-4">
            <div className="text-[13px] uppercase tracking-wide text-saffron">Top pick · {r.horizon === "SWING" ? "swing trade" : "long-term"}</div>
            <div className="mt-1 font-display text-2xl font-semibold">{r.top_pick}{top?.score != null && <span className="ml-3 text-base font-normal text-parch-dim">score {top.score}/100</span>}</div>
            {r.summary && <p className="mt-2 text-parch-dim">{r.summary}</p>}
            {r.top_pick_reasons?.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm">{r.top_pick_reasons.map((x: string) => <li key={x}>{x}</li>)}</ul>}
          </div>
          <div>
            <div className="mb-2 text-sm font-semibold">Ranking</div>
            <ol className="space-y-3">{r.ranking.map((x: any, i: number) => (
              <li key={x.symbol} className="flex gap-3 text-sm">
                <span className="w-5 shrink-0 text-mute">{i + 1}.</span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <b>{x.symbol}</b>{x.round === "group" && <span className="text-[12px] text-mute">group round</span>}
                    <Pill tone={x.verdict === "strong" ? "gain" : x.verdict === "weak" ? "loss" : x.verdict === "good" ? "info" : "mute"}>{x.verdict}</Pill>
                    <span className="flex items-center gap-2" aria-label={`score ${x.score ?? "unknown"} of 100`}>
                      <span className="h-1.5 w-20 overflow-hidden rounded-full bg-dusk-line"><span className="block h-full rounded-full bg-saffron" style={{ width: `${x.score ?? 0}%` }} /></span>
                      <span className="text-mute">{x.score ?? "–"}</span></span>
                  </div>
                  <p className="mt-0.5 text-parch-dim">{x.why}</p>
                </div>
              </li>))}</ol>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            {r.risks?.length > 0 && <div><div className="mb-1 text-sm font-semibold">Risks for the top pick</div><ul className="list-disc pl-5 text-sm text-parch-dim">{r.risks.map((x: string) => <li key={x}>{x}</li>)}</ul></div>}
            {r.data_gaps?.length > 0 && <div><div className="mb-1 text-sm font-semibold">Missing data that could change this</div><ul className="list-disc pl-5 text-sm text-parch-dim">{r.data_gaps.map((x: string) => <li key={x}>{x}</li>)}</ul></div>}
          </div>
          <p className="text-[13px] text-mute">{list.ai_at && !ask.data ? `Answered ${when(list.ai_at)} · ` : ""}{r.model} · Based on Yahoo company figures and Angel prices on this page, no news. Decision support, not investment advice.</p>
        </div>
      )}
    </Panel>
  );
}

function Performance({ rows, color, loading, error }: { rows: any[]; color: (s: string) => string; loading: boolean; error: unknown }) {
  const withSeries = rows.filter((r) => r.series?.length > 1);
  const [shown, setShown] = useState<string[] | null>(null);
  const on = shown ?? withSeries.slice(0, MAX_CHART).map((r) => r.symbol);
  const data = useMemo(() => {
    const byDate = new Map<string, any>();
    for (const r of withSeries.filter((x) => on.includes(x.symbol))) {
      const base = r.series[0][1];
      for (const [d, v] of r.series) {
        const row = byDate.get(d) ?? { d };
        row[r.symbol] = Math.round((v / base) * 1000) / 10;
        byDate.set(d, row);
      }
    }
    return [...byDate.values()].sort((a, b) => a.d.localeCompare(b.d));
  }, [withSeries.map((r) => r.symbol).join(), on.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (s: string) => setShown(on.includes(s) ? on.filter((x) => x !== s) : on.length >= MAX_CHART ? on : [...on, s]);
  return (
    <Panel title="1-year performance" aside={<span className="text-[13px] text-mute">₹100 invested a year ago</span>}>
      <ErrorNote error={error} />
      {loading ? <Loading what="Loading price history" /> : !withSeries.length ? <p className="text-parch-dim">No price history yet. Click Update data.</p> : <>
        {withSeries.length > MAX_CHART && <p className="mb-2 text-[13px] text-mute">Showing up to {MAX_CHART} at a time: click a stock to show or hide it.</p>}
        <div className="mb-3 flex flex-wrap gap-1.5">{withSeries.map((r) => (
          <button key={r.symbol} type="button" onClick={() => toggle(r.symbol)} aria-pressed={on.includes(r.symbol)}
            className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-sm ${on.includes(r.symbol) ? "border-dusk-line text-parch" : "border-transparent text-mute line-through"}`}>
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: on.includes(r.symbol) ? color(r.symbol) : "transparent", border: `2px solid ${color(r.symbol)}` }} />{r.symbol}
          </button>))}</div>
        <div className="h-72" aria-label="Indexed price performance chart">
          <ResponsiveContainer>
            <LineChart data={data} margin={{ left: 4, right: 12, top: 8 }}>
              <CartesianGrid stroke="#2F384C" vertical={false} />
              <XAxis dataKey="d" tickFormatter={(v) => day(v).slice(3)} stroke="#8B93A7" fontSize={12} minTickGap={48} />
              <YAxis stroke="#8B93A7" fontSize={12} width={44} domain={["auto", "auto"]} />
              <Tooltip contentStyle={{ background: "#1B2130", border: "1px solid #2F384C", borderRadius: 8, color: "#ECE6D8" }} labelFormatter={(v) => day(v as string)}
                itemSorter={(i: any) => -(i.value ?? 0)} formatter={(v: number, n: string) => [`₹${v.toFixed(1)}`, n]} />
              {on.map((s) => <Line key={s} dataKey={s} stroke={color(s)} strokeWidth={2} dot={false} connectNulls isAnimationActive={false} />)}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </>}
    </Panel>
  );
}

function MetricsTable({ rows, color, techLoading }: { rows: any[]; color: (s: string) => string; techLoading: boolean }) {
  const [sort, setSort] = useState<{ key: string; desc: boolean } | null>(null);
  const extremes = useMemo(() => {
    const out: Record<string, { best?: number; worst?: number }> = {};
    for (const c of ALL_COLS) {
      if (!c.dir || rows.length < 2) continue;
      const vals = rows.map((r) => r[c.key]).filter((v) => v !== null && v !== undefined && !(c.key === "pe" && v <= 0)) as number[];
      if (vals.length < 2) continue;
      const hi = Math.max(...vals), lo = Math.min(...vals);
      if (hi === lo) continue;
      out[c.key] = c.dir === "high" ? { best: hi, worst: lo } : { best: lo, worst: hi };
    }
    return out;
  }, [rows]);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const have = rows.filter((r) => r[sort.key] != null).sort((a, b) => (a[sort.key] - b[sort.key]) * (sort.desc ? -1 : 1));
    return [...have, ...rows.filter((r) => r[sort.key] == null)];
  }, [rows, sort]);
  const click = (key: string) => setSort(sort?.key === key ? (sort.desc ? { key, desc: false } : null) : { key, desc: true });
  return (
    <Panel title="All metrics" pad={false} aside={<span className="text-[13px] text-mute"><span className="text-gain">▲</span> best and <span className="text-loss">▼</span> weakest in this list · click a heading to sort</span>}>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="text-[12px] uppercase tracking-wide text-mute">
              <th className="sticky left-0 z-10 bg-dusk-panel" />
              {GROUPS.map((g) => <th key={g.name} colSpan={g.cols.length} className="border-l border-dusk-line px-2 pt-3 text-left font-semibold">{g.name}</th>)}
            </tr>
            <tr className="border-b border-dusk-line text-[13px] text-parch-dim">
              <th className="sticky left-0 z-10 bg-dusk-panel px-4 py-2 text-left font-medium">Stock</th>
              {GROUPS.map((g) => g.cols.map((c, i) => (
                <th key={c.key} className={`whitespace-nowrap px-2 py-2 text-right font-medium ${i === 0 ? "border-l border-dusk-line" : ""}`}>
                  <button type="button" className="hover:text-parch" onClick={() => click(c.key)} title={c.dir ? `${c.dir === "high" ? "Higher" : "Lower"} is better` : undefined}>
                    {c.label}{sort?.key === c.key ? (sort.desc ? " ↓" : " ↑") : ""}</button>
                </th>)))}
            </tr>
          </thead>
          <tbody>{sorted.map((r) => (
            <tr key={r.symbol} className="border-b border-dusk-line/60 hover:bg-dusk-raised/40">
              <td className="sticky left-0 z-10 min-w-[180px] bg-dusk-panel px-4 py-2">
                <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color(r.symbol) }} /><StockLink symbol={r.symbol} /></div>
                <div className="truncate text-[12px] text-mute">{r.name ?? (r.known ? "" : "not found")}{r.sector ? ` · ${r.sector}` : ""}</div>
              </td>
              {GROUPS.map((g) => g.cols.map((c, i) => {
                const v = r[c.key];
                const e = extremes[c.key];
                const best = e && v === e.best, worst = e && v === e.worst;
                return (
                  <td key={c.key} className={`whitespace-nowrap px-2 py-2 text-right tabular-nums ${i === 0 ? "border-l border-dusk-line" : ""} ${best ? "bg-gain-soft" : worst ? "bg-loss-soft" : ""}`}
                    title={best ? "Best in this list" : worst ? "Weakest in this list" : undefined}>
                    {c.tech && techLoading && v == null ? <span className="text-mute">…</span> : fmt(v, c.d ?? 1)}
                    {best && <span className="ml-1 text-[10px] text-gain">▲</span>}{worst && <span className="ml-1 text-[10px] text-loss">▼</span>}
                  </td>);
              }))}
            </tr>))}</tbody>
        </table>
      </div>
      <p className="px-4 py-3 text-[12px] text-mute">Company figures from Yahoo (latest results, growth = latest quarter vs a year ago); prices from Angel One. “–” = not available yet.</p>
    </Panel>
  );
}
