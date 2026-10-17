import { useEffect, useState } from "react";
import { ErrorNote, Field, Loading, Modal, ModeBadge, Page, Panel, Pill, Table, Tabs, useToast } from "../components/ui";
import { api } from "../lib/api";
import { money, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const sections: { id: string; label: string; keys: [string, string, string][] }[] = [
  { id: "trading", label: "Trading & risk", keys: [
    ["sizing", "Position size", "Every position uses an amount inside this band. Only you can change it."],
    ["risk", "Risk limits", "Breaching the LIVE daily loss halts trading and drops to PAPER."],
    ["trade_gate", "Trade gate", "What a signal must clear before an order is placed."],
    ["styles", "Intraday and swing", "Cutoff and square-off times are IST."],
    ["orders", "Orders", "Limit orders are preferred; market orders always carry market protection."],
    ["paper", "Paper trading", "Fills are simulated from live quotes with this slippage."]] },
  { id: "ai", label: "AI & news", keys: [
    ["models", "Model tiers", "Second opinion is required to agree in LIVE when set. A shadow model runs in parallel and is never traded."],
    ["ai_budget", "AI budget", "AI calls pause for the day once this is spent (evals excluded)."],
    ["model_pricing", "Model pricing (USD per million tokens: input, output)", "Used for cost tracking."],
    ["news", "News sources", "Feeds, watchlist and polling."],
    ["market_context", "Market context score", "Weights for Nifty, Sensex and India VIX."]] },
  { id: "gates", label: "Evals, gates & lessons", keys: [
    ["evals", "Eval thresholds", "Below these, CI fails and drift alerts fire."],
    ["live_gate", "LIVE gate per strategy", "A strategy must pass all of these before it can trade real money. Only you can change them."],
    ["lessons", "Lessons", "How much evidence a lesson needs and how many can be active."]] },
  { id: "ops", label: "Monitor & calendar", keys: [
    ["monitor", "Position monitor", ""], ["telegram", "Telegram summaries", ""],
    ["portfolio", "Portfolio limits", "Used for concentration warnings on Portfolio X-Ray."], ["holidays", "NSE holidays", "One date per line (YYYY-MM-DD)."]] },
];

export default function SettingsPage() {
  const [tab, setTab] = useState("mode");
  return (
    <Page title="Settings">
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "mode", label: "Mode" }, ...sections.map((s) => ({ id: s.id, label: s.label })), { id: "usage", label: "AI usage" }, { id: "security", label: "Security" }]} />
      {tab === "mode" && <ModeControl />}
      {sections.filter((s) => s.id === tab).map((s) => <SettingsGroup key={s.id} keys={s.keys} />)}
      {tab === "usage" && <AiUsage />}
      {tab === "security" && <Security />}
    </Page>
  );
}

function ModeControl() {
  const mode = useApi<any>("/api/mode");
  const audit = useApi<any[]>("/api/mode/audit");
  const settings = useApi<any>("/api/settings");
  const kite = useApi<any>("/api/kite/status");
  const inv = ["/api/mode", "/api/dashboard"];
  const paper = useAct(() => api("/api/mode/paper", { method: "POST" }), inv);
  const resume = useAct(() => api("/api/mode/resume", { method: "POST" }), inv);
  const [open, setOpen] = useState(false);
  const m = mode.data;
  if (!m) return <Loading />;
  return (
    <>
      <Panel title="Trading mode">
        <div className="flex flex-wrap items-center gap-4">
          <ModeBadge mode={m.mode} halted={m.halted} />
          <span className="text-parch-dim">since {when(m.updated_at)}</span>
        </div>
        <p className="mt-3 max-w-2xl text-parch-dim">
          PAPER runs everything exactly like LIVE except the real order. Every restart, an expired Zerodha token, the daily loss halt and repeated broker errors all switch back to PAPER automatically. Mode can only be changed here, never by config, CLI or Telegram.
        </p>
        <div className="mt-4 flex flex-wrap gap-2">
          {m.mode === "LIVE" ? <button className="btn-primary" onClick={() => paper.mutate(undefined)}>Switch to PAPER</button>
            : <button className="btn-danger" disabled={m.halted} onClick={() => setOpen(true)}>Switch to LIVE…</button>}
          {m.halted && <button className="btn-ghost" onClick={() => resume.mutate(undefined)}>Resume trading (stays PAPER)</button>}
        </div>
        {m.halted && <p className="mt-2 text-loss">Halted: {m.halt_reason}</p>}
        {!kite.data?.connected && m.mode !== "LIVE" && <p className="mt-2 text-sm text-mute">Zerodha must be connected before LIVE is allowed.</p>}
      </Panel>
      <Panel title="Mode changes" pad={false}>
        <Table rows={audit.data ?? []} cols={[
          { key: "created_at", label: "When", render: (r: any) => when(r.created_at) },
          { key: "change", label: "Change", render: (r: any) => `${r.from_mode} to ${r.to_mode}` },
          { key: "success", label: "Result", render: (r: any) => <Pill tone={r.success ? "gain" : "loss"}>{r.success ? "done" : "refused"}</Pill> },
          { key: "actor", label: "By" },
          { key: "reason", label: "Reason", className: "whitespace-normal" },
          { key: "ip", label: "IP", render: (r: any) => r.ip ?? "–" },
        ]} />
      </Panel>
      <GoLive open={open} onClose={() => setOpen(false)} risk={settings.data?.values?.risk} sizing={settings.data?.values?.sizing} />
    </>
  );
}

function GoLive({ open, onClose, risk, sizing }: { open: boolean; onClose: () => void; risk: any; sizing: any }) {
  const [f, setF] = useState({ password: "", totp: "", confirmation: "" });
  const go = useAct((b: any) => api("/api/mode/live", { body: b }), ["/api/mode", "/api/dashboard"]);
  useEffect(() => { if (!open) setF({ password: "", totp: "", confirmation: "" }); }, [open]);
  return (
    <Modal open={open} onClose={onClose} title="Switch to LIVE">
      <div className="space-y-4">
        <p>Real orders will be placed through Zerodha for strategies that passed their LIVE gate, and for manual invests you confirm.</p>
        {risk && sizing && (
          <dl className="grid grid-cols-2 gap-y-1 rounded-lg bg-dusk p-3 text-sm">
            <dt className="text-mute">Per position</dt><dd>{money(sizing.min_amount, false)} to {money(sizing.max_amount, false)}</dd>
            <dt className="text-mute">Max open positions</dt><dd>{risk.max_open_positions}</dd>
            <dt className="text-mute">Max trades per day</dt><dd>{risk.max_trades_per_day}</dd>
            <dt className="text-mute">Daily loss halt</dt><dd>{money(risk.max_daily_loss, false)}</dd>
            <dt className="text-mute">Max per stock</dt><dd>{money(risk.max_exposure_per_stock, false)}</dd>
          </dl>
        )}
        <Field label="Password"><input className="input" type="password" autoComplete="current-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></Field>
        <Field label="Authenticator code"><input className="input" inputMode="numeric" maxLength={6} value={f.totp} onChange={(e) => setF({ ...f, totp: e.target.value.replace(/\D/g, "") })} /></Field>
        <Field label='Type LIVE to confirm'><input className="input" value={f.confirmation} onChange={(e) => setF({ ...f, confirmation: e.target.value })} autoComplete="off" /></Field>
        <ErrorNote error={go.error} />
        <div className="flex justify-end gap-2">
          <button className="btn-ghost" onClick={onClose}>Stay in PAPER</button>
          <button className="btn-danger" disabled={f.confirmation !== "LIVE" || !f.password || f.totp.length !== 6 || go.isPending}
            onClick={() => go.mutate(f, { onSuccess: onClose })}>Switch to LIVE</button>
        </div>
      </div>
    </Modal>
  );
}

const human = (k: string) => k.replace(/_/g, " ").replace(/\bpct\b/, "%").replace(/\bbps\b/, "(bps)").replace(/\busd\b/, "(USD)").replace(/^./, (c) => c.toUpperCase());

function SettingsGroup({ keys }: { keys: [string, string, string][] }) {
  const { data, error } = useApi<any>("/api/settings");
  if (error) return <ErrorNote error={error} />;
  if (!data) return <Loading />;
  return <>{keys.map(([k, title, about]) => <SettingCard key={k} k={k} title={title} about={about} value={data.values[k]} humanOnly={data.human_only.includes(k)} />)}</>;
}

function SettingCard({ k, title, about, value, humanOnly }: { k: string; title: string; about: string; value: any; humanOnly: boolean }) {
  const [draft, setDraft] = useState<any>(value);
  const [text, setText] = useState("");
  const toast = useToast();
  const save = useAct((v: any) => api(`/api/settings/${k}`, { method: "PUT", body: v }), ["/api/settings", "/api/dashboard"]);
  const reset = useAct((b: number) => api("/api/settings/paper-wallet/reset", { body: { balance: b } }), ["/api/dashboard"]);
  useEffect(() => { setDraft(value); setText(Array.isArray(value) ? (k === "holidays" ? value.join("\n") : JSON.stringify(value, null, 2)) : ""); }, [value, k]);

  const submit = () => {
    try {
      const v = Array.isArray(value) ? (k === "holidays" ? text.split(/\s+/).filter(Boolean) : JSON.parse(text)) : draft;
      save.mutate(v, { onSuccess: () => toast.show(`${title} saved`), onError: (e) => toast.show(e.message, "loss") });
    } catch { toast.show("That isn't valid JSON.", "loss"); }
  };

  return (
    <Panel title={<span className="flex items-center gap-2">{title}{humanOnly && <Pill tone="warn">only you can change this</Pill>}</span>}>
      {about && <p className="mb-3 text-parch-dim">{about}</p>}
      {Array.isArray(value) ? (
        <textarea className="input min-h-[140px] font-mono text-[13px]" value={text} onChange={(e) => setText(e.target.value)} aria-label={title} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Object.entries(draft ?? {}).map(([f, v]) => (
            <Field key={f} label={human(f)}>
              {typeof v === "boolean" ? (
                <select className="input" value={String(v)} onChange={(e) => setDraft({ ...draft, [f]: e.target.value === "true" })}><option value="true">yes</option><option value="false">no</option></select>
              ) : typeof v === "number" ? (
                <input className="input" type="number" step="any" value={v} onChange={(e) => setDraft({ ...draft, [f]: e.target.value === "" ? 0 : Number(e.target.value) })} />
              ) : v === null || typeof v === "string" ? (
                <input className="input" value={v ?? ""} placeholder="not set" onChange={(e) => setDraft({ ...draft, [f]: e.target.value === "" ? null : e.target.value })} />
              ) : (
                <textarea className="input min-h-[120px] font-mono text-[12.5px]" defaultValue={JSON.stringify(v, null, 2)}
                  onBlur={(e) => { try { setDraft({ ...draft, [f]: JSON.parse(e.target.value) }); } catch { toast.show(`${human(f)} isn't valid JSON`, "loss"); } }} />
              )}
            </Field>
          ))}
        </div>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="btn-primary" disabled={save.isPending} onClick={submit}>Save {title.toLowerCase()}</button>
        {k === "paper" && <button className="btn-ghost" onClick={() => confirm(`Reset the paper wallet to ${money(draft.starting_balance, false)}?`) && reset.mutate(draft.starting_balance, { onSuccess: () => toast.show("Paper wallet reset"), onError: (e) => toast.show(e.message, "loss") })}>Reset paper wallet</button>}
      </div>
      {toast.node}
    </Panel>
  );
}

function AiUsage() {
  const { data } = useApi<any>("/api/settings/ai-usage?days=30");
  const [call, setCall] = useState<number | null>(null);
  const detail = useApi<any>(call ? `/api/settings/ai-calls/${call}` : null);
  if (!data) return <Loading />;
  const rows = (o: any) => Object.entries(o).map(([k, v]: any) => ({ k, ...v }));
  const cols = (label: string) => [
    { key: "k", label },
    { key: "calls", label: "Calls", align: "right" as const },
    { key: "cost_usd", label: "Cost", align: "right" as const, render: (r: any) => `$${r.cost_usd.toFixed(4)}` },
    { key: "tokens", label: "Tokens in / out", align: "right" as const, render: (r: any) => `${r.input_tokens.toLocaleString()} / ${r.output_tokens.toLocaleString()}` },
    { key: "errors", label: "Errors", align: "right" as const },
  ];
  return (
    <>
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="By purpose (30 days)" pad={false}><Table rows={rows(data.by_purpose)} cols={cols("Purpose")} /></Panel>
        <Panel title="By model (30 days)" pad={false}><Table rows={rows(data.by_model)} cols={cols("Model")} /></Panel>
      </div>
      <Panel title="Recent calls" pad={false}>
        <Table rows={data.recent} rowKey={(r: any) => r.id} onRow={(r: any) => setCall(r.id)} cols={[
          { key: "ts", label: "When", render: (r: any) => when(r.ts) },
          { key: "purpose", label: "Purpose" }, { key: "model", label: "Model" },
          { key: "prompt", label: "Prompt", render: (r: any) => `${r.prompt_name} ${r.prompt_version}` },
          { key: "cost_usd", label: "Cost", align: "right", render: (r: any) => `$${r.cost_usd.toFixed(5)}` },
          { key: "latency_ms", label: "Latency", align: "right", render: (r: any) => `${r.latency_ms} ms` },
          { key: "success", label: "", render: (r: any) => (r.success ? "" : <Pill tone="loss">failed</Pill>) },
        ]} />
      </Panel>
      <Modal open={call != null} onClose={() => setCall(null)} title={`AI call #${call}`} wide>
        {!detail.data ? <Loading /> : (
          <div className="space-y-3 text-sm">
            <p className="text-parch-dim">{detail.data.model} · {detail.data.prompt_name} {detail.data.prompt_version} · {detail.data.input_tokens} in / {detail.data.output_tokens} out · ${detail.data.cost_usd}</p>
            {detail.data.error && <p className="text-loss">{detail.data.error}</p>}
            <div><div className="text-mute">Prompt</div><pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-dusk p-3 text-[12.5px]">{detail.data.prompt}</pre></div>
            <div><div className="text-mute">Response</div><pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-dusk p-3 text-[12.5px]">{detail.data.response_raw}</pre></div>
          </div>
        )}
      </Modal>
    </>
  );
}

function Security() {
  const { data } = useApi<any[]>("/api/auth/logins?limit=200");
  const toast = useToast();
  const tg = useAct(() => api("/api/settings/telegram/test", { method: "POST" }));
  return (
    <>
      <Panel title="Telegram" aside={<button className="btn-ghost py-1.5" onClick={() => tg.mutate(undefined, { onSuccess: () => toast.show("Test message sent"), onError: (e) => toast.show(e.message, "loss") })}>Send a test message</button>}>
        <p className="text-parch-dim">Alerts, SELL approvals and /halt come through your bot. Only your chat ID is accepted, and Telegram can never switch to LIVE.</p>
      </Panel>
      <Panel title="Sign-in history" pad={false}>
        <Table rows={data ?? []} cols={[
          { key: "created_at", label: "When", render: (r: any) => when(r.created_at) },
          { key: "success", label: "Result", render: (r: any) => <Pill tone={r.success ? "gain" : "loss"}>{r.success ? "signed in" : "failed"}</Pill> },
          { key: "username_attempted", label: "Username" },
          { key: "ip", label: "IP", render: (r: any) => r.ip ?? "–" },
          { key: "user_agent", label: "Device", className: "whitespace-normal max-w-[360px] text-parch-dim text-[13px]" },
        ]} />
      </Panel>
      {toast.node}
    </>
  );
}
