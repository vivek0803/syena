import StockLink from "../components/StockLink";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ErrorNote, Page, Panel, Pill, Table, Tabs, useToast } from "../components/ui";
import { api } from "../lib/api";
import { money, signed, tone, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

export default function Trades() {
  const [tab, setTab] = useState("OPEN");
  const [mode, setMode] = useState("");
  const nav = useNavigate();
  const toast = useToast();
  const q = `/api/trades?limit=500${tab !== "ALL" ? `&status=${tab}` : ""}${mode ? `&mode=${mode}` : ""}`;
  const { data, error } = useApi<any[]>(q, { refetchInterval: 20_000 });
  const perf = useApi<any[]>("/api/performance");
  const patch = useAct(({ id, ...b }: any) => api(`/api/trades/${id}`, { method: "PATCH", body: b }), ["/api/trades", "/api/dashboard"]);
  const exit = useAct((id: number) => api(`/api/trades/${id}/exit`, { method: "POST" }), ["/api/trades", "/api/dashboard"]);
  const rows = data ?? [];
  const realised = rows.reduce((s, t) => s + (t.profit_loss ?? 0), 0);
  const unreal = rows.reduce((s, t) => s + (t.unrealised ?? 0), 0);

  return (
    <Page title="Trades & positions" intro="The full lifecycle of every trade, with realised and unrealised P&L after charges."
      actions={<a className="btn-ghost" href="/api/export/trades.csv">Download trades CSV</a>}>
      <div className="flex flex-wrap items-center gap-3">
        <Tabs value={tab} onChange={setTab} tabs={[{ id: "OPEN", label: "Open" }, { id: "CLOSED", label: "Closed" }, { id: "FAILED", label: "Failed" }, { id: "ALL", label: "All" }]} />
        <select className="input w-auto" value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Mode"><option value="">Paper and live</option><option value="PAPER">Paper</option><option value="LIVE">Live</option></select>
        <span className="text-parch-dim">Realised <b className={tone(realised)}>{signed(realised)}</b>, unrealised <b className={tone(unreal)}>{signed(unreal)}</b></span>
      </div>
      <ErrorNote error={error || patch.error || exit.error} />
      <Panel pad={false}>
        <Table rows={rows} rowKey={(r: any) => r.id} onRow={(r: any) => r.decision_id && nav(`/decisions/${r.decision_id}`)}
          cols={[
            { key: "created_at", label: "Opened", render: (r: any) => when(r.created_at) },
            { key: "stock", label: "Stock", render: (r: any) => <span className="flex items-center gap-2"><StockLink symbol={r.stock} />{r.side === "SELL" && <span className="rounded bg-loss-soft px-1.5 text-[12px] text-loss">short</span>}</span> },
            { key: "mode", label: "Mode", render: (r: any) => <Pill tone={r.mode === "LIVE" ? "loss" : "gain"}>{r.mode}</Pill> },
            { key: "source", label: "Source", render: (r: any) => `${r.source}${r.algo_id ? ` #${r.algo_id}` : ""}` },
            { key: "style", label: "Style", render: (r: any) => r.style.toLowerCase() },
            { key: "qty", label: "Qty", align: "right" },
            { key: "invested_amount", label: "Invested", align: "right", render: (r: any) => money(r.invested_amount) },
            { key: "entry_price", label: "Entry", align: "right", render: (r: any) => money(r.entry_price) },
            { key: "exit", label: "Exit / last", align: "right", render: (r: any) => money(r.exit_price_executed ?? r.last_price) },
            { key: "stop_loss", label: "Stop", align: "right", render: (r: any) => <>{money(r.stop_loss)}{r.trail?.moved > 0 && <div className="text-[11px] text-gain" title={`trailing stop, raised ${r.trail.moved}×`}>↑ trailing from {money(r.trail.initial_stop)}</div>}</> },
            { key: "target", label: "Target", align: "right", render: (r: any) => money(r.target) },
            { key: "charges", label: "Charges", align: "right", render: (r: any) => money(r.charges) },
            { key: "pnl", label: "P&L", align: "right", render: (r: any) => { const v = r.profit_loss ?? r.unrealised; return <span className={tone(v)}>{signed(v)}{r.status === "OPEN" ? " (open)" : ""}</span>; } },
            { key: "exit_reason", label: "Exit reason", render: (r: any) => r.exit_reason?.replace(/_/g, " ") ?? (r.status === "OPEN" ? "–" : r.status.toLowerCase()) },
            { key: "actions", label: "", render: (r: any) => r.status !== "OPEN" ? null : (
              <span className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                <label className="flex items-center gap-1 text-sm"><input type="checkbox" checked={r.auto_sell} onChange={(e) => patch.mutate({ id: r.id, auto_sell: e.target.checked })} /> auto-sell</label>
                <button className="btn-ghost px-2 py-1" onClick={() => confirm(`Exit ${r.stock} ${r.mode === "LIVE" ? "with a LIVE order" : "(paper)"} at market now?`) &&
                  exit.mutate(r.id, { onSuccess: (d: any) => toast.show(d.summary) })}>Exit now</button>
              </span>) },
          ]} />
      </Panel>
      <Panel title="Paper vs live, per strategy" pad={false}>
        <Table rows={perf.data ?? []} cols={[
          { key: "algo_id", label: "Strategy", render: (r: any) => (r.algo_id ? `#${r.algo_id}` : "manual") },
          { key: "mode", label: "Mode" },
          { key: "trades", label: "Closed trades", align: "right" },
          { key: "win_rate", label: "Win rate", align: "right", render: (r: any) => (r.win_rate == null ? "–" : `${(r.win_rate * 100).toFixed(0)}%`) },
          { key: "pnl", label: "P&L", align: "right", render: (r: any) => <span className={tone(r.pnl)}>{signed(r.pnl)}</span> },
        ]} />
      </Panel>
      {toast.node}
    </Page>
  );
}
