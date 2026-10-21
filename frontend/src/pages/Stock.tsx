import { createChart, CrosshairMode, IChartApi, ISeriesApi, LineStyle, SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AiVerdict from "../components/AiVerdict";
import { Empty, ErrorNote, Loading, Page, Panel, Pill, Tabs, decisionLabel, decisionTone } from "../components/ui";
import { api } from "../lib/api";
import { day, signed, tone, when } from "../lib/format";
import { useApi } from "../lib/hooks";

const RANGES = ["1D", "5D", "1M", "3M", "6M", "1Y", "3Y", "5Y"];
// candle size -> longest range it can show (more candles than that is slow to load and draw)
const CANDLES: [string, string, string][] = [["minute", "1m", "1M"], ["5minute", "5m", "3M"], ["15minute", "15m", "6M"], ["60minute", "1h", "1Y"], ["day", "1D", "5Y"]];
const AUTO: Record<string, string> = { "1D": "5minute", "5D": "15minute", "1M": "60minute", "3M": "day", "6M": "day", "1Y": "day", "3Y": "day", "5Y": "day" };
const fits = (iv: string, range: string) => RANGES.indexOf(range) <= RANGES.indexOf(CANDLES.find((c) => c[0] === iv)![2]);
const IST = 19800; // chart axis in Indian time
const C = { up: "#56B488", down: "#E36B5D", grid: "#232a3a", text: "#8B93A7", line: "#2F384C", bg: "#1B2130" };
// Overlay lines: validated categorical slots (blue, yellow, violet) - distinct from the green/red candles.
const LINES: Record<string, { label: string; color: string }> = {
  sma20: { label: "20-day avg", color: "#3987e5" }, sma50: { label: "50-day avg", color: "#c98500" }, sma200: { label: "200-day avg", color: "#9085e9" },
  ema20: { label: "EMA 20", color: "#3987e5" }, vwap: { label: "VWAP", color: "#c98500" }, ema50: { label: "EMA 50", color: "#c98500" },
};
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
      actions={<Link to="/stock" className="btn-ghost">Another stock</Link>}>
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
            : <PriceChart data={chart.data} trades={info.data?.trades ?? []} />}
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
    ["Promoter holding %", m.promoter_holding, 1], ["52-week high", m.high_52w, 2], ["52-week low", m.low_52w, 2],
    ["% from 52-week high", m.pct_from_52w_high, 1], ["RSI 14", rsi, 0],
  ];
  return (
    <>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">{rows.map(([k, v, d]) => (
        <div key={k} className="contents"><dt className="text-mute">{k}</dt><dd className="text-right tabular-nums">{n(v, d)}</dd></div>))}</dl>
      {!m.fundamentals_at && <p className="mt-3 text-[12px] text-mute">Company figures not fetched yet: they arrive with the nightly update, or add this stock to a Compare list and click Update data.</p>}
    </>
  );
}

function PriceChart({ data, trades }: { data: any; trades: any[] }) {
  const box = useRef<HTMLDivElement>(null);
  const rsiBox = useRef<HTMLDivElement>(null);
  const [on, setOn] = useState<Record<string, boolean>>({ sma20: false, sma50: true, sma200: true, ema20: true, vwap: true, ema50: false, patterns: true, trendlines: true });
  const [hover, setHover] = useState<any>(null);
  const intraday = data.interval !== "day";
  const shift = (t: number) => (t + (intraday ? IST : 0)) as UTCTimestamp;
  const markers = useMemo<SeriesMarker<Time>[]>(() => {
    if (!data.candles.length) return [];
    const lo = data.candles[0].t, hi = data.candles.at(-1).t;
    const snap = (iso: string) => {  // place a trade on the candle it happened in
      const ts = Math.floor(new Date(iso).getTime() / 1000);
      if (ts < lo - 86400 || ts > hi + 86400) return null;
      let best = data.candles[0].t;
      for (const c of data.candles) { if (c.t <= ts) best = c.t; else break; }
      return best;
    };
    const out: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      const short = t.side === "SELL";
      const a = t.created_at && t.entry_price ? snap(t.created_at) : null;
      if (a != null) out.push({ time: shift(a), position: short ? "aboveBar" : "belowBar", color: short ? C.down : C.up, shape: short ? "arrowDown" : "arrowUp", text: short ? "Short" : "Buy" });
      const b = t.closed_at && t.exit_price_executed ? snap(t.closed_at) : null;
      if (b != null) out.push({ time: shift(b), position: short ? "belowBar" : "aboveBar", color: "#ECE6D8", shape: "circle", text: short ? "Cover" : "Sell" });
    }
    if (on.patterns) {
      const recent = data.candles.length > 20 ? data.candles[data.candles.length - 20].t : 0;
      const seen = new Set<string>();
      for (const p of data.patterns ?? []) {
        const bull = p.direction === "bullish";
        const named = p.t >= recent;  // names on the last 20 candles; older patterns are small dots
        const key = `${p.t}-${bull}`;
        if (!named && seen.has(key)) continue;
        seen.add(key);
        out.push({ time: shift(p.t), position: bull ? "belowBar" : "aboveBar", color: bull ? "#7FD1A8" : p.direction === "bearish" ? "#F0948A" : "#B9B3A6",
          shape: "circle", size: named ? 0.6 : 0.3, text: named ? p.label : "" } as SeriesMarker<Time>);
      }
    }
    return out.sort((x, y) => (x.time as number) - (y.time as number));
  }, [data, trades, on.patterns]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!box.current || !rsiBox.current) return;
    const base = {
      autoSize: true, layout: { background: { color: C.bg }, textColor: C.text, fontFamily: "Figtree Variable, system-ui, sans-serif" },
      grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } }, rightPriceScale: { borderColor: C.line },
      timeScale: { borderColor: C.line, timeVisible: intraday, secondsVisible: false }, crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "en-IN", priceFormatter: (p: number) => p.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
    };
    const ch: IChartApi = createChart(box.current, base);
    const candles = ch.addCandlestickSeries({ upColor: C.up, downColor: C.down, wickUpColor: C.up, wickDownColor: C.down, borderVisible: false,
      priceFormat: { type: "price", precision: 2, minMove: 0.05 } });
    candles.setData(data.candles.map((c: any) => ({ time: shift(c.t), open: c.o, high: c.h, low: c.l, close: c.c })));
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.06, bottom: 0.24 } });
    const vol = ch.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
    ch.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    vol.setData(data.candles.map((c: any) => ({ time: shift(c.t), value: c.v ?? 0, color: c.c >= c.o ? "rgba(86,180,136,.35)" : "rgba(227,107,93,.35)" })));
    const lineSeries: Record<string, ISeriesApi<"Line">> = {};
    for (const [k, pts] of Object.entries<any[]>(data.lines ?? {})) {
      if (!on[k]) continue;
      const s = ch.addLineSeries({ color: LINES[k]?.color ?? "#ECE6D8", lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
      s.setData(pts.map((p) => ({ time: shift(p.t), value: p.v })));
      lineSeries[k] = s;
    }
    if (on.trendlines) {
      for (const tl of data.trendlines ?? []) {
        const s = ch.addLineSeries({ color: tl.kind === "support" ? "#56B488" : "#E36B5D", lineWidth: 1, lineStyle: LineStyle.Dashed,
          lastValueVisible: true, priceLineVisible: false, crosshairMarkerVisible: false, title: tl.kind === "support" ? "support" : "resistance" });
        const pts = tl.points.map((p: any) => ({ time: shift(p.t), value: p.v })).filter((p: any, i: number, a: any[]) => i === 0 || p.time > a[i - 1].time);
        s.setData(pts);
      }
    }
    candles.setMarkers(markers);
    ch.timeScale().fitContent();

    const rc: IChartApi = createChart(rsiBox.current, { ...base, layout: { ...base.layout, attributionLogo: false } as any,
      timeScale: { ...base.timeScale, visible: false }, handleScroll: false, handleScale: false });
    const rs = rc.addLineSeries({ color: "#B9B3A6", lineWidth: 2, lastValueVisible: true, priceLineVisible: false });
    rs.setData((data.rsi ?? []).map((p: any) => ({ time: shift(p.t), value: p.v })));
    for (const lvl of [70, 30]) rs.createPriceLine({ price: lvl, color: lvl === 70 ? C.down : C.up, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: "" });
    rc.priceScale("right").applyOptions({ scaleMargins: { top: 0.1, bottom: 0.1 } });
    const sync = (r: any) => { if (r) rc.timeScale().setVisibleLogicalRange(r); };
    ch.timeScale().subscribeVisibleLogicalRangeChange(sync);
    sync(ch.timeScale().getVisibleLogicalRange());

    const byTime = new Map<number, any>(data.candles.map((c: any) => [shift(c.t), c]));
    ch.subscribeCrosshairMove((p) => {
      const c = p.time != null ? byTime.get(p.time as number) : null;
      if (!c) { setHover(null); return; }
      const vals: Record<string, number | undefined> = {};
      for (const [k, s] of Object.entries(lineSeries)) vals[k] = (p.seriesData.get(s) as any)?.value;
      setHover({ ...c, vals });
    });
    return () => { ch.remove(); rc.remove(); };
  }, [data, on, markers]); // eslint-disable-line react-hooks/exhaustive-deps

  const c = hover ?? data.candles.at(-1);
  const fmtT = (t: number) => intraday ? new Date((t + IST) * 1000).toISOString().slice(0, 16).replace("T", " ") : day(new Date(t * 1000).toISOString());
  return (
    <div className="px-2 pb-3 pt-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-2 pb-2 text-[13px] tabular-nums">
        <span className="text-mute">{fmtT(c.t)}</span>
        <span>O <b>{n(c.o)}</b></span><span>H <b>{n(c.h)}</b></span><span>L <b>{n(c.l)}</b></span>
        <span>C <b className={c.c >= c.o ? "text-gain" : "text-loss"}>{n(c.c)}</b></span>
        <span className="text-mute">Vol {n(c.v, 0)}</span>
        <span className="ml-auto flex flex-wrap gap-1.5">
          {[["patterns", "Candle patterns"], ["trendlines", "Trendlines"]].map(([k, label]) => (
            <button key={k} type="button" aria-pressed={!!on[k]} onClick={() => setOn({ ...on, [k]: !on[k] })}
              className={`rounded-md border px-2 py-0.5 ${on[k] ? "border-dusk-line text-parch" : "border-transparent text-mute"}`}>{label}</button>))}
          {Object.keys(data.lines ?? {}).map((k) => (
          <button key={k} type="button" aria-pressed={!!on[k]} onClick={() => setOn({ ...on, [k]: !on[k] })}
            className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 ${on[k] ? "border-dusk-line text-parch" : "border-transparent text-mute"}`}>
            <span className="h-0.5 w-4 rounded" style={{ background: on[k] ? LINES[k]?.color : C.line }} />{LINES[k]?.label ?? k}
            {on[k] && hover?.vals?.[k] != null && <span className="text-parch-dim">{n(hover.vals[k])}</span>}
          </button>))}</span>
      </div>
      <div ref={box} className="h-[420px] w-full" aria-label={`${data.symbol} price chart`} />
      <div className="flex items-center gap-2 px-2 pt-1 text-[12px] text-mute">RSI 14 <span className="text-parch-dim">{n(data.rsi?.at(-1)?.v, 0)}</span> <span>(above 70 overbought, below 30 oversold)</span></div>
      <div ref={rsiBox} className="h-[110px] w-full" aria-label="RSI chart" />
      <p className="px-2 pt-2 text-[12px] text-mute">Arrows mark Syena's entries (▲ buy, ▼ short); dots mark exits. Small markers are candlestick patterns (green bullish, red bearish; named on the last 20 candles); dashed lines are the current trendlines through the last two swing lows (support) and highs (resistance).</p>
    </div>
  );
}
