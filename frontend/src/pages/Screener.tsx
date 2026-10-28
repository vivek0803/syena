import StockLink from "../components/StockLink";
import QueryBox from "../components/QueryBox";
import { FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Empty, ErrorNote, Field, Loading, Modal, Page, Panel, Pill, Stat, Table, Tabs, useToast } from "../components/ui";
import { api } from "../lib/api";
import { day, money, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const n = (v: any, d = 1) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));
const num = (key: string, label: string, d = 1, colour = false) => ({
  key, label, align: "right" as const,
  render: (r: any) => colour ? <span className={r[key] > 0 ? "text-gain" : r[key] < 0 ? "text-loss" : ""}>{r[key] > 0 ? "+" : ""}{n(r[key], d)}</span> : n(r[key], d),
});
const stockCol = { key: "symbol", label: "Stock", className: "whitespace-normal min-w-[160px]", render: (r: any) => <><Link to={`/company/${r.symbol}`} onClick={(e) => e.stopPropagation()} className="font-semibold text-sky hover:underline">{r.name ?? r.symbol}</Link><div className="text-[12px] text-mute"><StockLink symbol={r.symbol} className="font-mono" /></div></> };
const VIEWS: Record<string, { label: string; cols: any[] }> = {
  overview: { label: "Overview", cols: [stockCol, num("price", "Price", 2), num("change", "Day %", 2, true), num("market_cap", "Mcap ₹cr", 0), num("pe", "P/E"), num("pb", "P/B"),
    num("roe", "ROE %"), num("debt_to_equity", "D/E", 2), num("sales_growth", "Sales gr %"), num("profit_growth", "Profit gr %"), num("dividend_yield", "Div yld %", 2), num("pct_from_52w_high", "vs 52w high %")] },
  growth: { label: "3-year quality & growth", cols: [stockCol, num("market_cap", "Mcap ₹cr", 0), num("sales_cagr_3y", "Sales CAGR 3y %"), num("profit_cagr_3y", "Profit CAGR 3y %"),
    num("eps_cagr_3y", "EPS CAGR 3y %"), num("roe_3y", "ROE 3y avg %"), num("roce_3y", "ROCE 3y avg %"), num("opm_3y", "OPM 3y avg %"), num("roe", "ROE now %"), num("pe", "P/E")] },
  cash: { label: "Cash flow", cols: [stockCol, num("market_cap", "Mcap ₹cr", 0), num("operating_cash_flow", "Op. cash flow ₹cr", 0), num("free_cash_flow", "Free cash flow ₹cr", 0),
    num("fcf_3y_avg", "FCF 3y avg ₹cr", 0), num("fcf_yield", "FCF yield %", 2), num("debt_to_equity", "D/E", 2), num("pe", "P/E")] },
  holding: { label: "Shareholding", cols: [stockCol, num("market_cap", "Mcap ₹cr", 0), num("promoter_holding", "Promoter %"), num("promoter_change", "Δ promoter", 2, true),
    num("fii_holding", "FII %"), num("fii_change", "Δ FII", 2, true), num("dii_holding", "DII %"), num("dii_change", "Δ DII", 2, true), num("mf_holding", "MF %"), num("retail_holding", "Retail %")] },
};
const cols = VIEWS.overview.cols;
const examples = [
  "roe > 15 and debt to equity < 0.5 and sales growth > 15 and market cap > 1000",
  "3 year roe > 15 and sales cagr > 12 and fcf yield > 3",
  "fii change > 0.5 and dii change > 0 and promoter change >= 0 and market cap > 2000",
  "pe < 15 and pb < 2 and dividend yield > 2",
  "profit growth > 25 and roe > 18 and pct_from_52w_high > -10",
];

export default function Screener() {
  const { id } = useParams();
  return id ? <ScreenDetail id={Number(id)} /> : <ScreenerHome />;
}

function DataStatus() {
  const toast = useToast();
  const st = useApi<any>("/api/screener/status", { refetchInterval: 15_000 });
  const refresh = useAct((what: string) => api("/api/screener/refresh", { body: { what } }), ["/api/screener"]);
  const [csvOpen, setCsvOpen] = useState(false);
  const s = st.data;
  if (!s) return null;
  return (
    <Panel title="Data" aside={<div className="flex flex-wrap gap-2">
      <button className="btn-ghost" disabled={refresh.isPending} onClick={() => refresh.mutate("prices", { onSuccess: () => toast.show("Updating prices for all stocks…") })}>Update prices now</button>
      <button className="btn-ghost" disabled={refresh.isPending} onClick={() => refresh.mutate("fundamentals", { onSuccess: () => toast.show("Fetching company figures (next batch)…") })}>Fetch fundamentals now</button>
      <button className="btn-ghost" onClick={() => setCsvOpen(true)}>Import Screener.in CSV</button></div>}>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Stat label="Listed stocks" value={n(s.stocks, 0)} />
        <Stat label="With company figures" value={n(s.with_fundamentals, 0)} sub={s.stocks ? `${Math.round((s.with_fundamentals / s.stocks) * 100)}%` : undefined} />
        <Stat label="Prices as of" value={s.latest_day ? day(s.latest_day) : "not yet"} sub={s.priced_latest_day ? `${n(s.priced_latest_day, 0)} stocks` : undefined} />
        <Stat label="Background jobs" value={Object.keys(s.jobs ?? {}).length ? "" : "idle"} sub={Object.entries(s.jobs ?? {}).map(([k, v]) => `${k}: ${String(v).slice(0, 60)}`).join(" · ")} />
      </div>
      <p className="mt-3 text-[13px] text-mute">Prices and ratios update every trading day at 4:05 PM. Company figures (EPS, book value, ROE, debt, growth, cash flow and 3-year averages from the annual reports) refresh automatically each night, about 400 stocks at a time, so every stock is re-checked weekly and new quarterly results flow in on their own. Source: Yahoo Finance (free, unofficial), or your Screener.in export. Shareholding (promoter, FII, DII, mutual funds, retail) comes from the quarterly filings on NSE: new filings are read every 30 minutes, older ones a batch each night.</p>
      <ErrorNote error={refresh.error} />
      {toast.node}
      <CsvModal open={csvOpen} onClose={() => setCsvOpen(false)} />
    </Panel>
  );
}

function CsvModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const toast = useToast();
  const up = useAct((fd: FormData) => api("/api/screener/import-csv", { form: fd }), ["/api/screener"]);
  const [file, setFile] = useState<File | null>(null);
  return (
    <Modal open={open} onClose={onClose} title="Import a Screener.in export">
      <p className="text-parch-dim">In Screener.in, run a screen (e.g. "Market capitalization &gt; 0"), make sure the <b>NSE Code</b> column is added, and click Export. Columns Syena understands: Current Price, Market Capitalization, EPS 12M, Book value, Return on equity, Debt to equity, Sales growth, Profit growth, OPM, Promoter holding, Dividend yield.</p>
      <input className="mt-3" type="file" accept=".csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      <ErrorNote error={up.error} />
      {toast.node}
      <div className="mt-4 flex gap-2">
        <button className="btn-primary" disabled={!file || up.isPending} onClick={() => {
          const fd = new FormData(); fd.append("file", file as File);
          up.mutate(fd, { onSuccess: (r: any) => { toast.show(`Imported ${r.imported} stocks`); onClose(); } });
        }}>Import</button>
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

function ScreenerHome() {
  const [tab, setTab] = useState("all");
  return (
    <Page title="Screener" intro="Every NSE stock with daily prices and ratios. Save screens to get Telegram alerts when stocks enter or leave them, and technical signals on the ones that pass.">
      <div className="space-y-6">
        <DataStatus />
        <Tabs tabs={[{ id: "all", label: "Screen stocks" }, { id: "screens", label: "My saved screens" }]} value={tab} onChange={setTab} />
        {tab === "screens" ? <Screens /> : <AllStocks />}
      </div>
    </Page>
  );
}

const COLS_KEY = "syena.screener.columns";
const DEFAULT_COLS = ["price", "pe", "market_cap", "dividend_yield", "profit_growth", "sales_growth", "roe", "roce_3y", "peg"];
const loadCols = (): string[] => { try { const v = JSON.parse(localStorage.getItem(COLS_KEY) ?? "null"); return Array.isArray(v) && v.length ? v : DEFAULT_COLS; } catch { return DEFAULT_COLS; } };

function AllStocks() {
  const [where, setWhere] = useState("");
  const [applied, setApplied] = useState("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("market_cap");
  const [desc, setDesc] = useState(true);
  const [page, setPage] = useState(0);
  const [view, setView] = useState("custom");
  const [custom, setCustom] = useState<string[]>(loadCols);
  const [picking, setPicking] = useState(false);
  const fields = useApi<any[]>("/api/screener/fields");
  const label = (k: string) => fields.data?.find((f: any) => f.key === k)?.label ?? k;
  const qs = new URLSearchParams({ where: applied, q, sort, desc: String(desc), limit: "100", offset: String(page * 100) });
  const { data, error, isLoading } = useApi<any>(`/api/screener/stocks?${qs}`);
  // like screener.in: fields used in the query are shown as columns too
  const used = (fields.data ?? []).map((f: any) => f.key).filter((k: string) => new RegExp(`(^|[^a-z_])${k}([^a-z_]|$)`).test(applied.toLowerCase()));
  const customCols = [stockCol, ...Array.from(new Set([...custom, ...used])).map((k) => num(k, label(k), k.includes("market_cap") || k.includes("cash_flow") ? 0 : 2, k.endsWith("_change") || k === "change"))];
  const cols = view === "custom" ? customCols : VIEWS[view].cols;
  const sortable = cols.map((c: any) => ({ ...c, label: <button className="hover:text-parch" onClick={() => { setSort(c.key === "symbol" ? "symbol" : c.key); setDesc(sort === c.key ? !desc : true); setPage(0); }}>{c.label}{sort === c.key ? (desc ? " ↓" : " ↑") : ""}</button> as any }));
  const saveCols = (v: string[]) => { setCustom(v); try { localStorage.setItem(COLS_KEY, JSON.stringify(v)); } catch { /* private mode */ } };
  const exportCsv = async () => {
    const all = await api<any>(`/api/screener/stocks?${new URLSearchParams({ where: applied, q, sort, desc: String(desc), limit: "500", offset: "0" })}`);
    const keys = ["symbol", "name", ...cols.filter((c: any) => c.key !== "symbol").map((c: any) => c.key)];
    const esc = (v: any) => (v == null ? "" : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
    const csv = [keys.map((k) => (k === "symbol" || k === "name" ? k : label(k))).join(","), ...all.rows.map((r: any) => keys.map((k) => esc(r[k])).join(","))].join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const a = document.createElement("a"); a.href = url; a.download = "syena-screen.csv"; a.click(); URL.revokeObjectURL(url);
  };
  const run = () => { setApplied(where); setPage(0); };
  return (
    <Panel title="Create a stock screen" aside={<span className="text-[12px] text-mute">every NSE stock · same language as saved screens</span>}>
      <form className="mb-4 grid gap-3 lg:grid-cols-[1fr_260px]" onSubmit={(e) => { e.preventDefault(); run(); }}>
        <div>
          <QueryBox value={where} onChange={setWhere} onRun={run} placeholder={"Market Capitalization > 500 AND\nPrice to earning < 15 AND\nReturn on capital employed > 22%"} />
          <p className="mt-1 text-[12px] text-mute">Type a field and pick it from the list (Tab or Enter). Combine with AND / OR, brackets allowed. Ctrl+Enter runs. Examples: <button type="button" className="text-saffron hover:underline" onClick={() => setWhere(examples[1])}>value</button> · <button type="button" className="text-saffron hover:underline" onClick={() => setWhere("roce_3y > 20 and sales_cagr_3y > 15 and debt_to_equity < 0.5 and peg < 1.5")}>quality growth</button> · <button type="button" className="text-saffron hover:underline" onClick={() => setWhere("fii_change > 0.5 and dii_change > 0 and market_cap > 2000")}>institutions buying</button> · <button type="button" className="text-saffron hover:underline" onClick={() => setWhere("loss_making = 1 and sales_growth > 30")}>loss-making but growing</button></p>
        </div>
        <div className="space-y-2">
          <input className="input" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} placeholder="Search name or symbol" aria-label="Search" />
          <button className="btn-primary w-full">Run this query</button>
          {applied && <button type="button" className="btn-ghost w-full" onClick={() => { setWhere(""); setApplied(""); }}>Clear</button>}
        </div>
      </form>
      <ErrorNote error={error} />
      {isLoading ? <Loading /> : data && (<>
        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          {[["custom", "My columns"], ...Object.entries(VIEWS).map(([k, v]) => [k, v.label])].map(([k, lbl]) => (
            <button key={k} type="button" onClick={() => setView(k)}
              className={`rounded-md px-2.5 py-1 text-[13px] ${view === k ? "bg-saffron/15 text-saffron" : "text-mute hover:text-parch"}`}>{lbl}</button>))}
          <span className="ml-auto flex gap-2">
            {view === "custom" && <button type="button" className="btn-ghost py-1 text-[13px]" onClick={() => setPicking(!picking)}>Edit columns</button>}
            <button type="button" className="btn-ghost py-1 text-[13px]" onClick={exportCsv}>Export CSV</button>
          </span>
        </div>
        {picking && view === "custom" && (
          <div className="mb-3 rounded-lg border border-dusk-line bg-dusk/50 p-3">
            <div className="mb-2 flex items-center justify-between text-[13px] text-mute"><span>Pick the columns to show</span><button type="button" className="text-saffron hover:underline" onClick={() => saveCols(DEFAULT_COLS)}>reset</button></div>
            <div className="grid gap-1 sm:grid-cols-2 lg:grid-cols-4">{(fields.data ?? []).map((f: any) => (
              <label key={f.key} className="flex items-center gap-2 text-[13px] text-parch-dim">
                <input type="checkbox" checked={custom.includes(f.key)} onChange={(e) => saveCols(e.target.checked ? [...custom, f.key] : custom.filter((x) => x !== f.key))} />{f.label}</label>))}</div>
          </div>)}
        <p className="mb-2 text-sm text-mute">{n(data.total, 0)} results{applied ? " for your query" : ""}{data.day ? ` · prices of ${day(data.day)}` : " · prices not loaded yet: click 'Update prices now'"} · click a name for its company page</p>
        <Table cols={sortable} rows={data.rows} rowKey={(r: any) => r.symbol} />
        <div className="mt-3 flex items-center gap-2">
          <button className="btn-ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="text-[13px] text-mute">page {page + 1} of {Math.max(1, Math.ceil(data.total / 100))}</span>
          <button className="btn-ghost" disabled={(page + 1) * 100 >= data.total} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      </>)}
    </Panel>
  );
}

const blank = { name: "", conditions: "", signal_text: "", alerts: true, algo_id: "", exit_on_removal: false };

function ScreenForm({ initial, onDone, id }: { initial?: any; onDone: (s: any) => void; id?: number }) {
  const [f, setF] = useState(initial ?? blank);
  const algos = useApi<any[]>("/api/algos");
  const body = () => ({ ...f, signal_text: f.signal_text || null, algo_id: f.algo_id ? Number(f.algo_id) : null });
  const preview = useAct((b: any) => api("/api/screener/preview", { body: b }));
  const save = useAct((b: any) => api(id ? `/api/screener/screens/${id}` : "/api/screener/screens", { method: id ? "PUT" : "POST", body: b }), ["/api/screener"]);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(body(), { onSuccess: onDone }); };
  const p: any = preview.data;
  return (
    <form onSubmit={submit} className="space-y-3">
      <Field label="Name"><input className="input" required value={f.name} onChange={set("name")} placeholder="Quality growth" /></Field>
      <Field label="Conditions" hint={<>Fields: P/E, P/B, market cap (₹ cr), ROE, ROA, debt to equity, sales growth, profit growth, OPM, net margin, dividend yield, earnings yield, EV/EBITDA, price, % from 52 week high; 3-year: sales CAGR, profit CAGR, EPS CAGR, 3 year ROE, 3 year ROCE, 3 year OPM; cash: free cash flow (₹ cr), FCF yield, operating cash flow, 3 year free cash flow; ownership: promoter / FII / DII / MF / retail holding and FII change, DII change, promoter change (last quarter, points)… e.g. <i>{examples[0]}</i></>}>
        <textarea className="input min-h-[80px]" required value={f.conditions} onChange={set("conditions")} />
      </Field>
      <Field label="Technical signal on the stocks that pass (optional)" hint="Same language as algos, e.g. “RSI 14 crosses above 50 and close > EMA 50”. Checked daily after close.">
        <input className="input" value={f.signal_text} onChange={set("signal_text")} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <label className="flex items-center gap-2"><input type="checkbox" checked={f.alerts} onChange={set("alerts")} /> Telegram alerts</label>
        <Field label="Keep this algo's stock list = screen (optional)">
          <select className="input" value={f.algo_id} onChange={set("algo_id")}><option value="">none</option>
            {(algos.data ?? []).filter((a: any) => a.kind === "rule").map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}</select>
        </Field>
      </div>
      {f.algo_id && <label className="flex items-center gap-2"><input type="checkbox" checked={f.exit_on_removal} onChange={set("exit_on_removal")} /> Sell the algo's position when a stock leaves this screen</label>}
      <ErrorNote error={preview.error || save.error} />
      {p && <p className="text-sm text-parch-dim">{p.readable}: <b>{p.count}</b> stocks pass today{p.missing_data ? ` (${p.missing_data} skipped: data missing)` : ""}{p.rows.length ? `: ${p.rows.slice(0, 12).map((r: any) => r.symbol).join(", ")}${p.count > 12 ? " …" : ""}` : ""}.</p>}
      <div className="flex gap-2">
        <button type="button" className="btn-ghost" onClick={() => preview.mutate(body())}>Preview</button>
        <button className="btn-primary" disabled={save.isPending}>{id ? "Save" : "Create screen"}</button>
      </div>
    </form>
  );
}

function Screens() {
  const nav = useNavigate();
  const { data, error, isLoading } = useApi<any[]>("/api/screener/screens");
  const [open, setOpen] = useState(false);
  return (
    <Panel title="My screens" aside={<button className="btn-primary" onClick={() => setOpen(true)}>New screen</button>}>
      <ErrorNote error={error} />
      {isLoading ? <Loading /> : !data?.length ? (
        <Empty title="No screens yet">Create one, e.g. <i>{examples[0]}</i>. Syena runs it every trading day after the close and tells you on Telegram which stocks joined or left.</Empty>
      ) : (
        <Table rows={data} onRow={(r: any) => nav(`/screener/${r.id}`)} rowKey={(r: any) => r.id} cols={[
          { key: "name", label: "Screen", render: (r: any) => <b>{r.name}</b> },
          { key: "readable", label: "Conditions", className: "whitespace-normal", render: (r: any) => <span className="text-parch-dim">{r.readable}</span> },
          { key: "n_members", label: "Stocks", align: "right" },
          { key: "signal_text", label: "Signal", render: (r: any) => r.signal_text ? <Pill tone="gain">on</Pill> : "–" },
          { key: "last_run_at", label: "Last run", render: (r: any) => r.last_run_at ? when(r.last_run_at) : "not yet" },
        ]} />
      )}
      <Modal open={open} onClose={() => setOpen(false)} title="New screen" wide>
        <ScreenForm onDone={(s: any) => { setOpen(false); nav(`/screener/${s.id}`); }} />
      </Modal>
    </Panel>
  );
}

function ScreenDetail({ id }: { id: number }) {
  const toast = useToast();
  const nav = useNavigate();
  const { data, error, isLoading } = useApi<any>(`/api/screener/screens/${id}`);
  const run = useAct(() => api(`/api/screener/screens/${id}/run`, { method: "POST" }), ["/api/screener"]);
  const del = useAct(() => api(`/api/screener/screens/${id}`, { method: "DELETE" }), ["/api/screener"]);
  const [edit, setEdit] = useState(false);
  if (isLoading) return <Page title="Screen"><Loading /></Page>;
  if (error || !data) return <Page title="Screen"><ErrorNote error={error} /></Page>;
  const s = data.screen;
  return (
    <Page title={s.name} intro={<Link to="/screener" className="text-saffron hover:underline">All screens</Link>} actions={<>
      <button className="btn-primary" disabled={run.isPending} onClick={() => run.mutate(undefined, { onSuccess: (r: any) => toast.show(`${r.members.length} stocks pass`) })}>{run.isPending ? "Running…" : "Run now"}</button>
      <button className="btn-ghost" onClick={() => setEdit(true)}>Edit</button>
      <button className="btn-ghost text-loss" onClick={() => del.mutate(undefined, { onSuccess: () => nav("/screener") })}>Delete</button></>}>
      <ErrorNote error={run.error || del.error} />
      {toast.node}
      <div className="space-y-6">
        <Panel title="Rules">
          <p>{s.readable}</p>
          {s.signal_text && <p className="mt-2 text-parch-dim">Technical signal: “{s.signal_text}”</p>}
          <p className="mt-2 text-sm text-mute">{s.alerts ? "Telegram alerts on." : "Alerts off."}{s.algo_id ? <> Linked <Link className="text-saffron hover:underline" to={`/algos/${s.algo_id}`}>algo #{s.algo_id}</Link> trades exactly these stocks{s.exit_on_removal ? " and sells a stock when it leaves the screen" : ""}.</> : ""}</p>
        </Panel>
        {!s.algo_id && <TradeScreen id={id} members={data.members.length} />}
        <Panel title={`Stocks passing (${data.members.length})`} pad={false}>
          <Table cols={cols} rows={data.members} rowKey={(r: any) => r.symbol} empty={<span className="text-parch-dim">{data.data_note ?? "No stocks pass these conditions today."}</span>} />
        </Panel>
        <Panel title="Daily changes">
          {!data.runs.length ? <p className="text-parch-dim">No runs yet.</p> : (
            <ul className="space-y-2">{data.runs.map((r: any) => (
              <li key={r.id} className="border-b border-dusk-line pb-2">
                <span className="text-mute">{day(r.day)}</span> · {r.members.length} stocks
                {r.added.length > 0 && <div className="text-gain">➕ {r.added.join(", ")}</div>}
                {r.removed.length > 0 && <div className="text-loss">➖ {r.removed.join(", ")}</div>}
                {r.signals.length > 0 && <div>📈 {r.signals.map((x: any) => `${x.symbol} (${money(x.close)})`).join(", ")}</div>}
              </li>))}</ul>
          )}
        </Panel>
      </div>
      <Modal open={edit} onClose={() => setEdit(false)} title="Edit screen" wide>
        <ScreenForm id={id} initial={{ name: s.name, conditions: s.conditions, signal_text: s.signal_text ?? "", alerts: s.alerts, algo_id: s.algo_id ?? "", exit_on_removal: !!s.exit_on_removal }} onDone={() => setEdit(false)} />
      </Modal>
    </Page>
  );
}

function TradeScreen({ id, members }: { id: number; members: number }) {
  const nav = useNavigate();
  const [f, setF] = useState({ text: "buy when RSI 14 crosses above 50 and close > EMA 50, exit when close < EMA 20", style: "SWING", timeframe: "day", exit_on_removal: true });
  const make = useAct((b: any) => api(`/api/screener/screens/${id}/algo`, { body: b }), ["/api/screener", "/api/algos"]);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <Panel title="Trade this screen">
      <p className="mb-3 text-sm text-parch-dim">Fundamentals pick the stocks, a technical rule picks the moment. This creates a paper algo that watches only the {members} stocks passing this screen (biggest 100 if more), updated after every daily run. It starts as a draft: you'll confirm the rule, backtest and start it on the Algos page.</p>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); make.mutate(f, { onSuccess: (r: any) => nav(`/algos/${r.algo_id}`) }); }}>
        <Field label="When to buy and sell" hint="Same language as algos. Stop-loss and target come from the rule or the style's defaults.">
          <textarea className="input min-h-[64px]" required value={f.text} onChange={set("text")} />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Style"><select className="input" value={f.style} onChange={set("style")}><option value="SWING">Swing</option><option value="LONG_TERM">Long-term</option></select></Field>
          <Field label="Candles"><select className="input" value={f.timeframe} onChange={set("timeframe")}><option value="day">Daily</option><option value="60minute">1 hour</option><option value="30minute">30 min</option><option value="15minute">15 min</option></select></Field>
        </div>
        <label className="flex items-center gap-2"><input type="checkbox" checked={f.exit_on_removal} onChange={set("exit_on_removal")} /> Sell a position when its stock leaves the screen</label>
        <ErrorNote error={make.error} />
        <button className="btn-primary" disabled={make.isPending || !members}>{make.isPending ? "Creating…" : "Create algo"}</button>
      </form>
    </Panel>
  );
}
