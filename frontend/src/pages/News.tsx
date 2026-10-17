import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorNote, Loading, Page, Pill, useToast } from "../components/ui";
import { api } from "../lib/api";
import { signed, tone, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const statusTone: Record<string, string> = { ANALYSED: "gain", TRIAGED: "info", IRRELEVANT: "mute", DUPLICATE: "mute", NEW: "warn", ERROR: "loss" };

export default function News() {
  const [status, setStatus] = useState("");
  const [symbol, setSymbol] = useState("");
  const toast = useToast();
  const q = `/api/news?limit=150${status ? `&status=${status}` : ""}${symbol ? `&symbol=${symbol}` : ""}`;
  const { data, error, isLoading } = useApi<any[]>(q, { refetchInterval: 60_000 });
  const run = useAct(() => api("/api/news/run", { method: "POST" }), ["/api/news", "/api/dashboard"]);
  const label = useAct((b: any) => api("/api/labels", { body: b }));

  return (
    <Page title="News feed" intro="Every article fetched, how it was mapped to stocks, what the AI concluded, and which trades it influenced."
      actions={<button className="btn-primary" disabled={run.isPending} onClick={() => run.mutate(undefined, { onSuccess: (s: any) => toast.show(`${s.fetched} new, ${s.relevant} relevant, ${s.signals} signals`) })}>
        {run.isPending ? "Fetching…" : "Fetch now"}</button>}>
      <div className="flex flex-wrap gap-2">
        <select className="input w-auto" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          {["", "ANALYSED", "TRIAGED", "IRRELEVANT", "DUPLICATE", "NEW", "ERROR"].map((s) => <option key={s} value={s}>{s ? s.toLowerCase() : "all articles"}</option>)}
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
              {n.symbols.map((s: string) => <Pill key={s} tone="info">{s}</Pill>)}
            </div>
            <a href={n.url} target="_blank" rel="noreferrer noopener" className="mt-1.5 block text-[17px] font-semibold leading-snug hover:underline">{n.title}</a>
            {n.triage_reason && <p className="mt-1 text-sm text-parch-dim">Triage: {n.triage_reason}</p>}
            {n.analyses.map((a: any, i: number) => (
              <div key={i} className="mt-2 rounded-lg bg-dusk p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <b>{a.symbol}</b>
                  {a.action && <Pill tone={a.action === "BUY" ? "gain" : a.action === "SELL" ? "loss" : "mute"}>{a.action.toLowerCase()} {a.confidence}</Pill>}
                  {a.sentiment != null && <span className={tone(a.sentiment)}>sentiment {a.sentiment > 0 ? "+" : ""}{Number(a.sentiment).toFixed(2)}</span>}
                  <span className="text-mute">{a.model}</span>
                </div>
                {a.summary && <p className="mt-1 text-parch-dim">{a.summary}</p>}
              </div>
            ))}
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
