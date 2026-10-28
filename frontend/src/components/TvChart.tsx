import { createChart, CrosshairMode, IChartApi, ISeriesApi, LineStyle, PriceScaleMode, SeriesMarker, Time, UTCTimestamp } from "lightweight-charts";
import { BarChart3, Camera, CandlestickChart, ChevronDown, Eraser, Expand, LineChart, Minus, MousePointer2, Plus, Ruler, Shrink, Spline, TrendingUp, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ZoneBands } from "./zoneBands";
import { day } from "../lib/format";
import * as ta from "../lib/ta";

const IST = 19800;
const C = { up: "#3FC48A", down: "#F2685A", grid: "#1F283B", text: "#858DA3", line: "#283350", bg: "#151D2E" };
const PALETTE = ["#3987e5", "#c98500", "#9085e9", "#2bb3a8", "#e07bb5", "#8fbf4a"];
const FIB = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
type Tool = "cursor" | "trend" | "hline" | "fib" | "measure";
type Drawing = { id: string; type: "trend" | "hline" | "fib"; a: { t: number; p: number }; b?: { t: number; p: number } };
type Ind = { uid: string; id: string; params: Record<string, number>; color: string };
const n = (v: any, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));
const load = <T,>(k: string, d: T): T => { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch { return d; } };
const save = (k: string, v: any) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } };
const indLabel = (i: Ind) => {
  const p = i.params;
  return ({ sma: `SMA ${p.n}`, ema: `EMA ${p.n}`, bb: `BB ${p.n}, ${p.k}`, vwap: "VWAP", supertrend: `Supertrend ${p.n}, ${p.mult}`,
    rsi: `RSI ${p.n}`, macd: `MACD ${p.fast}, ${p.slow}, ${p.signal}`, atr: `ATR ${p.n}` } as Record<string, string>)[i.id] ?? i.id;
};
const DEFAULT_INDS: Ind[] = [{ uid: "e20", id: "ema", params: { n: 20 }, color: PALETTE[0] }, { uid: "s50", id: "sma", params: { n: 50 }, color: PALETTE[1] },
  { uid: "s200", id: "sma", params: { n: 200 }, color: PALETTE[2] }, { uid: "rsi", id: "rsi", params: { n: 14 }, color: "#B9B3A6" }];

/** TradingView-style chart: chart types, indicator picker, drawing tools saved per stock, log scale, full screen,
 *  screenshot, plus Syena's own layers (S/R zones, market structure, candle patterns, trendlines, trades). */
export default function TvChart({ data, trades, symbol }: { data: any; trades: any[]; symbol: string }) {
  const wrap = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const lowerRefs = useRef<(HTMLDivElement | null)[]>([]);
  const [type, setType] = useState<string>(() => load("syena.tv.type", "candles"));
  const [inds, setInds] = useState<Ind[]>(() => load("syena.tv.inds", DEFAULT_INDS));
  const [layers, setLayers] = useState<Record<string, boolean>>(() => load("syena.tv.layers", { zones: true, structure: true, patterns: true, trendlines: false, trades: true, volume: true }));
  const [log, setLog] = useState<boolean>(() => load("syena.tv.log", false));
  const [drawings, setDrawings] = useState<Drawing[]>(() => load(`syena.tv.draw.${symbol}`, []));
  const [tool, setTool] = useState<Tool>("cursor");
  const [pending, setPending] = useState<{ t: number; p: number } | null>(null);
  const [measure, setMeasure] = useState<string | null>(null);
  const [hover, setHover] = useState<any>(null);
  const [picker, setPicker] = useState(false);
  const [full, setFull] = useState(false);
  const chartRef = useRef<IChartApi | null>(null);
  const toolRef = useRef(tool); toolRef.current = tool;
  const pendingRef = useRef(pending); pendingRef.current = pending;
  useEffect(() => save("syena.tv.type", type), [type]);
  useEffect(() => save("syena.tv.inds", inds), [inds]);
  useEffect(() => save("syena.tv.layers", layers), [layers]);
  useEffect(() => save("syena.tv.log", log), [log]);
  useEffect(() => save(`syena.tv.draw.${symbol}`, drawings), [drawings, symbol]);
  useEffect(() => {
    const onFs = () => setFull(document.fullscreenElement === wrap.current);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { setTool("cursor"); setPending(null); } };
    document.addEventListener("fullscreenchange", onFs); window.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("fullscreenchange", onFs); window.removeEventListener("keydown", onKey); };
  }, []);

  const intraday = data.interval !== "day";
  const shift = (t: number) => (t + (intraday ? IST : 0)) as UTCTimestamp;
  const unshift = (t: number) => t - (intraday ? IST : 0);
  const all: ta.Bar[] = useMemo(() => [...(data.warm ?? []), ...data.candles].filter((b: any) => b.o != null), [data]);
  const firstT = data.candles[0]?.t ?? 0;
  const lower = inds.filter((i) => ta.INDICATORS.find((d) => d.id === i.id)?.pane === "lower");

  // indicator values for every bar (warm-up included), then cut to the visible range
  const computed = useMemo(() => {
    const close = all.map((b) => b.c);
    const out: Record<string, Record<string, (number | null)[]>> = {};
    for (const i of inds) {
      const p = i.params;
      if (i.id === "sma") out[i.uid] = { v: ta.sma(close, p.n) };
      else if (i.id === "ema") out[i.uid] = { v: ta.ema(close, p.n) };
      else if (i.id === "bb") { const m = ta.sma(close, p.n), sd = ta.stdev(close, p.n); out[i.uid] = { mid: m, up: m.map((x, k) => (x === null ? null : x + p.k * (sd[k] as number))), lo: m.map((x, k) => (x === null ? null : x - p.k * (sd[k] as number))) }; }
      else if (i.id === "vwap") out[i.uid] = { v: intraday ? ta.vwap(all) : all.map(() => null) };
      else if (i.id === "supertrend") { const s = ta.supertrend(all, p.n, p.mult); out[i.uid] = { v: s.v, up: s.up.map((x) => (x ? 1 : 0)) }; }
      else if (i.id === "rsi") out[i.uid] = { v: ta.rsi(close, p.n) };
      else if (i.id === "macd") { const m = ta.macd(close, p.fast, p.slow, p.signal); out[i.uid] = { line: m.line, signal: m.signal, hist: m.hist }; }
      else if (i.id === "atr") out[i.uid] = { v: ta.atr(all, p.n) };
    }
    return out;
  }, [all, inds, intraday]);
  const idxOf = useMemo(() => new Map(all.map((b, k) => [b.t, k])), [all]);
  const series = (vals: (number | null)[]) => all.map((b, k) => ({ t: b.t, v: vals[k] })).filter((x) => x.t >= firstT && x.v !== null && Number.isFinite(x.v as number))
    .map((x) => ({ time: shift(x.t), value: x.v as number }));

  const markers = useMemo<SeriesMarker<Time>[]>(() => {
    const out: SeriesMarker<Time>[] = [];
    const lo = data.candles[0]?.t ?? 0;
    const snap = (iso: string) => { const ts = Math.floor(new Date(iso).getTime() / 1000); if (ts < lo - 86400) return null; let best = lo; for (const c of data.candles) { if (c.t <= ts) best = c.t; else break; } return best; };
    if (layers.trades) for (const t of trades) {
      const short = t.side === "SELL";
      const a = t.created_at && t.entry_price ? snap(t.created_at) : null;
      if (a != null) out.push({ time: shift(a), position: short ? "aboveBar" : "belowBar", color: short ? C.down : C.up, shape: short ? "arrowDown" : "arrowUp", text: short ? "Short" : "Buy" });
      const b = t.closed_at && t.exit_price_executed ? snap(t.closed_at) : null;
      if (b != null) out.push({ time: shift(b), position: short ? "belowBar" : "aboveBar", color: "#EEE8DB", shape: "circle", text: short ? "Cover" : "Sell" });
    }
    if (layers.structure) for (const sw of data.levels?.swings ?? []) {
      if (sw.t < lo) continue;
      const top = sw.label.endsWith("H");
      out.push({ time: shift(sw.t), position: top ? "aboveBar" : "belowBar", color: "#B9B3A6", shape: top ? "arrowDown" : "arrowUp", size: 0, text: sw.label } as SeriesMarker<Time>);
    }
    if (layers.patterns) {
      const recent = data.candles.length > 20 ? data.candles[data.candles.length - 20].t : 0;
      for (const p of data.patterns ?? []) {
        const bull = p.direction === "bullish", named = p.t >= recent;
        out.push({ time: shift(p.t), position: bull ? "belowBar" : "aboveBar", color: bull ? "#7FD1A8" : p.direction === "bearish" ? "#F0948A" : "#B9B3A6", shape: "circle", size: named ? 0.6 : 0.3, text: named ? p.label : "" } as SeriesMarker<Time>);
      }
    }
    return out.sort((x, y) => (x.time as number) - (y.time as number));
  }, [data, trades, layers.trades, layers.structure, layers.patterns]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!box.current) return;
    const base = {
      autoSize: true, layout: { background: { color: C.bg }, textColor: C.text, fontFamily: "Figtree Variable, system-ui, sans-serif" },
      grid: { vertLines: { color: C.grid, style: LineStyle.Dotted }, horzLines: { color: C.grid, style: LineStyle.Dotted } }, rightPriceScale: { borderColor: C.line },
      timeScale: { borderColor: C.line, timeVisible: intraday, secondsVisible: false, rightOffset: 6 }, crosshair: { mode: CrosshairMode.Normal },
      localization: { locale: "en-IN", priceFormatter: (p: number) => p.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) },
    };
    const ch = createChart(box.current, base);
    chartRef.current = ch;
    ch.priceScale("right").applyOptions({ mode: log ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal, scaleMargins: { top: 0.06, bottom: layers.volume ? 0.22 : 0.06 } });
    const bars = type === "heikin" ? ta.heikinAshi(data.candles) : data.candles;
    let main: ISeriesApi<any>;
    if (type === "line" || type === "area") {
      main = type === "line" ? ch.addLineSeries({ color: "#7DB4F5", lineWidth: 2 }) : ch.addAreaSeries({ lineColor: "#7DB4F5", topColor: "rgba(125,180,245,.35)", bottomColor: "rgba(125,180,245,0)", lineWidth: 2 });
      main.setData(bars.map((c: any) => ({ time: shift(c.t), value: c.c })));
    } else if (type === "bars") {
      main = ch.addBarSeries({ upColor: C.up, downColor: C.down, thinBars: false });
      main.setData(bars.map((c: any) => ({ time: shift(c.t), open: c.o, high: c.h, low: c.l, close: c.c })));
    } else {
      main = ch.addCandlestickSeries({ upColor: C.up, downColor: C.down, wickUpColor: C.up, wickDownColor: C.down, borderVisible: false, priceFormat: { type: "price", precision: 2, minMove: 0.05 } });
      main.setData(bars.map((c: any) => ({ time: shift(c.t), open: c.o, high: c.h, low: c.l, close: c.c })));
    }
    if (layers.volume) {
      const vol = ch.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
      ch.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
      vol.setData(data.candles.map((c: any) => ({ time: shift(c.t), value: c.v ?? 0, color: c.c >= c.o ? "rgba(86,180,136,.35)" : "rgba(227,107,93,.35)" })));
    }
    const hoverSeries: [string, ISeriesApi<any>][] = [];
    for (const i of inds.filter((x) => ta.INDICATORS.find((d) => d.id === x.id)?.pane === "main")) {
      const v = computed[i.uid] ?? {};
      if (i.id === "bb") {
        for (const [k, w] of [["up", 1], ["mid", 1], ["lo", 1]] as const) {
          const s = ch.addLineSeries({ color: i.color, lineWidth: w, lineStyle: k === "mid" ? LineStyle.Dashed : LineStyle.Solid, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
          s.setData(series(v[k])); if (k === "mid") hoverSeries.push([i.uid, s]);
        }
      } else if (i.id === "supertrend") {
        const s = ch.addLineSeries({ color: i.color, lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        s.setData(all.map((b, k) => ({ b, k })).filter(({ b, k }) => b.t >= firstT && v.v[k] !== null)
          .map(({ b, k }) => ({ time: shift(b.t), value: v.v[k] as number, color: v.up[k] ? C.up : C.down })));
        hoverSeries.push([i.uid, s]);
      } else {
        const s = ch.addLineSeries({ color: i.color, lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        s.setData(series(v.v)); hoverSeries.push([i.uid, s]);
      }
    }
    if (layers.trendlines) for (const tl of data.trendlines ?? []) {
      const s = ch.addLineSeries({ color: tl.kind === "support" ? C.up : C.down, lineWidth: 1, lineStyle: LineStyle.Dashed, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
      s.setData(tl.points.map((p: any) => ({ time: shift(p.t), value: p.v })).filter((p: any, k: number, a: any[]) => k === 0 || p.time > a[k - 1].time));
    }
    if (layers.zones && data.levels && type !== "line" && type !== "area") {
      const bands = [...(data.levels.resistances ?? []).map((z: any, k: number) => ({ low: z.low, high: z.high, color: C.down, label: `R${k + 1} · ${z.strength}` })),
        ...(data.levels.supports ?? []).map((z: any, k: number) => ({ low: z.low, high: z.high, color: C.up, label: `S${k + 1} · ${z.strength}` }))];
      main.attachPrimitive(new ZoneBands(bands));
    }
    // drawings
    for (const d of drawings) {
      if (d.type === "hline") { main.createPriceLine({ price: d.a.p, color: "#F2A93B", lineWidth: 1, lineStyle: LineStyle.Solid, axisLabelVisible: true, title: "" }); continue; }
      if (!d.b) continue;
      if (d.type === "trend") {
        const pts = [d.a, d.b].sort((x, y) => x.t - y.t);
        if (pts[0].t === pts[1].t) continue;
        const s = ch.addLineSeries({ color: "#F2A93B", lineWidth: 2, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
        s.setData(pts.map((x) => ({ time: shift(x.t), value: x.p })));
      } else if (d.type === "fib") {
        const hi = Math.max(d.a.p, d.b.p), lo = Math.min(d.a.p, d.b.p);
        const upMove = d.b.p > d.a.p;  // drawn low -> high: retracements measured down from the high
        for (const f of FIB) {
          const price = upMove ? hi - (hi - lo) * f : lo + (hi - lo) * f;
          main.createPriceLine({ price, color: f === 0.618 || f === 0.5 ? "#F2A93B" : "#9085e9", lineWidth: 1, lineStyle: f === 0 || f === 1 ? LineStyle.Solid : LineStyle.Dashed, axisLabelVisible: true, title: `${f}` });
        }
      }
    }
    main.setMarkers(markers);
    ch.timeScale().fitContent();
    // lower panes, kept in step with the main chart
    const lowerCharts: IChartApi[] = [];
    lower.forEach((i, k) => {
      const el = lowerRefs.current[k];
      if (!el) return;
      const lc = createChart(el, { ...base, layout: { ...base.layout, attributionLogo: false } as any, timeScale: { ...base.timeScale, visible: k === lower.length - 1 }, handleScroll: false, handleScale: false });
      const v = computed[i.uid] ?? {};
      if (i.id === "macd") {
        const h = lc.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
        h.setData(series(v.hist).map((x) => ({ ...x, color: x.value >= 0 ? "rgba(63,196,138,.6)" : "rgba(242,104,90,.6)" })));
        lc.addLineSeries({ color: "#3987e5", lineWidth: 2, priceLineVisible: false }).setData(series(v.line));
        lc.addLineSeries({ color: "#c98500", lineWidth: 2, priceLineVisible: false }).setData(series(v.signal));
      } else {
        const s = lc.addLineSeries({ color: i.color, lineWidth: 2, priceLineVisible: false });
        s.setData(series(v.v));
        if (i.id === "rsi") for (const lvl of [70, 30]) s.createPriceLine({ price: lvl, color: lvl === 70 ? C.down : C.up, lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: true, title: "" });
      }
      lowerCharts.push(lc);
    });
    const sync = (r: any) => { if (r) lowerCharts.forEach((lc) => lc.timeScale().setVisibleLogicalRange(r)); };
    ch.timeScale().subscribeVisibleLogicalRangeChange(sync);
    sync(ch.timeScale().getVisibleLogicalRange());
    const byTime = new Map<number, any>(data.candles.map((c: any) => [shift(c.t), c]));
    ch.subscribeCrosshairMove((p) => { const c = p.time != null ? byTime.get(p.time as number) : null; setHover(c ?? null); });
    // native clicks: the chart library drops a quick second click as a would-be double-click
    const el = box.current;
    const onClick = (e: MouseEvent) => {
      const r = el.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      if (x > ch.timeScale().width()) return;  // on the price axis
      const price = main.coordinateToPrice(y);
      if (price == null) return;
      const tt = ch.timeScale().coordinateToTime(x);
      const t = tt != null ? unshift(tt as number) : null;
      const cur = toolRef.current, pa = pendingRef.current;
      const uid = () => crypto.randomUUID?.() ?? String(Date.now());
      if (cur === "hline") { setDrawings((d) => [...d, { id: uid(), type: "hline", a: { t: t ?? 0, p: price as number } }]); setTool("cursor"); return; }
      if (cur === "cursor" || t == null) return;
      if (!pa) { setPending({ t, p: price as number }); return; }
      if (cur === "measure") {
        const pct = ((price as number) / pa.p - 1) * 100;
        const bars = Math.abs((idxOf.get(t) ?? 0) - (idxOf.get(pa.t) ?? 0));
        setMeasure(`${pct >= 0 ? "+" : ""}${pct.toFixed(2)}% (${n((price as number) - pa.p)}) over ${bars} candles`);
      } else setDrawings((d) => [...d, { id: uid(), type: cur, a: pa, b: { t, p: price as number } }]);
      setPending(null); setTool("cursor");
    };
    el.addEventListener("click", onClick);
    return () => { el.removeEventListener("click", onClick); ch.remove(); lowerCharts.forEach((lc) => lc.remove()); chartRef.current = null; };
  }, [data, type, inds, layers, log, drawings, markers, computed, full]); // eslint-disable-line react-hooks/exhaustive-deps

  const c = hover ?? data.candles.at(-1);
  const prev = (() => { const k = data.candles.findIndex((x: any) => x.t === c?.t); return k > 0 ? data.candles[k - 1].c : (data.warm?.at(-1)?.c ?? c?.o); })();
  const chg = c && prev ? (c.c / prev - 1) * 100 : null;
  const k = c ? idxOf.get(c.t) : undefined;
  const fmtT = (t: number) => intraday ? new Date((t + IST) * 1000).toISOString().slice(0, 16).replace("T", " ") : day(new Date(t * 1000).toISOString());
  const valueAt = (i: Ind) => {
    if (k === undefined) return null;
    const v = computed[i.uid];
    if (!v) return null;
    if (i.id === "bb") return `${n(v.up[k])} / ${n(v.mid[k])} / ${n(v.lo[k])}`;
    if (i.id === "macd") return `${n(v.line[k])} / ${n(v.signal[k])}`;
    return n(v.v?.[k]);
  };
  const addInd = (id: string) => {
    const def = ta.INDICATORS.find((d) => d.id === id)!;
    setInds([...inds, { uid: `${id}${Date.now()}`, id, params: { ...def.params }, color: PALETTE[inds.length % PALETTE.length] }]);
    setPicker(false);
  };
  const editParam = (uid: string, key: string, val: number) => setInds(inds.map((i) => (i.uid === uid ? { ...i, params: { ...i.params, [key]: val } } : i)));
  const shot = () => {
    const cv = chartRef.current?.takeScreenshot();
    if (!cv) return;
    const a = document.createElement("a"); a.href = cv.toDataURL("image/png"); a.download = `${symbol}-${data.interval}.png`; a.click();
  };
  const toggleFull = () => { if (document.fullscreenElement) document.exitFullscreen(); else wrap.current?.requestFullscreen?.(); };
  const TOOLS: [Tool, any, string][] = [["cursor", MousePointer2, "Cursor"], ["trend", TrendingUp, "Trend line (2 clicks)"], ["hline", Minus, "Horizontal line (1 click)"],
    ["fib", Spline, "Fibonacci retracement (click the swing low, then the high)"], ["measure", Ruler, "Measure (2 clicks)"]];
  const mainH = full ? `calc(100vh - ${110 + lower.length * 120}px)` : "460px";

  return (
    <div ref={wrap} className={`bg-dusk-panel ${full ? "h-screen overflow-auto p-2" : ""}`}>
      {/* toolbar */}
      <div className="flex flex-wrap items-center gap-1 border-b border-dusk-line px-2 py-1.5 text-[13px]">
        <div className="flex rounded-md bg-dusk p-0.5">
          {[["candles", CandlestickChart, "Candles"], ["heikin", CandlestickChart, "Heikin Ashi"], ["bars", BarChart3, "Bars"], ["line", LineChart, "Line"], ["area", LineChart, "Area"]].map(([k, Icon, label]: any) => (
            <button key={k} type="button" title={label} onClick={() => setType(k)} className={`flex items-center gap-1 rounded px-2 py-1 ${type === k ? "bg-dusk-raised text-parch" : "text-mute hover:text-parch"}`}>
              <Icon size={14} />{k === "heikin" ? "HA" : ""}</button>))}
        </div>
        <div className="relative">
          <button type="button" onClick={() => setPicker(!picker)} className="flex items-center gap-1 rounded-md px-2 py-1 text-parch-dim hover:bg-dusk-raised hover:text-parch"><Plus size={14} /> Indicators <ChevronDown size={12} /></button>
          {picker && (
            <div className="absolute left-0 top-full z-30 mt-1 w-64 rounded-lg border border-dusk-line bg-dusk-raised p-1 shadow-xl">
              {ta.INDICATORS.map((d) => <button key={d.id} type="button" onClick={() => addInd(d.id)} className="flex w-full justify-between rounded px-2.5 py-1.5 text-left text-parch-dim hover:bg-dusk hover:text-parch"><span>{d.label}</span><span className="text-[11px] text-mute">{d.pane === "main" ? "on price" : "panel"}</span></button>)}
            </div>)}
        </div>
        <span className="mx-1 h-5 w-px bg-dusk-line" />
        {TOOLS.map(([k, Icon, label]) => (
          <button key={k} type="button" title={label} onClick={() => { setTool(k); setPending(null); setMeasure(null); }}
            className={`rounded-md p-1.5 ${tool === k ? "bg-saffron/20 text-saffron" : "text-mute hover:bg-dusk-raised hover:text-parch"}`}><Icon size={15} /></button>))}
        {drawings.length > 0 && <button type="button" title="Remove all drawings" onClick={() => setDrawings([])} className="rounded-md p-1.5 text-mute hover:bg-dusk-raised hover:text-loss"><Eraser size={15} /></button>}
        <span className="mx-1 h-5 w-px bg-dusk-line" />
        <button type="button" onClick={() => setLog(!log)} className={`rounded-md px-2 py-1 ${log ? "bg-dusk-raised text-parch" : "text-mute hover:text-parch"}`} title="Logarithmic price scale">log</button>
        <span className="ml-auto flex items-center gap-1">
          <button type="button" title="Save a picture of the chart" onClick={shot} className="rounded-md p-1.5 text-mute hover:bg-dusk-raised hover:text-parch"><Camera size={15} /></button>
          <button type="button" title={full ? "Exit full screen" : "Full screen"} onClick={toggleFull} className="rounded-md p-1.5 text-mute hover:bg-dusk-raised hover:text-parch">{full ? <Shrink size={15} /> : <Expand size={15} />}</button>
        </span>
      </div>
      {/* legend */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 pt-2 text-[13px] tabular-nums">
        <b className="text-parch">{symbol}</b><span className="text-mute">{fmtT(c.t)}</span>
        <span>O <b>{n(c.o)}</b></span><span>H <b>{n(c.h)}</b></span><span>L <b>{n(c.l)}</b></span>
        <span>C <b className={c.c >= c.o ? "text-gain" : "text-loss"}>{n(c.c)}</b></span>
        {chg != null && <span className={chg >= 0 ? "text-gain" : "text-loss"}>{chg >= 0 ? "+" : ""}{chg.toFixed(2)}%</span>}
        <span className="text-mute">Vol {n(c.v, 0)}</span>
        {tool !== "cursor" && <span className="rounded bg-saffron/15 px-2 py-0.5 text-[12px] text-saffron">{pending ? "click the second point" : TOOLS.find((x) => x[0] === tool)?.[2]} · Esc to cancel</span>}
        {measure && <span className="rounded bg-sky/15 px-2 py-0.5 text-[12px] text-sky">{measure} <button type="button" onClick={() => setMeasure(null)}><X size={11} className="inline" /></button></span>}
      </div>
      <div className="flex flex-wrap gap-1.5 px-3 pb-1 pt-1 text-[12px]">
        {inds.filter((i) => ta.INDICATORS.find((d) => d.id === i.id)?.pane === "main").map((i) => (
          <span key={i.uid} className="group inline-flex items-center gap-1.5 rounded border border-dusk-line px-1.5 py-0.5">
            <span className="h-0.5 w-3 rounded" style={{ background: i.color }} />{indLabel(i)} <span className="text-parch-dim">{valueAt(i)}</span>
            {Object.entries(i.params).map(([pk, pv]) => <input key={pk} type="number" value={pv} onChange={(e) => editParam(i.uid, pk, Math.max(1, Number(e.target.value)))} className="hidden w-12 rounded bg-dusk px-1 text-parch group-hover:inline" title={pk} />)}
            <button type="button" onClick={() => setInds(inds.filter((x) => x.uid !== i.uid))} className="text-mute hover:text-loss" aria-label={`Remove ${indLabel(i)}`}><X size={11} /></button>
          </span>))}
        {[["zones", "S/R zones"], ["structure", "HH/HL"], ["patterns", "Patterns"], ["trendlines", "Trendlines"], ["trades", "Trades"], ["volume", "Volume"]].map(([kk, label]) => (
          <button key={kk} type="button" aria-pressed={!!layers[kk]} onClick={() => setLayers({ ...layers, [kk]: !layers[kk] })}
            className={`rounded border px-1.5 py-0.5 ${layers[kk] ? "border-dusk-line text-parch" : "border-transparent text-mute"}`}>{label}</button>))}
      </div>
      <div ref={box} className={`w-full ${tool !== "cursor" ? "cursor-crosshair" : ""}`} style={{ height: mainH }} aria-label={`${symbol} price chart`} />
      {lower.map((i, kk) => (
        <div key={i.uid} className="border-t border-dusk-line">
          <div className="flex items-center gap-2 px-3 pt-1 text-[12px] text-mute">{indLabel(i)} <span className="text-parch-dim">{valueAt(i)}</span>
            {Object.entries(i.params).map(([pk, pv]) => <input key={pk} type="number" value={pv} onChange={(e) => editParam(i.uid, pk, Math.max(1, Number(e.target.value)))} className="w-12 rounded bg-dusk px-1 text-parch" title={pk} />)}
            <button type="button" onClick={() => setInds(inds.filter((x) => x.uid !== i.uid))} className="hover:text-loss" aria-label={`Remove ${indLabel(i)}`}><X size={12} /></button></div>
          <div ref={(el) => { lowerRefs.current[kk] = el; }} className="h-[110px] w-full" />
        </div>))}
      {drawings.length > 0 && (
        <div className="flex flex-wrap gap-1.5 px-3 pb-2 pt-1 text-[12px] text-mute">Drawings:
          {drawings.map((d) => <span key={d.id} className="inline-flex items-center gap-1 rounded bg-dusk px-1.5 py-0.5">{d.type === "hline" ? `line ${n(d.a.p)}` : d.type === "fib" ? `fib ${n(d.a.p)}→${n(d.b?.p)}` : `trend ${n(d.a.p)}→${n(d.b?.p)}`}
            <button type="button" onClick={() => setDrawings(drawings.filter((x) => x.id !== d.id))} className="hover:text-loss"><X size={11} /></button></span>)}
          <span>(saved for {symbol} in this browser)</span>
        </div>)}
    </div>
  );
}
