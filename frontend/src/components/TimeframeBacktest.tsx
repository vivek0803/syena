import { useState } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { ErrorNote, Panel, Pill } from "./ui";
import { api } from "../lib/api";
import { money, ratio, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const ORDER = ["minute", "2minute", "5minute", "15minute", "60minute", "240minute", "day"];

/** The same rule backtested on every candle size, side by side. */
export default function TimeframeBacktest({ id, disabled, nSymbols }: { id: number; disabled: boolean; nSymbols: number }) {
  const q = useApi<any>(`/api/algos/${id}/backtest-timeframes`, { refetchInterval: (x: any) => (x.state.data?.job?.status === "running" ? 2500 : false) } as any);
  const [pick, setPick] = useState<string[]>(ORDER);
  const run = useAct(() => api(`/api/algos/${id}/backtest-timeframes`, { body: { timeframes: pick } }), [`/api/algos/${id}/backtest-timeframes`]);
  const d = q.data, res = d?.latest, job = d?.job, prog = d?.progress;
  const running = job?.status === "running";
  const tfs = d?.timeframes ?? {};
  return (
    <Panel title="Backtest on every timeframe" aside={<button className="btn-primary py-1.5" disabled={disabled || running || run.isPending || !pick.length}
      onClick={() => run.mutate(undefined)}>{running ? "Running…" : res ? "Run again" : "Run on all timeframes"}</button>}>
      <div className="flex flex-wrap gap-1.5">{ORDER.map((tf) => {
        const on = pick.includes(tf);
        return <button key={tf} type="button" onClick={() => setPick(on ? pick.filter((x) => x !== tf) : ORDER.filter((x) => pick.includes(x) || x === tf))}
          className={`rounded-md px-2.5 py-1 text-[13px] ${on ? "bg-saffron/15 text-saffron ring-1 ring-saffron/30" : "bg-dusk text-mute hover:text-parch"}`}>
          {tfs[tf]?.label ?? tf} <span className="text-[11px] opacity-70">· {tfs[tf] ? (tfs[tf].days >= 365 ? `${Math.round(tfs[tf].days / 365)}y` : `${tfs[tf].days}d`) : ""}</span></button>;
      })}</div>
      <p className="mt-2 text-[12px] text-mute">Same rule, same stocks{nSymbols > 25 ? " (first 25)" : ""}, charges and slippage included. Indicator periods count candles, so “SMA 20” is 20 minutes on 1-minute candles and 20 days on daily ones. Short candles are tested on shorter windows (that's how much intraday history the brokers keep). 2-minute and 4-hour candles are built from 1-minute and 1-hour ones.</p>
      {running && prog && (
        <div className="mt-3 text-sm text-parch-dim">Testing {prog.current}… {prog.done} / {prog.total}
          <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-dusk-line"><div className="h-full rounded-full bg-saffron" style={{ width: `${prog.total ? (prog.done / prog.total) * 100 : 0}%` }} /></div>
        </div>)}
      {job?.status === "failed" && <p className="mt-2 text-sm text-loss">Failed: {job.error}</p>}
      <ErrorNote error={run.error ?? q.error} />
      {res && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-[12px] text-mute"><tr>{["Candles", "Period", "Trades", "Win rate", "Total P&L", "Profit factor", "Max drawdown", "Sharpe", "Equity", ""].map((h) => <th key={h} className="px-2 py-2 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-dusk-line">{res.rows.map((r: any) => {
              const m = r.metrics;
              const best = res.best === r.timeframe;
              return (
                <tr key={r.timeframe} className={best ? "bg-gain-soft/20" : ""}>
                  <td className="px-2 py-2 font-medium">{r.label}</td>
                  <td className="px-2 py-2 text-mute">{r.days >= 365 ? `${Math.round(r.days / 365)} yr` : `${r.days} days`}</td>
                  {r.error ? <td colSpan={7} className="px-2 py-2 text-[13px] text-loss">{r.error}</td> : (<>
                    <td className="px-2 py-2 tabular-nums">{m.trades}</td>
                    <td className="px-2 py-2 tabular-nums">{m.trades ? ratio(m.win_rate) : "–"}</td>
                    <td className={`px-2 py-2 tabular-nums ${m.total_pnl > 0 ? "text-gain" : m.total_pnl < 0 ? "text-loss" : ""}`}>{money(m.total_pnl)}</td>
                    <td className="px-2 py-2 tabular-nums">{m.profit_factor ?? "–"}</td>
                    <td className="px-2 py-2 tabular-nums">{m.max_drawdown_pct != null ? `${m.max_drawdown_pct}%` : "–"}</td>
                    <td className="px-2 py-2 tabular-nums">{m.sharpe ?? "–"}</td>
                    <td className="w-28 px-2 py-1">{r.equity?.length > 1 && (
                      <ResponsiveContainer width="100%" height={30}><AreaChart data={r.equity}><Area dataKey="equity" type="monotone" stroke={m.total_pnl >= 0 ? "#3FC48A" : "#F2685A"} fill={m.total_pnl >= 0 ? "#3FC48A22" : "#F2685A22"} strokeWidth={1.5} isAnimationActive={false} /></AreaChart></ResponsiveContainer>)}</td>
                  </>)}
                  <td className="px-2 py-2 text-right">{best && <Pill tone="gain">best fit</Pill>}{!r.error && m?.trades < 10 && <span className="text-[12px] text-mute">too few trades</span>}</td>
                </tr>);
            })}</tbody>
          </table>
          <p className="mt-2 text-[12px] text-mute">Run {when(res.at)} on {res.symbols_tested.length} stock{res.symbols_tested.length === 1 ? "" : "s"}. “Best fit” = highest profit factor with at least 10 trades. Change the algo's candle size above to trade the one you like, then re-run the main backtest.</p>
        </div>)}
    </Panel>
  );
}
