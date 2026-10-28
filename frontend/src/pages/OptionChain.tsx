import { useEffect, useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorNote, Field, Loading, Modal, Page, Panel, Stat, useToast } from "../components/ui";
import OptionTrades from "../components/OptionTrades";
import NiftyStrategy from "../components/NiftyStrategy";
import { api } from "../lib/api";
import { useAct } from "../lib/hooks";
import { when } from "../lib/format";
import { useApi } from "../lib/hooks";

const n = (v: any, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));
const lakh = (v: any) => (v === null || v === undefined ? "–" : Math.abs(v) >= 1e5 ? `${(v / 1e5).toFixed(2)}L` : n(v, 0));
const signed = (v: any, f = lakh) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${f(v)}`);
const BUILD: Record<string, string> = { "long build-up": "text-gain", "short covering": "text-gain", "short build-up": "text-loss", "long unwinding": "text-loss" };

function Side({ o, max, side, onBuy }: { o: any; max: number; side: "ce" | "pe"; onBuy: () => void }) {
  if (!o) return <><td colSpan={6} className="px-2 text-center text-mute">–</td></>;
  const w = max ? Math.round((o.oi / max) * 100) : 0;
  const cells = [
    <td key="oi" className="relative px-2 py-1.5 tabular-nums">
      <span className={`absolute inset-y-1 ${side === "ce" ? "right-0" : "left-0"} rounded-sm ${side === "ce" ? "bg-loss/20" : "bg-gain/20"}`} style={{ width: `${w}%` }} />
      <span className="relative">{lakh(o.oi)}</span></td>,
    <td key="ch" className={`px-2 tabular-nums ${o.oi_change > 0 ? "text-gain" : o.oi_change < 0 ? "text-loss" : "text-mute"}`} title={o.buildup ?? ""}>{signed(o.oi_change)}{o.buildup && <div className={`text-[10px] leading-tight ${BUILD[o.buildup]}`}>{o.buildup}</div>}</td>,
    <td key="v" className="px-2 tabular-nums text-parch-dim">{lakh(o.volume)}</td>,
    <td key="iv" className="px-2 tabular-nums text-parch-dim">{n(o.iv, 1)}</td>,
    <td key="d" className="px-2 tabular-nums text-mute">{n(o.delta, 2)}</td>,
    <td key="l" className="group px-2 tabular-nums font-medium">
      <button type="button" onClick={onBuy} title="Buy (paper)" className={`mr-1.5 rounded px-1.5 text-[11px] font-semibold opacity-40 group-hover:opacity-100 ${side === "ce" ? "bg-gain/20 text-gain" : "bg-loss/20 text-loss"}`}>B</button>{n(o.ltp)}<span className={`ml-1 text-[11px] ${o.change > 0 ? "text-gain" : o.change < 0 ? "text-loss" : "text-mute"}`}>{o.change != null ? signed(o.change, (x) => n(x)) : ""}</span></td>,
  ];
  return <>{side === "ce" ? cells : [...cells].reverse()}</>;
}

export default function OptionChain() {
  const [u, setU] = useState("NIFTY");
  const [exp, setExp] = useState("");
  const [strikes, setStrikes] = useState(15);
  const und = useApi<any>("/api/options/underlyings", { retry: false } as any);
  const q = useApi<any>(`/api/options/chain?underlying=${encodeURIComponent(u)}&strikes=${strikes}${exp ? `&expiry=${exp}` : ""}`, { refetchInterval: 30_000, retry: false } as any);
  const c = q.data;
  useEffect(() => setExp(""), [u]);
  const maxOi = useMemo(() => Math.max(1, ...(c?.rows ?? []).flatMap((r: any) => [r.ce?.oi ?? 0, r.pe?.oi ?? 0])), [c]);
  const chart = (c?.rows ?? []).map((r: any) => ({ strike: r.strike, "Call OI": r.ce?.oi ?? 0, "Put OI": r.pe?.oi ?? 0 }));
  const s = c?.summary;
  const toast = useToast();
  const [buy, setBuy] = useState<any>(null);
  const [lots, setLots] = useState(1);
  const [stop, setStop] = useState(30);
  const [target, setTarget] = useState(60);
  const order = useAct((b: any) => api<any>("/api/options/order", { body: b }), ["/api/options/trades"]);
  return (
    <Page title="Option chain" intro="Calls and puts around the current price with open interest, change in OI, volume, implied volatility and delta, and what the positioning suggests. Prices from your broker, refreshed every 30 seconds. Syena paper-trades options: it buys calls when bullish and puts when bearish (news verdicts, algos set to options, or your clicks: hover a price and press B).">
      <div className="space-y-6">
        <Panel>
          <div className="flex flex-wrap items-end gap-3">
            <Field label="Underlying">
              <select className="input" value={u} onChange={(e) => setU(e.target.value)}>
                <optgroup label="Indices">{(und.data?.indices ?? ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]).map((x: string) => <option key={x}>{x}</option>)}</optgroup>
                {und.data?.stocks?.length > 0 && <optgroup label="Stocks">{und.data.stocks.map((x: string) => <option key={x}>{x}</option>)}</optgroup>}
              </select></Field>
            <Field label="Expiry">
              <select className="input" value={exp || c?.expiry || ""} onChange={(e) => setExp(e.target.value)}>
                {(c?.expiries ?? []).map((x: string) => <option key={x} value={x}>{new Date(x).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" })}</option>)}
              </select></Field>
            <Field label="Strikes each side">
              <select className="input" value={strikes} onChange={(e) => setStrikes(Number(e.target.value))}>{[10, 15, 20, 30].map((x) => <option key={x}>{x}</option>)}</select></Field>
            {c && <span className="pb-2 text-[13px] text-mute">Updated {when(c.at)} · {c.days_to_expiry} days to expiry · lot {c.lot_size}</span>}
          </div>
          <ErrorNote error={und.error ?? q.error} />
        </Panel>

        {q.isLoading ? <Loading /> : !c ? <Empty title="No chain">Pick an underlying. The option list loads from your broker the first time each day (about a minute).</Empty> : (<>
          <Panel><div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
            <Stat label={`${c.underlying} spot`} value={n(c.spot)} />
            <Stat label="Put-call ratio (OI)" value={n(s.pcr)} sub={s.pcr > 1.2 ? "put-heavy: support" : s.pcr < 0.8 ? "call-heavy: ceiling" : "balanced"} />
            <Stat label="Max pain" value={n(s.max_pain, 0)} />
            <Stat label="Resistance (call OI)" value={n(s.resistance, 0)} />
            <Stat label="Support (put OI)" value={n(s.support, 0)} />
            <Stat label="Expected move" value={s.expected_move_pct != null ? `±${s.expected_move_pct}%` : "–"} sub={s.atm_iv ? `ATM IV ${s.atm_iv}% · straddle ₹${n(s.straddle)}` : undefined} />
          </div></Panel>
          {s.read?.length > 0 && <Panel title="What the chain says"><ul className="list-disc space-y-1 pl-5 text-sm text-parch-dim">{s.read.map((x: string) => <li key={x}>{x}</li>)}</ul>
            {c.oi_change_note && <p className="mt-2 text-[12px] text-mute">{c.oi_change_note}</p>}</Panel>}

          <Panel title="Chain" pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[980px] text-right text-[13px]">
                <thead className="text-[11px] text-mute">
                  <tr><th colSpan={6} className="px-2 pt-2 text-center text-loss">CALLS</th><th /><th colSpan={6} className="px-2 pt-2 text-center text-gain">PUTS</th></tr>
                  <tr>{["OI", "Chg OI", "Volume", "IV %", "Delta", "LTP"].map((h) => <th key={"c" + h} className="px-2 py-1.5 font-medium">{h}</th>)}
                    <th className="px-2 text-center font-medium">Strike</th>
                    {["LTP", "Delta", "IV %", "Volume", "Chg OI", "OI"].map((h) => <th key={"p" + h} className="px-2 py-1.5 font-medium">{h}</th>)}</tr>
                </thead>
                <tbody className="divide-y divide-dusk-line/60">{c.rows.map((r: any) => {
                  const itmCall = c.spot != null && r.strike < c.spot, atm = r.strike === c.atm;
                  return (
                    <tr key={r.strike} className={atm ? "bg-saffron/10" : ""}>
                      <Side o={r.ce} max={maxOi} side="ce" onBuy={() => setBuy({ ...r.ce, strike: r.strike, type: "CE" })} />
                      <td className={`px-3 text-center font-semibold tabular-nums ${atm ? "text-saffron" : "text-parch"} ${itmCall ? "bg-loss/5" : "bg-gain/5"}`}>{n(r.strike, 2)}</td>
                      <Side o={r.pe} max={maxOi} side="pe" onBuy={() => setBuy({ ...r.pe, strike: r.strike, type: "PE" })} />
                    </tr>);
                })}</tbody>
              </table>
            </div>
            <p className="px-4 py-3 text-[12px] text-mute">Bars show each strike's open interest against the biggest one. Hover a change-in-OI figure for its build-up (price up + OI up = long build-up; price down + OI up = short build-up; price up + OI down = short covering; price down + OI down = long unwinding). IV and delta are worked out with Black-Scholes from the mid price.</p>
          </Panel>


          <Panel title="Open interest by strike">
            <div className="h-72"><ResponsiveContainer width="100%" height="100%">
              <BarChart data={chart} margin={{ left: 8, right: 8 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="strike" tick={{ fontSize: 11 }} interval="preserveStartEnd" />
                <YAxis tickFormatter={(v) => lakh(v)} tick={{ fontSize: 11 }} width={56} />
                <Tooltip formatter={(v: any) => lakh(v)} />
                <Legend />
                {c.spot && <ReferenceLine x={c.atm} stroke="#F2A93B" strokeDasharray="4 3" label={{ value: "ATM", fill: "#F2A93B", fontSize: 11, position: "top" }} />}
                <Bar dataKey="Call OI" fill="#F2685A" radius={[3, 3, 0, 0]} />
                <Bar dataKey="Put OI" fill="#3FC48A" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer></div>
          </Panel>
        </>)}
        <NiftyStrategy />
        <OptionTrades />
      </div>
      {toast.node}
      <Modal open={!!buy} onClose={() => setBuy(null)} title={buy ? `Buy ${c?.underlying} ${buy.strike} ${buy.type === "CE" ? "call" : "put"} (paper)` : ""}>
        {buy && c && (<div className="space-y-3 text-sm">
          <p className="text-parch-dim">Expiry {c.expiry} · premium ₹{n(buy.ask ?? buy.ltp)} × lot {c.lot_size} = <b>₹{n((buy.ask ?? buy.ltp) * c.lot_size * lots, 0)}</b> for {lots} lot{lots > 1 ? "s" : ""}{buy.iv ? ` · IV ${buy.iv}%` : ""}{buy.delta != null ? ` · delta ${buy.delta}` : ""}</p>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Lots"><input className="input" type="number" min={1} max={20} value={lots} onChange={(e) => setLots(Math.max(1, Number(e.target.value)))} /></Field>
            <Field label="Stop (% of premium)"><input className="input" type="number" min={5} max={95} value={stop} onChange={(e) => setStop(Number(e.target.value))} /></Field>
            <Field label="Target (% gain)"><input className="input" type="number" min={5} max={1000} value={target} onChange={(e) => setTarget(Number(e.target.value))} /></Field>
          </div>
          {(buy.ask ?? buy.ltp) * c.lot_size * lots > 15000 && <p className="text-[13px] text-saffron">Above the default ₹15,000 options budget per trade: Syena will reject it unless you raise options.max_premium in Settings.</p>}
          <p className="text-[12px] text-mute">Simulated fill at the ask with F&O charges. Syena closes it at the stop or target on the premium, or by 15:00 the day before expiry. The most you can lose is the premium.</p>
          <ErrorNote error={order.error} />
          <div className="flex gap-2">
            <button className="btn-primary" disabled={order.isPending} onClick={() => order.mutate({ underlying: c.underlying, expiry: c.expiry, strike: buy.strike, option_type: buy.type, lots, stop_pct: stop, target_pct: target },
              { onSuccess: (r: any) => { toast.show(r.summary, r.type === "PLACED" ? "ok" : "loss"); setBuy(null); } })}>Buy (paper)</button>
            <button className="btn-ghost" onClick={() => setBuy(null)}>Cancel</button>
          </div>
        </div>)}
      </Modal>
    </Page>
  );
}
