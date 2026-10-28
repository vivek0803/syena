import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AiVerdict from "../components/AiVerdict";
import TvChart from "../components/TvChart";
import { Empty, ErrorNote, Loading, Page, Panel, Pill, Tabs, decisionLabel, decisionTone } from "../components/ui";
import { api } from "../lib/api";
import { day, signed, tone, when } from "../lib/format";
import { useApi } from "../lib/hooks";

const RANGES = ["1D", "5D", "1M", "3M", "6M", "1Y", "3Y", "5Y"];
// candle size -> longest range it can show (more candles than that is slow to load and draw)
const CANDLES: [string, string, string][] = [["minute", "1m", "1M"], ["2minute", "2m", "1M"], ["5minute", "5m", "3M"], ["15minute", "15m", "6M"], ["60minute", "1h", "1Y"], ["240minute", "4h", "1Y"], ["day", "1D", "5Y"]];
const AUTO: Record<string, string> = { "1D": "5minute", "5D": "15minute", "1M": "60minute", "3M": "day", "6M": "day", "1Y": "day", "3Y": "day", "5Y": "day" };
const fits = (iv: string, range: string) => RANGES.indexOf(range) <= RANGES.indexOf(CANDLES.find((c) => c[0] === iv)![2]);
const IST = 19800; // chart axis in Indian time
const n = (v: any, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));

export default function Stock() {
  const { symbol } = useParams();
  if (!symbol) return <StockSearch />;
  return <StockView key={symbol} symbol={symbol.toUpperCase()} />;
}

function StockSearch() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<any[]>([]);
  useEffect(() => {
    if (!q.trim()) { setHits([]); return; }
    const ctl = new AbortController();
    const t = setTimeout(() => api<any[]>(`/api/screener/search?q=${encodeURIComponent(q.trim())}`, { signal: ctl.signal }).then(setHits).catch(() => {}), 150);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [q]);
  return (
    <Page title="Stock charts" intro="Open any NSE stock: price chart, company figures, the AI's news verdicts and Syena's trades in it.">
      <Panel>
        <form onSubmit={(e) => { e.preventDefault(); const s = hits[0]?.symbol ?? q.trim().toUpperCase(); if (s) nav(`/stock/${s}`); }}>
          <input className="input text-lg" autoFocus placeholder="Type a symbol or company name, e.g. RELIANCE or Infosys" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search stocks" />
        </form>
        {hits.length > 0 && <ul className="mt-3 divide-y divide-dusk-line">{hits.map((h) => (
          <li key={h.symbol}><Link to={`/stock/${h.symbol}`} className="flex gap-3 px-1 py-2 hover:bg-dusk-raised/50"><b>{h.symbol}</b><span className="text-mute">{h.name}</span></Link></li>))}</ul>}
      </Panel>
    </Page>
  );
}

function StockView({ symbol }: { symbol: string }) {
  const [range, setRange] = useState(() => { try { return localStorage.getItem("syena.chartRange") || "1Y"; } catch { return "1Y"; } });
  useEffect(() => { try { localStorage.setItem("syena.chartRange", range); } catch { /* private mode */ } }, [range]);
  const info = useApi<any>(`/api/stock/${symbol}`);
  const [interval, setIntervalRaw] = useState<string>(() => { try { return localStorage.getItem("syena.chartInterval") || ""; } catch { return ""; } });
  const setInterval = (v: string) => { setIntervalRaw(v); try { localStorage.setItem("syena.chartInterval", v); } catch { /* private mode */ } };
  const iv = interval && fits(interval, range) ? interval : AUTO[range];  // like TradingView: a range too long for the candle falls back
  const chart = useApi<any>(`/api/stock/${symbol}/chart?range=${range}&interval=${iv}`, { staleTime: 60_000 } as any);
  const thr = useApi<any>("/api/settings");
  const m = info.data?.metrics;
  const last = chart.data?.candles?.at(-1);
  const first = chart.data?.candles?.[0];
  const chg = last && first ? (last.c / first.o - 1) * 100 : null;
  const threshold = thr.data?.values?.trade_gate?.confidence_threshold;

  return (
    <Page title={symbol} intro={m ? <span>{m.name ?? ""}{m.sector ? <span className="text-mute"> · {m.sector}</span> : null}</span> : undefined}
      actions={<div className="flex flex-wrap gap-2"><Link to={`/company/${symbol}`} className="btn-ghost">Financials</Link><Link to={`/ask?q=${encodeURIComponent(`What does the data say about ${symbol} right now?`)}`} className="btn-ghost">Ask Syena AI about {symbol}</Link><Link to="/stock" className="btn-ghost">Another stock</Link></div>}>
      <ErrorNote error={info.error} />
      <div className="space-y-6">
        <Panel pad={false}>
          <div className="flex flex-wrap items-end justify-between gap-3 px-4 pt-4">
            <div>
              <div className="font-display text-3xl font-semibold tabular-nums">{last ? `₹${n(last.c)}` : m?.price ? `₹${n(m.price)}` : "–"}</div>
              {chg != null && <div className={`text-sm ${tone(chg)}`}>{chg > 0 ? "+" : ""}{chg.toFixed(2)}% over {range}</div>}
              <div className="text-[12px] text-mute">{CANDLES.find((c) => c[0] === iv)?.[1]} candles{!interval ? " (auto)" : ""}</div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <div role="group" aria-label="Candle size" className="flex rounded-lg bg-dusk p-1">
                <button type="button" onClick={() => setInterval("")} aria-pressed={!interval}
                  className={`rounded-md px-2.5 py-1.5 text-sm font-medium ${!interval ? "bg-dusk-raised text-parch" : "text-mute hover:text-parch"}`} title="Pick the candle size for the range">Auto</button>
                {CANDLES.map(([k, label, max]) => {
                  const ok = fits(k, range);
                  return <button key={k} type="button" disabled={!ok} onClick={() => setInterval(k)} aria-pressed={iv === k && !!interval}
                    title={ok ? `${label} candles` : `${label} candles go back at most ${max}`}
                    className={`rounded-md px-2.5 py-1.5 text-sm font-medium ${iv === k && interval ? "bg-dusk-raised text-parch" : ok ? "text-mute hover:text-parch" : "cursor-not-allowed text-mute/40"}`}>{label}</button>;
                })}
              </div>
              <Tabs value={range} onChange={setRange} tabs={RANGES.map((r) => ({ id: r, label: r }))} />
            </div>
          </div>
          <ErrorNote error={chart.error} />
          {chart.isLoading ? <div className="h-[460px]"><Loading what="Loading prices" /></div>
            : !chart.data?.candles?.length ? <div className="p-6"><Empty title="No price data">Angel One returned no candles for this range (market holiday, or a newly listed stock).</Empty></div>
            : <TvChart data={chart.data} trades={info.data?.trades ?? []} symbol={symbol} />}
          {chart.data?.corporate_actions?.length > 0 && <p className="px-4 pb-3 text-[12px] text-mute">
            {chart.data.corporate_actions.map((a: any) => a.adjusted ? `${a.label} on ${a.date}: earlier prices adjusted so the chart is continuous`
              : `${a.label} on ${a.date}: not adjusted, check it`).join(" · ")}</p>}
        </Panel>

        <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
          <div className="space-y-6">
            <Panel title="AI verdicts on news" aside={<span className="text-[13px] text-mute">latest first</span>}>
              {info.isLoading ? <Loading /> : !info.data?.analyses?.length ? <p className="text-parch-dim">No news about {symbol} has been analysed yet.</p> : (
                <div className="space-y-3">{info.data.analyses.map((a: any) => <AiVerdict key={a.id} a={a} threshold={threshold} compact />)}</div>)}
            </Panel>
            <Panel title="Syena's decisions">
              {!info.data?.decisions?.length ? <p className="text-parch-dim">No decisions on {symbol} yet.</p> : (
                <ul className="divide-y divide-dusk-line">{info.data.decisions.map((d: any) => (
                  <li key={d.id}><Link to={`/decisions/${d.id}`} className="flex items-start gap-3 py-2 hover:bg-dusk-raised/40">
                    <Pill tone={decisionTone(d.type, d.side)}>{decisionLabel(d)}</Pill>
                    <span className="flex-1 text-sm text-parch-dim">{d.summary}</span>
                    <span className="whitespace-nowrap text-[12px] text-mute">{when(d.ts)}</span>
                  </Link></li>))}</ul>)}
            </Panel>
          </div>
          <div className="space-y-6">
            <Panel title="Key figures"><Figures m={m} rsi={chart.data?.rsi?.at(-1)?.v} /></Panel>
            <Ownership m={m} history={info.data?.shareholding ?? []} />
            <LevelsPanel data={chart.data} label={CANDLES.find((c) => c[0] === iv)?.[1] ?? ""} />
            <ZoneSetupPanel data={chart.data} label={CANDLES.find((c) => c[0] === iv)?.[1] ?? ""} />
            <VerifyPanel symbol={symbol} />
            <PatternPanel data={chart.data} label={CANDLES.find((c) => c[0] === iv)?.[1] ?? ""} />
            {info.data?.trades?.length > 0 && <Panel title="Trades">
              <ul className="space-y-2 text-sm">{info.data.trades.slice(0, 12).map((t: any) => (
                <li key={t.id} className="flex flex-wrap items-center gap-2">
                  <Pill tone={t.side === "SELL" ? "loss" : "gain"}>{t.side === "SELL" ? "short" : "long"}</Pill>
                  <span className="text-parch-dim">{t.qty} @ ₹{n(t.entry_price)}{t.exit_price_executed ? ` → ₹${n(t.exit_price_executed)}` : ""}</span>
                  {t.profit_loss != null && <span className={tone(t.profit_loss)}>{signed(t.profit_loss)}</span>}
                  <span className="ml-auto text-[12px] text-mute">{t.mode.toLowerCase()} · {t.status.toLowerCase()}</span>
                </li>))}</ul>
            </Panel>}
          </div>
        </div>
      </div>
    </Page>
  );
}

function LevelsPanel({ data, label }: { data: any; label: string }) {
  const lv = data?.levels;
  if (!lv) return null;
  const price = data.candles?.at(-1)?.c;
  const row = (z: any, i: number, kind: string) => (
    <li key={kind + i} className="py-1.5">
      <div className="flex items-baseline gap-2">
        <span className={`w-7 font-semibold ${kind === "R" ? "text-loss" : "text-gain"}`}>{kind}{i + 1}</span>
        <span className="tabular-nums">₹{n(z.low)} – {n(z.high)}</span>
        <span className={`ml-auto text-[12px] ${z.strength === "strong" ? "text-parch" : "text-mute"}`}>{z.strength}</span>
        <span className="w-14 text-right text-[12px] tabular-nums text-mute">{z.distance_pct > 0 ? "+" : ""}{z.distance_pct}%</span>
      </div>
      <p className="pl-9 text-[12px] text-mute">{z.why}</p>
    </li>);
  return (
    <Panel title="Support & resistance" aside={<span className="text-[12px] text-mute">{label} candles</span>}>
      {lv.trend && <p className="mb-2 text-sm"><span className="text-mute">Market structure:</span> <b className={lv.trend === "uptrend" ? "text-gain" : lv.trend === "downtrend" ? "text-loss" : "text-parch"}>{lv.trend}</b>
        {lv.break && <span className="text-parch-dim"> · {lv.break.direction} break through ₹{n(lv.break.level)}</span>}</p>}
      <ul className="divide-y divide-dusk-line text-sm">
        {[...(lv.resistances ?? [])].reverse().map((z: any, i: number, a: any[]) => row(z, a.length - 1 - i, "R"))}
        <li className="py-1.5 text-[12px] text-saffron">Price now ₹{n(price)}</li>
        {(lv.supports ?? []).map((z: any, i: number) => row(z, i, "S"))}
      </ul>
      {!lv.resistances?.length && !lv.supports?.length && <p className="text-sm text-parch-dim">Not enough swings on these candles to find zones: try a longer range.</p>}
      <p className="mt-3 text-[12px] text-mute">Zones are where several swing highs/lows cluster. Stronger with more touches, bigger bounces, a support-resistance flip and a matching level on the {data.interval === "day" ? "weekly" : "daily"} chart. HH/HL = higher high/low, LH/LL = lower, EH/EL = about equal. In algos: “buy when price bounces off support”, “buy when close breaks above resistance”.</p>
    </Panel>
  );
}

function ZoneSetupPanel({ data, label }: { data: any; label: string }) {
  const z = data?.zone_setup;
  if (!z?.available) return null;
  const bull = z.bias?.startsWith("bullish"), htfBull = z.htf_bias?.startsWith("bullish");
  return (
    <Panel title="Zone confluence setup" aside={<span className="text-[12px] text-mute">{label} candles</span>}>
      <div className="space-y-1 text-sm">
        <p><span className="text-mute">Bias:</span> <b className={bull ? "text-gain" : z.bias?.startsWith("bearish") ? "text-loss" : "text-parch"}>{z.bias}</b></p>
        {z.htf_bias && <p><span className="text-mute">Higher timeframe:</span> <span className={htfBull ? "text-gain" : z.htf_bias.startsWith("bearish") ? "text-loss" : "text-parch-dim"}>{z.htf_bias}</span></p>}
      </div>
      {z.fired_today ? (
        <div className="mt-3 rounded-lg border border-gain/40 bg-gain-soft/20 p-3 text-sm">
          <b className="text-gain">Setup on the latest candle</b>
          <p className="mt-1 text-parch-dim">Score {z.score}/100 · stop ₹{n(z.stop)} · target ₹{n(z.target)} · 1:{z.rr}</p>
          <p className="mt-1 text-[12px] text-mute">{z.why}</p>
        </div>
      ) : z.waiting_zones?.length ? (
        <div className="mt-3 text-sm">
          <p className="text-mute">Demand zones it waits for price to return to:</p>
          <ul className="mt-1 space-y-1">{z.waiting_zones.map((w: any, i: number) => (
            <li key={i} className="flex items-baseline gap-2">
              <span className="tabular-nums">₹{n(w.low)} – {n(w.high)}</span>
              <span className="text-[12px] text-mute">{w.kind === "flip" ? "old resistance, now support" : w.kind === "origin" ? "where a strong move began" : `support zone · ${w.touches} touches`}</span>
              <span className="ml-auto text-[12px] tabular-nums text-mute">{w.distance_pct}%</span>
            </li>))}</ul>
        </div>
      ) : <p className="mt-3 text-sm text-parch-dim">No fresh demand zone below price right now.</p>}
      {z.recent?.length > 0 && (
        <details className="mt-3 text-[13px]">
          <summary className="cursor-pointer text-parch-dim">Last {z.recent.length} setups on this chart</summary>
          <ul className="mt-2 space-y-2">{[...z.recent].reverse().map((r: any) => (
            <li key={r.ts}><span className="text-parch">{when(r.ts)}</span> <span className="text-mute">score {r.score} · stop ₹{n(r.stop)} · target ₹{n(r.target)} · 1:{r.rr}</span>
              <p className="text-[12px] text-mute">{r.why}</p></li>))}</ul>
        </details>)}
      <p className="mt-3 text-[12px] text-mute">Syena's swing setup: price back in a support zone with a bullish confirmation candle, scored out of 100 on trend, zone quality, confirmation, volume, a sweep of the lows and Fibonacci/RSI extras; trades from {z.min_score ?? 60}. Stop beyond the zone, target the next strong resistance, at least 1:2. Test it on the Pattern lab page.</p>
    </Panel>
  );
}

function VerifyPanel({ symbol }: { symbol: string }) {
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<any>(null);
  const run = () => { setBusy(true); setErr(null); api<any>(`/api/stock/${symbol}/verify?days=5`).then(setRes).catch(setErr).finally(() => setBusy(false)); };
  return (
    <Panel title="Are these prices right?">
      <p className="mb-2 text-[12px] text-mute">Compares the last 5 daily candles with NSE's official end-of-day file (bhavcopy), independent of the broker the chart comes from.</p>
      <button type="button" className="btn-ghost" onClick={run} disabled={busy}>{busy ? "Checking…" : res ? "Check again" : "Check against NSE"}</button>
      <ErrorNote error={err} />
      {res && (res.error ? <p className="mt-2 text-sm text-saffron">{res.error}</p> : (
        <div className="mt-3 text-sm">
          <p className={res.ok ? "text-gain" : "text-saffron"}>{res.ok ? `✓ All ${res.checked} days match NSE` : `${res.matched} of ${res.checked} days match NSE`}</p>
          <ul className="mt-1 space-y-1">{res.days.map((d: any) => (
            <li key={d.date} className="flex gap-2 text-[13px]">
              <span className="w-24 tabular-nums text-mute">{d.date}</span>
              <span className={d.status === "match" ? "text-gain" : d.status === "mismatch" ? "text-loss" : "text-mute"}>{d.status === "match" ? "match" : d.status.replace(/_/g, " ")}</span>
              {d.detail && <span className="flex-1 text-[12px] text-mute">{d.detail}</span>}
            </li>))}</ul>
          <p className="mt-2 text-[12px] text-mute">Allowed difference {res.tolerance_pct}%. NSE's “close” is the average of the last 30 minutes, so a close matching the last trade price also counts.</p>
        </div>))}
    </Panel>
  );
}

function PatternPanel({ data, label }: { data: any; label: string }) {
  const [dir, setDir] = useState<"all" | "bullish" | "bearish">("all");
  if (!data) return null;
  const sum = data.month_summary;
  const list = [...(data.recent_patterns ?? [])].reverse().filter((p: any) => dir === "all" || p.direction === dir);
  const when_ = (t: number) => data.interval === "day" ? day(new Date(t * 1000).toISOString()) : new Date((t + IST) * 1000).toISOString().slice(5, 16).replace("T", " ");
  return (
    <Panel title="Candle patterns" aside={<span className="text-[12px] text-mute">{label} candles · last month</span>}>
      {sum?.available && (
        <div className="mb-3 space-y-1 text-sm">
          <p><span className="text-gain">▲ {sum.bullish} bullish</span> · <span className="text-loss">▼ {sum.bearish} bearish</span> · <span className="text-parch-dim">bias {sum.bias}</span></p>
          {sum.most_common?.length > 0 && <p className="text-[12px] text-mute">Most frequent: {sum.most_common.map(([k, v]: [string, number]) => `${k} ×${v}`).join(", ")}</p>}
          <p className="text-[12px] text-mute">Price is {sum.trendline}.</p>
        </div>
      )}
      <div role="group" aria-label="Filter patterns" className="mb-2 flex items-center gap-1 text-[12px]">
        {(["all", "bullish", "bearish"] as const).map((d) => (
          <button key={d} type="button" onClick={() => setDir(d)} aria-pressed={dir === d}
            className={`rounded-md px-2 py-0.5 ${dir === d ? "bg-dusk-raised text-parch" : "text-mute hover:text-parch"}`}>{d}</button>))}
        <span className="ml-auto text-mute">indecision candles hidden</span>
      </div>
      {!list.length ? <p className="text-sm text-parch-dim">No {dir === "all" ? "" : dir + " "}patterns in the last month on {label} candles.</p> : (
        <ul className="max-h-72 space-y-1.5 overflow-y-auto pr-1 text-sm">{list.map((p: any, i: number) => (
          <li key={i} className="flex items-center gap-2">
            <span className={p.direction === "bullish" ? "text-gain" : p.direction === "bearish" ? "text-loss" : "text-mute"} aria-hidden>{p.direction === "bullish" ? "▲" : p.direction === "bearish" ? "▼" : "■"}</span>
            <span>{p.label}</span>
            <span className="ml-auto whitespace-nowrap text-[12px] text-mute">{when_(p.t)}</span>
          </li>))}</ul>)}
      {data.trendlines?.length > 0 && <p className="mt-3 text-[12px] text-mute">{data.trendlines.map((t: any) => `${t.kind} trendline ${t.slope_pct_per_bar > 0 ? "rising" : t.slope_pct_per_bar < 0 ? "falling" : "flat"}, now ₹${n(t.points.at(-1).v)}`).join(" · ")}</p>}
      <p className="mt-2 text-[12px] text-mute">Use them in algos, e.g. “buy when bullish engulfing and close &gt; SMA 50” or “buy when close breaks above the trendline”.</p>
    </Panel>
  );
}

function Figures({ m, rsi }: { m: any; rsi?: number }) {
  if (!m) return <Loading />;
  const rows: [string, any, number?][] = [
    ["Market cap ₹cr", m.market_cap, 0], ["P/E", m.pe, 1], ["P/B", m.pb, 1], ["EV/EBITDA", m.ev_ebitda, 1],
    ["ROE %", m.roe, 1], ["ROA %", m.roa, 1], ["Debt / equity", m.debt_to_equity, 2], ["Operating margin %", m.opm, 1],
    ["Sales growth % (YoY)", m.sales_growth, 1], ["Profit growth % (YoY)", m.profit_growth, 1], ["Dividend yield %", m.dividend_yield, 2],
    ["Sales CAGR 3y %", m.sales_cagr_3y, 1], ["Profit CAGR 3y %", m.profit_cagr_3y, 1], ["ROE 3y avg %", m.roe_3y, 1],
    ["ROCE 3y avg %", m.roce_3y, 1], ["Free cash flow ₹cr", m.free_cash_flow, 0], ["FCF yield %", m.fcf_yield, 2],
    ["52-week high", m.high_52w, 2], ["52-week low", m.low_52w, 2], ["% from 52-week high", m.pct_from_52w_high, 1], ["RSI 14", rsi, 0],
  ];
  return (
    <>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">{rows.map(([k, v, d]) => (
        <div key={k} className="contents"><dt className="text-mute">{k}</dt><dd className="text-right tabular-nums">{n(v, d)}</dd></div>))}</dl>
      {!m.fundamentals_at && <p className="mt-3 text-[12px] text-mute">Company figures not fetched yet: they arrive with the nightly update, or add this stock to a Compare list and click Update data.</p>}
    </>
  );
}

function Ownership({ m, history }: { m: any; history: any[] }) {
  if (!m || (m.promoter_holding == null && m.fii_holding == null && !history.length)) return null;
  const chg = (v: any) => v == null ? null : <span className={v > 0 ? "text-gain" : v < 0 ? "text-loss" : "text-mute"}>{v > 0 ? "+" : ""}{n(v, 2)}</span>;
  const rows: [string, any, any][] = [["Promoters", m.promoter_holding, m.promoter_change], ["FIIs (foreign institutions)", m.fii_holding, m.fii_change],
    ["DIIs (domestic institutions)", m.dii_holding, m.dii_change], ["  of which mutual funds", m.mf_holding, null], ["Retail investors", m.retail_holding, null]];
  return (
    <Panel title="Shareholding" aside={history[0] ? <span className="text-[12px] text-mute">quarter to {day(history[0].as_of)}</span> : undefined}>
      <dl className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-1.5 text-sm">
        <dt className="text-[11px] text-mute">holder</dt><dd className="text-right text-[11px] text-mute">%</dd><dd className="text-right text-[11px] text-mute">vs last qtr</dd>
        {rows.map(([k, v, c]) => (
          <div key={k} className="contents"><dt className="whitespace-pre text-mute">{k}</dt><dd className="text-right tabular-nums">{n(v, 2)}</dd><dd className="text-right tabular-nums">{chg(c) ?? "–"}</dd></div>))}
      </dl>
      {history.length > 1 && (
        <details className="mt-3 text-[13px]"><summary className="cursor-pointer text-parch-dim">Last {history.length} quarters</summary>
          <table className="mt-2 w-full text-right tabular-nums"><thead className="text-[11px] text-mute"><tr><th className="text-left font-normal">Quarter</th><th className="font-normal">Prom.</th><th className="font-normal">FII</th><th className="font-normal">DII</th><th className="font-normal">MF</th></tr></thead>
            <tbody>{history.map((h) => <tr key={h.as_of}><td className="text-left text-mute">{day(h.as_of)}</td><td>{n(h.promoter, 1)}</td><td>{n(h.fii, 1)}</td><td>{n(h.dii, 1)}</td><td>{n(h.mf, 1)}</td></tr>)}</tbody></table>
        </details>)}
      <p className="mt-2 text-[12px] text-mute">From the quarterly shareholding pattern filed with NSE. Rising FII/DII holding means big institutions are buying.</p>
    </Panel>
  );
}
