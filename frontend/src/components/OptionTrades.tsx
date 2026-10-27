import { Link } from "react-router-dom";
import { Empty, Panel, Pill, Stat } from "./ui";
import { money, when } from "../lib/format";
import { useApi } from "../lib/hooks";

/** Scoreboard of Syena's paper option trades: totals, per strategy, and every position. */
export default function OptionTrades() {
  const q = useApi<any>("/api/options/trades", { refetchInterval: 30_000 } as any);
  const d = q.data;
  if (!d) return null;
  const s = d.summary;
  const tone = (v: any) => (v > 0 ? "text-gain" : v < 0 ? "text-loss" : "");
  return (
    <Panel title="Syena's option trades (paper)" aside={<span className="text-[12px] text-mute">buys calls when bullish, puts when bearish; never writes options</span>}>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-6">
        <Stat label="Closed trades" value={s.trades} />
        <Stat label="Realised P&L" value={<span className={tone(s.pnl)}>{money(s.pnl)}</span>} />
        <Stat label="Open" value={s.open} sub={s.open ? <span className={tone(s.unrealised)}>{money(s.unrealised)} unrealised</span> : undefined} />
        <Stat label="Win rate" value={s.win_rate != null ? `${s.win_rate}%` : "–"} />
        <Stat label="Avg win / loss" value={s.avg_win != null || s.avg_loss != null ? `${money(s.avg_win ?? 0)} / ${money(s.avg_loss ?? 0)}` : "–"} />
        <Stat label="Profit factor" value={s.profit_factor ?? "–"} />
      </div>
      {d.by_strategy.length > 0 && (
        <table className="mt-4 w-full text-sm"><thead className="text-left text-[12px] text-mute"><tr>{["Strategy", "Trades", "Win rate", "P&L", "Return on premium"].map((h) => <th key={h} className="py-1.5 font-medium">{h}</th>)}</tr></thead>
          <tbody className="divide-y divide-dusk-line">{d.by_strategy.map((b: any) => (
            <tr key={b.strategy}><td className="py-1.5">{b.strategy}</td><td className="tabular-nums">{b.trades}</td><td className="tabular-nums">{b.win_rate ?? "–"}%</td>
              <td className={`tabular-nums ${tone(b.pnl)}`}>{money(b.pnl)}</td><td className={`tabular-nums ${tone(b.return_pct)}`}>{b.return_pct ?? "–"}%</td></tr>))}</tbody></table>)}
      {!d.trades.length ? <div className="mt-4"><Empty title="No option trades yet">They start with the next news verdict on an F&O stock, an algo set to "Call options", or a Buy from the chain above.</Empty></div> : (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead className="text-left text-[12px] text-mute"><tr>{["Contract", "Strategy", "Lots", "Entry", "Now / exit", "Stop · target", "P&L", "Status", "Opened"].map((h) => <th key={h} className="px-2 py-1.5 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-dusk-line">{d.trades.slice(0, 60).map((t: any) => (
              <tr key={t.id}>
                <td className="px-2 py-1.5"><span className="font-medium">{t.underlying} {t.strike} {t.type === "CE" ? "call" : "put"}</span><div className="text-[11px] text-mute">exp {t.expiry} · spot then {t.spot_at_entry ?? "–"}{t.iv_at_entry ? ` · IV ${t.iv_at_entry}%` : ""}</div></td>
                <td className="px-2 text-parch-dim">{t.strategy}</td>
                <td className="px-2 tabular-nums">{t.lots ?? "–"}</td>
                <td className="px-2 tabular-nums">{t.entry ?? "–"}</td>
                <td className="px-2 tabular-nums">{t.exit ?? t.last ?? "–"}</td>
                <td className="px-2 tabular-nums text-mute">{t.stop} · {t.target}</td>
                <td className={`px-2 tabular-nums ${tone(t.pnl)}`}>{t.pnl != null ? money(t.pnl) : "–"}</td>
                <td className="px-2"><Pill tone={t.status === "OPEN" ? "info" : t.pnl > 0 ? "gain" : "loss"}>{t.status === "CLOSED" ? (t.exit_reason ?? "closed").replace(/_/g, " ") : t.status.toLowerCase()}</Pill></td>
                <td className="px-2 text-[12px] text-mute">{t.decision_id ? <Link className="hover:underline" to={`/decisions/${t.decision_id}`}>{when(t.opened)}</Link> : when(t.opened)}</td>
              </tr>))}</tbody>
          </table>
        </div>)}
    </Panel>
  );
}
