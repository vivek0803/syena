import { Link } from "react-router-dom";
import { ErrorNote, Panel, Pill, Stat } from "./ui";
import { api } from "../lib/api";
import { money, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const n = (v: any, d = 1) => (v == null ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));

/** NIFTY daily options strategy: today's plan, recent days, and a backtest of the signal. */
export default function NiftyStrategy() {
  const q = useApi<any>("/api/options/nifty", { refetchInterval: 60_000 } as any);
  const bt = useAct((days: number) => api<any>("/api/options/nifty/backtest", { body: { days } }), ["/api/options/nifty"]);
  const d = q.data;
  if (!d) return null;
  const t = d.today, c = d.config, b = bt.data ?? d.backtest;
  const dirPill = (x: any) => x === "BUY" ? <Pill tone="gain">call bought</Pill> : x === "SELL" ? <Pill tone="loss">put bought</Pill> : <Pill tone="mute">no trade</Pill>;
  return (
    <Panel title="NIFTY daily options strategy (paper)" aside={<span className="text-[12px] text-mute">{c.enabled ? "on" : "off"} · {c.lots} lot · stop −{c.stop_pct}% · target +{c.target_pct}% · trailing · out by 15:10</span>}>
      <p className="text-sm text-parch-dim">Every trading day: bias from NIFTY's daily structure and 20-day EMA → opening range 09:15–09:30 → the first 5-minute close outside it between {c.entry_start} and {c.entry_end} buys the at-the-money weekly call (up) or put (down), if the bias and the put-call ratio don't argue against it. At most one trade a day.</p>
      {t ? (
        <div className="mt-3 rounded-xl border border-dusk-line/70 bg-dusk/50 p-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <b className="text-parch">Today</b>{dirPill(t.direction)}
            <span className="text-mute">bias <b className={t.bias === "bullish" ? "text-gain" : t.bias === "bearish" ? "text-loss" : "text-parch"}>{t.bias}</b></span>
            {t.or_high && <span className="text-mute">opening range {n(t.or_low)}–{n(t.or_high)}</span>}
            {t.pcr != null && <span className="text-mute">PCR {t.pcr}</span>}
            <span className="ml-auto text-[12px] text-mute">checked {when(t.at)}</span>
          </div>
          <ul className="mt-2 list-disc pl-5 text-[13px] text-parch-dim">{(t.reasons ?? []).map((r: string) => <li key={r}>{r}</li>)}{(t.bias_why ?? []).map((r: string) => <li key={r} className="text-mute">{r}</li>)}</ul>
          {t.decision_id && <Link to={`/decisions/${t.decision_id}`} className="mt-1 inline-block text-[13px] text-saffron hover:underline">{t.decision}</Link>}
        </div>) : <p className="mt-3 text-sm text-mute">No check yet today: it runs every 5 minutes from 09:30 on trading days.</p>}
      {d.history?.length > 1 && (
        <details className="mt-3 text-[13px]"><summary className="cursor-pointer text-parch-dim">Last {d.history.length} days</summary>
          <ul className="mt-2 space-y-1">{d.history.map((h: any) => <li key={h.day} className="flex flex-wrap gap-2"><span className="w-24 text-mute">{h.day}</span>{dirPill(h.direction)}<span className="text-mute">{h.bias} · {(h.reasons ?? []).at(-1)}</span></li>)}</ul></details>)}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="text-sm text-parch-dim">Backtest the signal on NIFTY's 5-minute candles:</span>
        {[30, 90, 180].map((x) => <button key={x} className="btn-ghost py-1 text-[13px]" disabled={bt.isPending} onClick={() => bt.mutate(x)}>{x} days</button>)}
        {bt.isPending && <span className="text-[13px] text-mute">downloading candles…</span>}
      </div>
      <ErrorNote error={bt.error} />
      {b && (
        <div className="mt-3">
          <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Stat label="Trades / days" value={`${b.trades} / ${b.days}`} />
            <Stat label="Win rate" value={b.win_rate != null ? `${b.win_rate}%` : "–"} />
            <Stat label="Avg NIFTY points" value={n(b.avg_points)} sub={`total ${n(b.total_points)}`} />
            <Stat label="Est. option P&L (1 lot)" value={<span className={b.est_total_pnl > 0 ? "text-gain" : b.est_total_pnl < 0 ? "text-loss" : ""}>{money(b.est_total_pnl)}</span>} />
            <Stat label="Profit factor (est.)" value={b.profit_factor ?? "–"} />
          </div>
          <p className="mt-2 text-[12px] text-mute">Historical option prices aren't available, so the option result is estimated from the index move (ATM delta 0.5 × points × lot of {b.lot_size}, minus premium decay and charges). The paper trades above show the real option P&L from here on.</p>
        </div>)}
    </Panel>
  );
}
