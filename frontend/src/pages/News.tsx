import { Globe2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import AiVerdict from "../components/AiVerdict";
import StockLink from "../components/StockLink";
import { Empty, ErrorNote, Loading, Page, Pill, useToast } from "../components/ui";
import { api } from "../lib/api";
import { signed, tone, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const CHECK_NAMES: Record<string, string> = {
  confidence: "weighted score below your threshold", ai_verdict: "AI saw no clear edge (hold)", technicals: "technicals didn't agree", market_context: "weak market",
  second_opinion: "second AI disagreed", sizing: "position size vs share price", price: "no price", circuit_limit: "stock at circuit limit",
  max_daily_loss: "daily loss limit", max_open_positions: "max open positions", max_trades_per_day: "max trades per day",
  per_stock_exposure: "already holding enough of it", mode: "trading mode", not_halted: "trading halted", market_hours: "market closed",
  analysis: "analysis error", short_selling: "short selling off", intraday_cutoff: "after intraday cutoff",
  liquidity: "company too small / thinly traded", reward_risk: "target too close vs stop",
};

function Sources({ data, onPick }: { data?: any[]; onPick: (s: string) => void }) {
  const [open, setOpen] = useState(false);
  if (!data?.length) return null;
  const bad = data.filter((x) => x.health && !x.health.ok && x.enabled !== false);
  return (
    <section className="panel p-4" aria-label="News sources">
      <button type="button" className="flex w-full items-center justify-between text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <h2 className="font-display text-base font-semibold">Sources <span className="ml-2 text-sm font-normal text-mute">{data.length} · last 24 hours</span></h2>
        <span className="text-sm text-mute">{bad.length > 0 && <span className="mr-3 text-loss">{bad.length} failing</span>}{open ? "Hide" : "Show"}</span>
      </button>
      {open && <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr className="border-b border-dusk-line text-left text-[12px] text-mute">
            <th className="py-1.5 pr-3 font-medium">Source</th><th className="px-2 font-medium">Last fetch</th>
            <th className="px-2 text-right font-medium">Articles</th><th className="px-2 text-right font-medium">Analysed</th>
            <th className="px-2 text-right font-medium">Not relevant</th><th className="px-2 text-right font-medium">Too old</th><th className="px-2 text-right font-medium">Duplicate</th>
          </tr></thead>
          <tbody>{data.map((x) => {
            const st = x.status ?? {};
            const h = x.health;
            return (
              <tr key={x.name} className="border-b border-dusk-line/60">
                <td className="py-1.5 pr-3"><button type="button" className="hover:text-saffron hover:underline" onClick={() => onPick(x.name)}>{x.name}</button>{x.enabled === false && <span className="ml-2 text-[12px] text-mute">off</span>}</td>
                <td className="px-2">{!h ? <span className="text-mute">not fetched yet</span> : h.ok
                  ? <span className="text-gain">✓ <span className="text-parch-dim">{h.items} items</span></span>
                  : <span className="text-loss" title={h.error}>✕ failed <span className="text-[12px] text-mute">{(h.error ?? "").slice(0, 60)}</span></span>}</td>
                <td className="px-2 text-right tabular-nums">{x.total}</td>
                <td className="px-2 text-right tabular-nums">{(st.ANALYSED ?? 0) + (st.TRIAGED ?? 0)}</td>
                <td className="px-2 text-right tabular-nums text-parch-dim">{st.IRRELEVANT ?? 0}</td>
                <td className="px-2 text-right tabular-nums text-parch-dim">{st.STALE ?? 0}</td>
                <td className="px-2 text-right tabular-nums text-parch-dim">{st.DUPLICATE ?? 0}</td>
              </tr>);
          })}</tbody>
        </table>
        <p className="mt-2 text-[12px] text-mute">Articles older than {""}15 minutes when first seen are “too old”. A failing source can be switched off in Settings → News sources (add "enabled": false to it).</p>
      </div>}
    </section>
  );
}

function Funnel() {
  const [hours, setHours] = useState(24);
  const { data: f } = useApi<any>(`/api/news/funnel?hours=${hours}`, { refetchInterval: 60_000 });
  if (!f) return null;
  const st = f.status ?? {};
  const v = f.verdicts ?? {};
  const analysed = (v.BUY ?? 0) + (v.HOLD ?? 0) + (v.SELL ?? 0);
  const steps: [string, number, string][] = [
    ["Fetched", f.fetched, ""],
    ["Too old to trade", st.STALE ?? 0, "skipped"],
    ["Duplicates", st.DUPLICATE ?? 0, "skipped"],
    ["Not price-relevant", st.IRRELEVANT ?? 0, "skipped"],
    ["Analysed by AI", analysed, ""],
    ["AI said buy / sell", (v.BUY ?? 0) + (v.SELL ?? 0), ""],
    ["Trades placed", f.placed, ""],
  ];
  const blocked = Object.entries(f.blocked_by ?? {}) as [string, number][];
  const low = (f.buy_confidences ?? []).filter((c: number) => c < f.confidence_threshold).length;
  return (
    <section className="panel space-y-3 p-4" aria-label="Where the news went">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-display text-base font-semibold">Where the news went</h2>
        <select className="input w-auto py-1" value={hours} onChange={(e) => setHours(Number(e.target.value))} aria-label="Period">
          <option value={24}>last 24 hours</option><option value={72}>last 3 days</option><option value={168}>last 7 days</option></select>
      </div>
      <ol className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">{steps.map(([label, n, kind]) => (
        <li key={label} className="rounded-lg bg-dusk-raised px-3 py-2">
          <div className="text-[12px] text-mute">{label}</div>
          <div className={`font-display text-xl font-semibold ${kind ? "text-parch-dim" : ""}`}>{n}</div>
        </li>))}</ol>
      <div className="space-y-1 text-sm text-parch-dim">
        {analysed > 0 && <p>AI verdicts: <b className="text-gain">{v.BUY ?? 0} buy</b>, <b className="text-loss">{v.SELL ?? 0} sell (short)</b>, <b className="text-sky">{v.HOLD ?? 0} hold</b>. Outcome: <b className="text-gain">{f.placed - (f.shorts ?? 0)} buy</b>, <b className="text-loss">{f.shorts ?? 0} sell</b>, {f.rejected} rejected{f.errors ? <>, <b className="text-saffron">{f.errors} error</b></> : null}. An AI "hold" counts as rejected.</p>}
        {blocked.length > 0 && <p>Rejected at: {blocked.map(([k, n]) => `${CHECK_NAMES[k] ?? k.replace(/_/g, " ")} (${n})`).join(", ")}. Open a decision to see the exact reason.</p>}
        {low > 0 && <p>{low} of {f.buy_confidences.length} buy/sell signals had a weighted score (news 80% · technicals 15% · market 5%) under your threshold of {f.confidence_threshold} (scores: {f.buy_confidences.join(", ")}). Threshold and weights are in Settings → Trade gate.</p>}
        {(st.ERROR ?? 0) + (st.FAILED ?? 0) > 0 && <p className="text-loss">{(st.ERROR ?? 0) + (st.FAILED ?? 0)} articles failed to process (see Logs).</p>}
      </div>
    </section>
  );
}

const statusTone: Record<string, string> = { ANALYSED: "gain", TRIAGED: "info", IRRELEVANT: "mute", DUPLICATE: "mute", STALE: "mute", NEW: "warn", ERROR: "loss", FAILED: "loss" };

export default function News() {
  const [status, setStatus] = useState("");
  const [symbol, setSymbol] = useState("");
  const [source, setSource] = useState("");
  const toast = useToast();
  const q = `/api/news?limit=150${status ? `&status=${status}` : ""}${symbol ? `&symbol=${symbol}` : ""}${source ? `&source=${encodeURIComponent(source)}` : ""}`;
  const srcs = useApi<any[]>("/api/news/sources", { refetchInterval: 60_000 });
  const { data, error, isLoading } = useApi<any[]>(q, { refetchInterval: 60_000 });
  const run = useAct(() => api("/api/news/run", { method: "POST" }), ["/api/news", "/api/dashboard"]);
  const label = useAct((b: any) => api("/api/labels", { body: b }));
  const settings = useApi<any>("/api/settings");
  const threshold = settings.data?.values?.trade_gate?.confidence_threshold;

  return (
    <Page title="News feed" intro="Every article fetched, how it was mapped to stocks, what the AI concluded, and which trades it influenced."
      actions={<button className="btn-primary" disabled={run.isPending} onClick={() => run.mutate(undefined, { onSuccess: (s: any) => toast.show(`${s.fetched} new, ${s.relevant} relevant, ${s.signals} signals`) })}>
        {run.isPending ? "Fetching…" : "Fetch now"}</button>}>
      <Funnel />
      <Sources data={srcs.data} onPick={setSource} />
      <div className="flex flex-wrap gap-2">
        <select className="input w-auto" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source">
          <option value="">all sources</option><option value="rss">news sites (RSS)</option><option value="telegram">Telegram</option>
          <option value="NSE announcements">NSE filings</option>
          <optgroup label="One source">{(srcs.data ?? []).map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}</optgroup>
        </select>
        <select className="input w-auto" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          {["", "ANALYSED", "TRIAGED", "MACRO", "IRRELEVANT", "FILTERED", "STALE", "DUPLICATE", "NEW", "ERROR", "FAILED"].map((s) => <option key={s} value={s}>{s ? s.toLowerCase() : "all articles"}</option>)}
        </select>
        <input className="input w-32" placeholder="Symbol" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
      </div>
      <ErrorNote error={error || run.error} />
      {isLoading && <Loading />}
      {data?.length === 0 && <Empty title="No articles yet">The worker polls your feeds every few minutes (Settings, News). Zerodha must be connected so technicals can be computed.</Empty>}
      <ul className="space-y-3">
        {data?.map((n) => (
          <li key={n.id} className="panel p-4">
            <div className="flex flex-wrap items-center gap-2 text-[13px] text-mute">
              <Pill tone={statusTone[n.status]}>{n.status.toLowerCase()}</Pill>
              <span>{n.source}</span><span>{when(n.published_at)}</span>
              {n.event_type && <span>{n.event_type.replace(/_/g, " ")}</span>}
              {n.symbols.map((s: string) => <StockLink key={s} symbol={s} className="rounded-md bg-sky/15 px-2 py-0.5 text-[13px] font-medium text-sky" />)}
            </div>
            <a href={n.url} target="_blank" rel="noreferrer noopener" className="mt-1.5 block text-[17px] font-semibold leading-snug hover:underline">{n.title}</a>
            {n.triage_reason && <p className={`mt-1 text-sm ${n.triage_reason.startsWith("Error:") ? "text-loss" : "text-parch-dim"}`}>{n.triage_reason.startsWith("Error:") ? n.triage_reason : <>Filter: {n.triage_reason}</>}</p>}
            {n.macro && <MacroCard m={n.macro} />}
            {n.analyses.length > 0 && <div className="mt-3 space-y-2">{n.analyses.map((a: any, i: number) => <AiVerdict key={i} a={a} threshold={threshold} compact />)}</div>}
            {n.errors?.length > 0 && <ul className="mt-2 space-y-1">{n.errors.map((e: any) => (
              <li key={e.decision_id}><Link to={`/decisions/${e.decision_id}`} className="flex items-start gap-2 rounded-lg border border-loss/30 bg-loss-soft/50 px-3 py-2 text-sm hover:brightness-110">
                <span className="text-loss" aria-hidden>✕</span><span><b>{e.symbol}</b> <span className="text-parch-dim">{e.summary}</span></span></Link></li>))}</ul>}
            {n.trades.length > 0 && (
              <div className="mt-2 text-sm">Influenced: {n.trades.map((t: any) => (
                <span key={t.trade_id} className="mr-3">{t.stock} ({t.mode.toLowerCase()}, {t.status.toLowerCase()}{t.pnl != null ? <>, <span className={tone(t.pnl)}>{signed(t.pnl)}</span></> : null})</span>))}
                <Link to={`/decisions?symbol=${n.symbols[0] ?? ""}`} className="text-saffron hover:underline">decisions</Link>
              </div>
            )}
            <div className="mt-2 flex flex-wrap gap-2 text-[13px] text-mute">
              Your label for triage evals:
              <button className="hover:text-parch" onClick={() => label.mutate({ kind: "triage", ref_id: n.id, label: { relevant: true, symbols: n.symbols } }, { onSuccess: () => toast.show("Labelled relevant") })}>relevant</button>
              <button className="hover:text-parch" onClick={() => label.mutate({ kind: "triage", ref_id: n.id, label: { relevant: false, symbols: [] } }, { onSuccess: () => toast.show("Labelled not relevant") })}>not relevant</button>
            </div>
          </li>
        ))}
      </ul>
      {toast.node}
    </Page>
  );
}


/** Broad news linked to the stocks it helps or hurts (each stock is then judged by the normal analysis). */
function MacroCard({ m }: { m: any }) {
  return (
    <div className="mt-3 rounded-xl border border-sky/30 bg-sky-soft/40 p-3.5">
      <div className="flex items-center gap-2 text-sm font-semibold text-sky"><Globe2 size={16} />Broad news: who it affects</div>
      {m.summary && <p className="mt-1 text-sm text-parch-dim">{m.summary}</p>}
      <ul className="mt-2 space-y-1.5 text-sm">{(m.themes ?? []).map((t: any, i: number) => (
        <li key={i}><span className={t.direction === "negative" ? "text-loss" : "text-gain"}>{t.direction === "negative" ? "▼" : "▲"} {t.theme}</span>
          <span className="text-mute"> ({t.confidence}%)</span>: <span className="text-parch-dim">{t.why}</span>
          {t.industries?.length > 0 && <span className="block text-[12px] text-mute">Industries: {t.industries.join(", ")}</span>}</li>))}</ul>
      {m.stocks?.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{m.stocks.map((x: any) => (
        <StockLink key={x.symbol} symbol={x.symbol} className={`rounded-md px-2 py-0.5 text-[13px] font-medium ${x.direction === "negative" ? "bg-loss-soft text-loss" : "bg-gain-soft text-gain"}`} />))}
        <span className="self-center text-[12px] text-mute">each analysed below on its own merits</span></div>}
    </div>
  );
}
