import { FormEvent, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorNote, Field, Json, Loading, Modal, Page, Panel, Pill, Stat, Table, Trail, decisionTone, useToast } from "../components/ui";
import { api } from "../lib/api";
import { day, money, num, ratio, signed, tone, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const examples = [
  "buy when SMA 20 crosses above SMA 50",
  "ema(50) > ema(200)",
  "price closes below lower Bollinger band and RSI < 30",
  "MACD bullish crossover with volume above 20-day average",
];
const timeframes = ["5minute", "15minute", "30minute", "60minute", "day"];

export default function Algos() {
  const { id } = useParams();
  return id ? <Inspector id={Number(id)} /> : <Builder />;
}

function Builder() {
  const nav = useNavigate();
  const [f, setF] = useState({ text: "", style: "SWING", run_mode: "PAPER", symbols: "INFY, TCS", timeframe: "day", name: "" });
  const [result, setResult] = useState<any>(null);
  const list = useApi<any[]>("/api/algos");
  const build = useAct((body: any) => api("/api/algos", { body }), ["/api/algos"]);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    build.mutate({ ...f, symbols: f.symbols.split(/[,\s]+/).filter(Boolean), name: f.name || null }, { onSuccess: setResult });
  };

  return (
    <Page title="Algo builder" intro="Describe a rule in plain English. You'll see exactly how it was understood before anything is backtested or run.">
      <Panel title="New rule">
        <form onSubmit={submit} className="space-y-4">
          <Field label="Your rule" hint="Include exits and risk if you like, e.g. “…, sell when RSI > 70, stop loss 2%, target 5%”.">
            <textarea className="input min-h-[96px] font-medium" required maxLength={2000} value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })}
              placeholder="buy when SMA 20 crosses above SMA 50" />
          </Field>
          <div className="flex flex-wrap gap-2">
            {examples.map((x) => <button type="button" key={x} className="rounded-md bg-dusk px-2.5 py-1 text-sm text-parch-dim hover:text-parch" onClick={() => setF({ ...f, text: x })}>{x}</button>)}
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Field label="Mode"><select className="input" value={f.run_mode} onChange={(e) => setF({ ...f, run_mode: e.target.value })}>
              <option value="BACKTEST">Backtest only</option><option value="PAPER">Paper</option><option value="LIVE">Live (after the gate)</option></select></Field>
            <Field label="Style"><select className="input" value={f.style} onChange={(e) => setF({ ...f, style: e.target.value })}>
              <option value="INTRADAY">Intraday (MIS)</option><option value="SWING">Swing (CNC)</option></select></Field>
            <Field label="Candles"><select className="input" value={f.timeframe} onChange={(e) => setF({ ...f, timeframe: e.target.value })}>
              {timeframes.map((t) => <option key={t} value={t}>{t === "day" ? "daily" : t.replace("minute", " min")}</option>)}</select></Field>
            <Field label="Stocks (NSE symbols)"><input className="input" value={f.symbols} onChange={(e) => setF({ ...f, symbols: e.target.value.toUpperCase() })} /></Field>
            <Field label="Name (optional)"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          </div>
          <ErrorNote error={build.error} />
          <button className="btn-primary" disabled={build.isPending}>{build.isPending ? "Reading your rule…" : "Build"}</button>
        </form>
        {result && <ParseResult parse={result.parse} algo={result.algo} onOpen={() => nav(`/algos/${result.algo.id}`)} />}
      </Panel>

      <Panel title="Your algos" pad={false}>
        <Table rows={list.data ?? []} rowKey={(r: any) => r.id} onRow={(r: any) => nav(`/algos/${r.id}`)}
          empty={<Empty title="No algos yet">Build your first rule above.</Empty>}
          cols={[
            { key: "name", label: "Name", render: (r: any) => <span className="font-semibold">{r.name}</span> },
            { key: "kind", label: "Kind", render: (r: any) => r.kind === "news" ? "AI news" : "rule" },
            { key: "status", label: "Status", render: (r: any) => <Pill tone={r.status === "RUNNING" ? "gain" : r.status === "DRAFT" ? "warn" : "mute"}>{r.status.toLowerCase()}</Pill> },
            { key: "run_mode", label: "Mode", render: (r: any) => `${r.run_mode.toLowerCase()}${r.live_enabled ? " (live enabled)" : ""}` },
            { key: "style", label: "Style", render: (r: any) => r.style.toLowerCase() },
            { key: "symbols", label: "Stocks", render: (r: any) => (r.symbols ?? []).join(", ") || "–" },
            { key: "metrics", label: "Backtest", render: (r: any) => r.metrics ? `${r.metrics.trades} trades, ${ratio(r.metrics.win_rate)} wins` : "–" },
          ]} />
      </Panel>
    </Page>
  );
}

function ParseResult({ parse, algo, onOpen }: { parse: any; algo?: any; onOpen?: () => void }) {
  if (parse.status !== "ok") {
    return (
      <div className="mt-4 rounded-lg border border-saffron/50 bg-saffron-soft/40 p-4">
        <div className="font-semibold">{parse.reason === "unsupported" ? "That isn't supported yet" : "I need one detail before I can build this"}</div>
        <p className="mt-1">{parse.question}</p>
        <p className="mt-2 text-sm text-parch-dim">Rephrase the rule above and build again. Nothing was guessed.</p>
      </div>
    );
  }
  return (
    <div className="mt-4 rounded-lg border border-gain/40 bg-gain-soft/30 p-4">
      <div className="mb-2 font-semibold">Here's how I read it. Check it before backtesting.</div>
      <pre className="whitespace-pre-wrap rounded-md bg-dusk p-3 font-sans text-[15px] leading-relaxed">{parse.readable}</pre>
      {parse.notes?.length > 0 && <ul className="mt-2 list-disc pl-5 text-sm text-parch-dim">{parse.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>}
      <p className="mt-2 text-[13px] text-mute">Parsed by {parse.source === "llm" ? "the AI parser (validated against the schema)" : "the built-in grammar"}.</p>
      {algo && onOpen && <button className="btn-primary mt-3" onClick={onOpen}>Looks right: open to backtest</button>}
    </div>
  );
}

function Inspector({ id }: { id: number }) {
  const toast = useToast();
  const { data, error, isLoading } = useApi<any>(`/api/algos/${id}`);
  const bt = useApi<any>(`/api/algos/${id}/backtest`);
  const live = useApi<any[]>(data?.algo?.status === "RUNNING" || data?.algo?.parsed_rule ? `/api/algos/${id}/live-values` : null, { retry: false, refetchInterval: 60_000 });
  const inv = [`/api/algos`];
  const runBt = useAct((days: number) => api(`/api/algos/${id}/backtest?days=${days}`, { method: "POST" }), inv);
  const start = useAct(() => api(`/api/algos/${id}/start`, { method: "POST" }), inv);
  const stop = useAct(() => api(`/api/algos/${id}/stop`, { method: "POST" }), inv);
  const patch = useAct((b: any) => api(`/api/algos/${id}`, { method: "PATCH", body: b }), inv);
  const editRule = useAct((rule: any) => api(`/api/algos/${id}/rule`, { method: "PUT", body: rule }), inv);
  const disableLive = useAct(() => api(`/api/algos/${id}/disable-live`, { method: "POST" }), inv);
  const [liveOpen, setLiveOpen] = useState(false);
  const [ruleText, setRuleText] = useState<string | null>(null);
  const [days, setDays] = useState(365);

  if (isLoading) return <Page title="Algo"><Loading /></Page>;
  if (error || !data) return <Page title="Algo"><ErrorNote error={error} /></Page>;
  const a = data.algo;
  const report = bt.data;
  const running = a.status === "RUNNING";
  const err = runBt.error || start.error || stop.error || patch.error || editRule.error;

  return (
    <Page title={a.name} intro={<Link to="/algos" className="text-saffron hover:underline">All algos</Link>}
      actions={a.kind === "rule" && <>
        {running ? <button className="btn-ghost" onClick={() => stop.mutate(undefined)}>Stop</button>
          : <button className="btn-primary" disabled={!report || a.run_mode === "BACKTEST"} onClick={() => start.mutate(undefined, { onSuccess: () => toast.show(`Started in ${a.live_enabled ? "LIVE" : "PAPER"}`) })}>
            Start ({a.live_enabled ? "live" : "paper"})</button>}
        {a.live_enabled ? <button className="btn-ghost" onClick={() => disableLive.mutate(undefined)}>Disable live</button>
          : a.run_mode === "LIVE" && <button className="btn-ghost" onClick={() => setLiveOpen(true)}>Enable live…</button>}
      </>}>
      <ErrorNote error={err} />
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Rule" aside={<Pill tone={running ? "gain" : "mute"}>{a.status.toLowerCase()}</Pill>}>
          <div className="space-y-3">
            <p className="text-parch-dim">“{a.english_text}”</p>
            {a.readable ? <pre className="whitespace-pre-wrap rounded-md bg-dusk p-3 font-sans text-[15px]">{a.readable}</pre> : <p className="text-loss">No confirmed rule yet.</p>}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Field label="Mode"><select className="input" disabled={running} value={a.run_mode} onChange={(e) => patch.mutate({ run_mode: e.target.value })}>
                <option value="BACKTEST">Backtest</option><option value="PAPER">Paper</option><option value="LIVE">Live</option></select></Field>
              <Field label="Style"><select className="input" disabled={running} value={a.style} onChange={(e) => patch.mutate({ style: e.target.value })}>
                <option value="INTRADAY">Intraday</option><option value="SWING">Swing</option></select></Field>
              <Field label="Candles"><select className="input" disabled={running} value={a.timeframe} onChange={(e) => patch.mutate({ timeframe: e.target.value })}>
                {timeframes.map((t) => <option key={t} value={t}>{t}</option>)}</select></Field>
              <Field label="Stocks"><input className="input" disabled={running} defaultValue={(a.symbols ?? []).join(", ")}
                onBlur={(e) => patch.mutate({ symbols: e.target.value.split(/[,\s]+/).filter(Boolean) })} /></Field>
            </div>
            {a.kind === "rule" && a.parsed_rule && (ruleText === null ? (
              <button className="text-sm text-saffron hover:underline" disabled={running} onClick={() => setRuleText(JSON.stringify(a.parsed_rule, null, 2))}>Edit the rule JSON</button>
            ) : (
              <div className="space-y-2">
                <textarea className="input min-h-[220px] font-mono text-[13px]" value={ruleText} onChange={(e) => setRuleText(e.target.value)} aria-label="Rule JSON" />
                <div className="flex gap-2">
                  <button className="btn-primary" onClick={() => { try { editRule.mutate(JSON.parse(ruleText), { onSuccess: () => setRuleText(null) }); } catch { toast.show("That isn't valid JSON.", "loss"); } }}>Save rule</button>
                  <button className="btn-ghost" onClick={() => setRuleText(null)}>Cancel</button>
                </div>
                <p className="text-[13px] text-mute">Saving resets the backtest and the LIVE gate.</p>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Live indicator values" aside={<span className="text-sm text-mute">last completed candle</span>}>
          {live.error ? <p className="text-parch-dim">{(live.error as Error).message}</p> : !live.data ? <Loading /> : (
            <div className="space-y-4">
              {live.data.map((s: any) => (
                <div key={s.symbol}>
                  <div className="mb-1 flex items-center gap-2"><span className="font-semibold">{s.symbol}</span>
                    {s.error ? <span className="text-sm text-mute">{s.error}</span> : <>
                      <Pill tone={s.entry_now ? "gain" : "mute"}>{s.entry_now ? "entry condition true" : "entry condition false"}</Pill>
                      {s.exit_now != null && <Pill tone={s.exit_now ? "warn" : "mute"}>{s.exit_now ? "exit true" : "exit false"}</Pill>}
                      <span className="text-[13px] text-mute">{when(s.bar)}</span></>}
                  </div>
                  {s.values && <table className="w-full text-sm"><tbody>{s.values.map((v: any) => (
                    <tr key={v.operand}><td className="py-0.5 text-parch-dim">{v.operand}</td><td className="text-right">{v.value ?? "–"}</td></tr>))}</tbody></table>}
                </div>
              ))}
            </div>
          )}
        </Panel>
      </div>

      {a.kind === "rule" && (
        <Panel title="Backtest" aside={<div className="flex items-center gap-2">
          <select className="input w-auto py-1.5" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period">
            {[90, 180, 365, 730, 1095].map((d) => <option key={d} value={d}>{d >= 365 ? `${d / 365} year${d > 365 ? "s" : ""}` : `${d} days`}</option>)}
          </select>
          <button className="btn-primary py-1.5" disabled={runBt.isPending || running || !a.parsed_rule} onClick={() => runBt.mutate(days)}>{runBt.isPending ? "Running…" : report ? "Re-run" : "Run backtest"}</button>
        </div>}>
          {!report ? <p className="text-parch-dim">Run a backtest on historical Zerodha candles. Fills happen at the next candle's open with slippage and full charges; the ₹ position-size band applies.</p> : <BacktestReport r={report} />}
        </Panel>
      )}

      {data.gate && (
        <Panel title="LIVE gate" aside={<span className="text-sm text-mute">thresholds in Settings</span>}>
          <Trail sequential={false} checks={data.gate} outcome={data.gate.every((c: any) => c.passed) ? "Eligible for LIVE" : "Not eligible for LIVE yet"} outcomeTone={data.gate.every((c: any) => c.passed) ? "gain" : "loss"} />
        </Panel>
      )}

      <Panel title="Signals and decisions" pad={false}>
        <Table rows={data.decisions} rowKey={(r: any) => r.id} empty={<p className="p-1 text-parch-dim">No signals fired yet.</p>}
          cols={[
            { key: "ts", label: "When", render: (r: any) => when(r.ts) },
            { key: "type", label: "Decision", render: (r: any) => <Link className="hover:underline" to={`/decisions/${r.id}`}><Pill tone={decisionTone(r.type)}>{r.type.toLowerCase()}</Pill></Link> },
            { key: "symbol", label: "Stock" },
            { key: "mode", label: "Mode" },
            { key: "summary", label: "Summary", className: "whitespace-normal min-w-[260px] text-parch-dim" },
          ]} />
      </Panel>
      <LiveEnable open={liveOpen} onClose={() => setLiveOpen(false)} id={id} />
      {toast.node}
    </Page>
  );
}

function BacktestReport({ r }: { r: any }) {
  const m = r.metrics;
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Trades" value={m.trades} />
        <Stat label="Win rate" value={ratio(m.win_rate)} />
        <Stat label="Total P&L" value={signed(m.total_pnl)} valueClass={tone(m.total_pnl)} sub={`${num(m.return_pct)}% on ₹1L`} />
        <Stat label="Max drawdown" value={m.max_drawdown_pct == null ? "–" : `${num(m.max_drawdown_pct)}%`} />
        <Stat label="Sharpe" value={num(m.sharpe)} />
        <Stat label="Profit factor" value={num(m.profit_factor)} />
        <Stat label="Charges paid" value={money(m.total_charges, false)} />
      </div>
      {r.equity?.length > 1 && (
        <div className="h-64" aria-label="Equity curve">
          <ResponsiveContainer>
            <LineChart data={r.equity} margin={{ left: 8, right: 8, top: 8 }}>
              <CartesianGrid stroke="#2F384C" vertical={false} />
              <XAxis dataKey="ts" tickFormatter={(v) => day(v).slice(0, 6)} stroke="#8B93A7" fontSize={12} minTickGap={40} />
              <YAxis stroke="#8B93A7" fontSize={12} width={70} tickFormatter={(v) => `₹${(v / 1000).toFixed(1)}k`} domain={["auto", "auto"]} />
              <Tooltip contentStyle={{ background: "#1B2130", border: "1px solid #2F384C", borderRadius: 8 }} labelFormatter={(v) => day(v as string)} formatter={(v: number) => [money(v), "Equity"]} />
              <Line dataKey="equity" stroke="#E8A33D" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      <p className="text-sm text-mute">{r.config?.symbols?.join(", ")} · {r.config?.timeframe} candles · {day(r.config?.start)} to {day(r.config?.end)}{r.skipped?.length ? ` · ${r.skipped.length} signals skipped (sizing/cutoff)` : ""}</p>
      <details>
        <summary className="cursor-pointer text-saffron">Trade list ({r.trades.length})</summary>
        <Table rows={r.trades} cols={[
          { key: "symbol", label: "Stock" },
          { key: "entry_ts", label: "Entry", render: (t: any) => `${when(t.entry_ts)} @ ${t.entry}` },
          { key: "exit_ts", label: "Exit", render: (t: any) => `${when(t.exit_ts)} @ ${t.exit}` },
          { key: "qty", label: "Qty", align: "right" },
          { key: "exit_reason", label: "Why", render: (t: any) => t.exit_reason.replace(/_/g, " ") },
          { key: "charges", label: "Charges", align: "right", render: (t: any) => money(t.charges) },
          { key: "pnl", label: "P&L", align: "right", render: (t: any) => <span className={tone(t.pnl)}>{signed(t.pnl)}</span> },
        ]} />
      </details>
      <Json value={r.per_symbol} label="Per-stock metrics" />
    </div>
  );
}

function LiveEnable({ open, onClose, id }: { open: boolean; onClose: () => void; id: number }) {
  const [f, setF] = useState({ password: "", totp: "" });
  const go = useAct((b: any) => api(`/api/algos/${id}/enable-live`, { body: b }), ["/api/algos"]);
  return (
    <Modal open={open} onClose={onClose} title="Enable this algo for LIVE">
      <p className="mb-3 text-parch-dim">Only possible once every LIVE gate check passes. Real orders are placed only while the whole system is in LIVE mode.</p>
      <div className="space-y-3">
        <Field label="Password"><input className="input" type="password" autoComplete="current-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        <Field label="Authenticator code"><input className="input" inputMode="numeric" maxLength={6} value={f.totp} onChange={(e) => setF({ ...f, totp: e.target.value.replace(/\D/g, "") })} /></Field>
        <ErrorNote error={go.error} />
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn-danger" disabled={go.isPending} onClick={() => go.mutate(f, { onSuccess: onClose })}>Enable live</button>
        </div>
      </div>
    </Modal>
  );
}
