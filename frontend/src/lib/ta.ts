/** Technical indicators computed in the browser for the chart (same formulas as the backend: Wilder RSI/ATR). */
export type Bar = { t: number; o: number; h: number; l: number; c: number; v?: number | null };
export type Pt = { t: number; v: number };

export function sma(x: number[], n: number): (number | null)[] {
  const out: (number | null)[] = []; let sum = 0;
  for (let i = 0; i < x.length; i++) { sum += x[i]; if (i >= n) sum -= x[i - n]; out.push(i >= n - 1 ? sum / n : null); }
  return out;
}

export function ema(x: number[], n: number): (number | null)[] {
  const out: (number | null)[] = []; const k = 2 / (n + 1); let prev: number | null = null;
  for (let i = 0; i < x.length; i++) {
    if (i < n - 1) { out.push(null); continue; }
    if (prev === null) { let s = 0; for (let j = i - n + 1; j <= i; j++) s += x[j]; prev = s / n; } else prev = x[i] * k + prev * (1 - k);
    out.push(prev);
  }
  return out;
}

export function stdev(x: number[], n: number): (number | null)[] {
  const m = sma(x, n);
  return x.map((_, i) => { if (m[i] === null) return null; let s = 0; for (let j = i - n + 1; j <= i; j++) s += (x[j] - (m[i] as number)) ** 2; return Math.sqrt(s / n); });
}

export function rsi(x: number[], n = 14): (number | null)[] {
  const out: (number | null)[] = [null]; let ag = 0, al = 0;
  for (let i = 1; i < x.length; i++) {
    const d = x[i] - x[i - 1], g = Math.max(d, 0), l = Math.max(-d, 0);
    if (i <= n) { ag += g / n; al += l / n; out.push(i === n ? 100 - 100 / (1 + ag / (al || 1e-12)) : null); continue; }
    ag = (ag * (n - 1) + g) / n; al = (al * (n - 1) + l) / n; out.push(100 - 100 / (1 + ag / (al || 1e-12)));
  }
  return out;
}

export function atr(b: Bar[], n = 14): (number | null)[] {
  const tr = b.map((x, i) => (i ? Math.max(x.h - x.l, Math.abs(x.h - b[i - 1].c), Math.abs(x.l - b[i - 1].c)) : x.h - x.l));
  const out: (number | null)[] = []; let prev: number | null = null;
  for (let i = 0; i < tr.length; i++) {
    if (i < n - 1) { out.push(null); continue; }
    if (prev === null) { let s = 0; for (let j = 0; j < n; j++) s += tr[i - j]; prev = s / n; } else prev = (prev * (n - 1) + tr[i]) / n;
    out.push(prev);
  }
  return out;
}

export function macd(x: number[], f = 12, s = 26, sig = 9) {
  const ef = ema(x, f), es = ema(x, s);
  const line = x.map((_, i) => (ef[i] !== null && es[i] !== null ? (ef[i] as number) - (es[i] as number) : null));
  const start = line.findIndex((v) => v !== null);
  const sl = start < 0 ? [] : ema(line.slice(start) as number[], sig);
  const signal = line.map((_, i) => (i < start ? null : sl[i - start]));
  return { line, signal, hist: line.map((v, i) => (v !== null && signal[i] != null ? v - (signal[i] as number) : null)) };
}

export function vwap(b: Bar[], ist = 19800): (number | null)[] {
  let day = "", pv = 0, vol = 0;
  return b.map((x) => {
    const d = new Date((x.t + ist) * 1000).toISOString().slice(0, 10);
    if (d !== day) { day = d; pv = 0; vol = 0; }
    const v = x.v ?? 0; pv += ((x.h + x.l + x.c) / 3) * v; vol += v;
    return vol ? pv / vol : null;
  });
}

export function supertrend(b: Bar[], n = 10, mult = 3): { v: (number | null)[]; up: boolean[] } {
  const a = atr(b, n); const v: (number | null)[] = []; const up: boolean[] = [];
  let fu = 0, fl = 0, dir = true;
  b.forEach((x, i) => {
    if (a[i] === null) { v.push(null); up.push(true); return; }
    const mid = (x.h + x.l) / 2, bu = mid + mult * (a[i] as number), bl = mid - mult * (a[i] as number);
    const pc = i ? b[i - 1].c : x.c;
    fu = !fu || bu < fu || pc > fu ? bu : fu; fl = !fl || bl > fl || pc < fl ? bl : fl;
    if (dir && x.c < fl) dir = false; else if (!dir && x.c > fu) dir = true;
    v.push(dir ? fl : fu); up.push(dir);
  });
  return { v, up };
}

export function heikinAshi(b: Bar[]): Bar[] {
  const out: Bar[] = [];
  b.forEach((x, i) => {
    const c = (x.o + x.h + x.l + x.c) / 4;
    const o = i ? (out[i - 1].o + out[i - 1].c) / 2 : (x.o + x.c) / 2;
    out.push({ t: x.t, o, c, h: Math.max(x.h, o, c), l: Math.min(x.l, o, c), v: x.v });
  });
  return out;
}

/** Indicator catalogue for the chart's picker. pane "main" draws over price; "lower" gets its own panel. */
export type IndicatorDef = { id: string; label: string; pane: "main" | "lower"; params: Record<string, number> };
export const INDICATORS: IndicatorDef[] = [
  { id: "sma", label: "Moving average (SMA)", pane: "main", params: { n: 50 } },
  { id: "ema", label: "Exponential moving average (EMA)", pane: "main", params: { n: 20 } },
  { id: "bb", label: "Bollinger Bands", pane: "main", params: { n: 20, k: 2 } },
  { id: "vwap", label: "VWAP (intraday)", pane: "main", params: {} },
  { id: "supertrend", label: "Supertrend", pane: "main", params: { n: 10, mult: 3 } },
  { id: "rsi", label: "RSI", pane: "lower", params: { n: 14 } },
  { id: "macd", label: "MACD", pane: "lower", params: { fast: 12, slow: 26, signal: 9 } },
  { id: "atr", label: "ATR (volatility)", pane: "lower", params: { n: 14 } },
];
