import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorNote, Field, Loading, Page, Panel, Pill, useToast } from "../components/ui";
import { api } from "../lib/api";
import { when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const pct = (v: any, d = 2) => (v == null ? "–" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
const tone = (v: any) => (v == null ? "text-mute" : v > 0 ? "text-gain" : "text-loss");

export default function PatternLab() {
  const [universe, setUniverse] = useState("nifty200");
  const [years, setYears] = useState(5);
  const [onlyWinners, setOnlyWinners] = useState(false);
  const toast = useToast();
  const lab = useApi<any>("/api/pattern-lab", { refetchInterval: (q: any) => (q.state.data?.job?.status === "running" ? 2000 : false) } as any);
  const run = useAct(() => api("/api/pattern-lab/run", { body: { universe, years } }), ["/api/pattern-lab"]);
  const make = useAct((b: { pattern: string; filter: string }) => api<any>("/api/pattern-lab/algo", { body: b }), ["/api/algos"]);
  const d = lab.data, res = d?.latest, job = d?.job, prog = d?.progress;
  const running = job?.status === "running";
  const rows = (res?.results ?? []).filter((r: any) => !onlyWinners || r.works);

  return (
    <Page title="Pattern lab" intro="Which candlestick patterns actually made money on NSE stocks, and in which setting? Every bullish pattern is backtested alone and with filters across a whole index, with charges and slippage, and judged on both the earlier and the latest part of the period.">
      {toast.node}
      <div className="space-y-6">
        <Panel>
          <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); run.mutate(undefined, { onError: (er) => toast.show(er.message, "loss") }); }}>
            <Field label="Stocks"><select className="input" value={universe} onChange={(e) => setUniverse(e.target.value)}>
              {Object.entries(d?.universes ?? { nifty200: "Nifty 200" }).map(([k, v]) => <option key={k} value={k}>{v as string}</option>)}</select></Field>
            <Field label="Years of history"><select className="input" value={years} onChange={(e) => setYears(Number(e.target.value))}>
              {[3, 5, 7, 10].map((y) => <option key={y} value={y}>{y} years</option>)}</select></Field>
            <button className="btn-primary" disabled={running || run.isPending}>{running ? "Running…" : res ? "Run again" : "Run the lab"}</button>
            <span className="text-[13px] text-mute">First run downloads prices: about 10–15 minutes for Nifty 200. It runs in the background.</span>
          </form>
          {running && prog && (
            <div className="mt-3">
              <div className="text-sm text-parch-dim">{prog.stage}: {prog.done} / {prog.total}</div>
              <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-dusk-line"><div className="h-full rounded-full bg-saffron" style={{ width: `${prog.total ? (prog.done / prog.total) * 100 : 0}%` }} /></div>
            </div>)}
          {job?.status === "failed" && <p className="mt-2 text-sm text-loss">The last run failed: {job.error}</p>}
          <ErrorNote error={lab.error} />
        </Panel>

        {lab.isLoading ? <Loading /> : !res ? <Empty title="No results yet">Run the lab to see which patterns have an edge.</Empty> : (
          <Panel title={`${res.winners} combination${res.winners === 1 ? "" : "s"} with a proven edge`} aside={<span className="text-[13px] text-mute">{res.stocks} stocks · {res.period.from} to {res.period.to} · run {when(res.at)}</span>} pad={false}>
            <div className="space-y-1 px-4 pt-3 text-[13px] text-mute">
              <p>Each test buys at the next day's open after the pattern, stop {res.method.stop}, target {res.method.target}, out after {res.method.max_hold_days} days at the latest, with {res.method.costs}.</p>
              <p><b className="text-parch-dim">Works</b> = {res.method.works_if}. Earlier = before {res.period.split}, latest = after.</p>
              <label className="flex items-center gap-2 pt-1 text-parch-dim"><input type="checkbox" checked={onlyWinners} onChange={(e) => setOnlyWinners(e.target.checked)} /> Show only combinations that work</label>
            </div>
            <div className="overflow-x-auto">
              <table className="mt-2 w-full text-sm">
                <thead className="text-left text-[12px] text-mute"><tr>
                  {["Pattern", "Setting", "Trades", "Win rate", "Avg / trade", "Profit factor", "Earlier", "Latest", ""].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}
                </tr></thead>
                <tbody className="divide-y divide-dusk-line">{rows.map((r: any) => (
                  <tr key={r.pattern + r.filter} className={r.works ? "bg-gain-soft/20" : ""}>
                    <td className="px-3 py-2 font-medium">{r.pattern_label}</td>
                    <td className="px-3 py-2 text-parch-dim">{r.filter_label}</td>
                    <td className="px-3 py-2 tabular-nums">{r.trades}</td>
                    <td className="px-3 py-2 tabular-nums">{r.trades ? `${r.win_rate}%` : "–"}</td>
                    <td className={`px-3 py-2 tabular-nums ${tone(r.avg_return_pct)}`}>{pct(r.avg_return_pct)}</td>
                    <td className="px-3 py-2 tabular-nums">{r.profit_factor ?? "–"}</td>
                    <td className={`px-3 py-2 tabular-nums ${tone(r.early?.avg_return_pct)}`}>{pct(r.early?.avg_return_pct)}</td>
                    <td className={`px-3 py-2 tabular-nums ${tone(r.late?.avg_return_pct)}`}>{pct(r.late?.avg_return_pct)}</td>
                    <td className="px-3 py-2 text-right">{r.works ? (
                      <button className="btn-ghost py-1 text-[13px]" disabled={make.isPending} onClick={() => make.mutate({ pattern: r.pattern, filter: r.filter }, {
                        onSuccess: (o: any) => toast.show(`Draft algo “${o.name}” created for ${o.symbols} stocks: open Algo builder to backtest and start it`, "ok"),
                        onError: (er) => toast.show(er.message, "loss") })}>Make algo</button>
                    ) : r.trades < 40 ? <span className="text-[12px] text-mute">too few trades</span> : <Pill tone="mute">no edge</Pill>}</td>
                  </tr>))}</tbody>
              </table>
            </div>
            <p className="px-4 py-3 text-[12px] text-mute">Past results don't guarantee future ones. Algos made here start as drafts in <Link to="/algos" className="text-saffron hover:underline">Algo builder</Link>: run them in PAPER first.</p>
          </Panel>)}
      </div>
    </Page>
  );
}
