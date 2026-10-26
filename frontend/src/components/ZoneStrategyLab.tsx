import { Link } from "react-router-dom";
import { ErrorNote, Panel, Pill } from "./ui";
import { api } from "../lib/api";
import { when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const pct = (v: any, d = 2) => (v == null ? "–" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
const tone = (v: any) => (v == null ? "text-mute" : v > 0 ? "text-gain" : "text-loss");

/** Backtest of the zone retest strategy (Umar Punjabi's method) and its variations across an index. */
export default function ZoneStrategyLab({ universe, years, toast }: { universe: string; years: number; toast: { show: (m: string, t?: any) => void } }) {
  const lab = useApi<any>("/api/pattern-lab/strategy", { refetchInterval: (q: any) => (q.state.data?.job?.status === "running" ? 2000 : false) } as any);
  const run = useAct(() => api("/api/pattern-lab/strategy/run", { body: { universe, years } }), ["/api/pattern-lab/strategy"]);
  const make = useAct((variant: string) => api<any>("/api/pattern-lab/strategy/algo", { body: { variant } }), ["/api/algos"]);
  const d = lab.data, res = d?.latest, job = d?.job, prog = d?.progress;
  const running = job?.status === "running";
  return (
    <Panel title="Zone retest strategy · Umar Punjabi method" pad={false}
      aside={res ? <span className="text-[13px] text-mute">{res.stocks} stocks · {res.period.from} to {res.period.to} · run {when(res.at)}</span> : undefined}>
      <div className="space-y-2 px-4 pt-3 text-[13px] text-parch-dim">
        <ol className="list-decimal space-y-0.5 pl-5">
          <li><b className="text-parch">Bias first:</b> trade only when structure is bullish (close above the last swing high) on this and the weekly chart.</li>
          <li><b className="text-parch">Strong zones only:</b> where a move of 2×ATR+ that broke structure started, or a resistance broken aggressively.</li>
          <li><b className="text-parch">Liquidity:</b> a zone visited twice already is skipped unless the retest sweeps the lows first.</li>
          <li><b className="text-parch">Confirmation:</b> price back in the zone and a bullish candle closes there. No confirmation, no trade.</li>
          <li><b className="text-parch">Risk:</b> stop beyond the zone, target the next strong resistance, at least 1:2. Clear area ahead = hold for the bigger move.</li>
        </ol>
        <div className="flex flex-wrap items-center gap-3 pt-1">
          <button className="btn-primary" disabled={running || run.isPending}
            onClick={() => run.mutate(undefined, { onError: (er) => toast.show(er.message, "loss") })}>{running ? "Testing…" : res ? "Test again" : "Test the strategy"}</button>
          <span className="text-mute">Uses the stocks and years chosen above.</span>
        </div>
        {running && prog && (
          <div>
            <div className="text-sm">{prog.stage}: {prog.done} / {prog.total}</div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-dusk-line"><div className="h-full rounded-full bg-saffron" style={{ width: `${prog.total ? (prog.done / prog.total) * 100 : 0}%` }} /></div>
          </div>)}
        {job?.status === "failed" && <p className="text-sm text-loss">The last run failed: {job.error}</p>}
        <ErrorNote error={lab.error} />
      </div>
      {res && (<>
        <p className="px-4 pt-3 text-[13px] text-mute">Entry at the next day's open, {res.method.stop}, target {res.method.target}, max {res.method.max_hold_days} days, {res.method.costs}. <b className="text-parch-dim">Works</b> = {res.method.works_if}.</p>
        <div className="overflow-x-auto">
          <table className="mt-2 w-full text-sm">
            <thead className="text-left text-[12px] text-mute"><tr>
              {["Version", "Trades", "Win rate", "Avg / trade", "Profit factor", "Planned R", "Earlier", "Latest", ""].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}
            </tr></thead>
            <tbody className="divide-y divide-dusk-line">{res.results.map((r: any) => (
              <tr key={r.variant} className={r.works ? "bg-gain-soft/20" : ""}>
                <td className="px-3 py-2 font-medium">{r.label}</td>
                <td className="px-3 py-2 tabular-nums">{r.trades}</td>
                <td className="px-3 py-2 tabular-nums">{r.trades ? `${r.win_rate}%` : "–"}</td>
                <td className={`px-3 py-2 tabular-nums ${tone(r.avg_return_pct)}`}>{pct(r.avg_return_pct)}</td>
                <td className="px-3 py-2 tabular-nums">{r.profit_factor ?? "–"}</td>
                <td className="px-3 py-2 tabular-nums">{r.avg_planned_rr ? `1:${r.avg_planned_rr}` : "–"}</td>
                <td className={`px-3 py-2 tabular-nums ${tone(r.early?.avg_return_pct)}`}>{pct(r.early?.avg_return_pct)}</td>
                <td className={`px-3 py-2 tabular-nums ${tone(r.late?.avg_return_pct)}`}>{pct(r.late?.avg_return_pct)}</td>
                <td className="px-3 py-2 text-right">
                  <button className={`${r.works ? "btn-ghost" : "btn-ghost opacity-70"} py-1 text-[13px]`} disabled={make.isPending} onClick={() => make.mutate(r.variant, {
                    onSuccess: (o: any) => toast.show(`Draft algo “${o.name}” created for ${o.symbols} stocks: open Algo builder to backtest and start it in PAPER`, "ok"),
                    onError: (er) => toast.show(er.message, "loss") })}>Make algo</button>
                  {!r.works && <div className="mt-0.5"><Pill tone="mute">{r.trades < 40 ? "too few trades" : "no edge yet"}</Pill></div>}
                </td>
              </tr>))}</tbody>
          </table>
        </div>
      </>)}
      <p className="px-4 py-3 text-[12px] text-mute">Syena trades this long-only (cash shares), so a bearish bias means “no new buys” rather than shorting. Algos start as drafts in <Link to="/algos" className="text-saffron hover:underline">Algo builder</Link>: backtest and run them in PAPER first.</p>
    </Panel>
  );
}
