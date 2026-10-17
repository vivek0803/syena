import { useState } from "react";
import { ErrorNote, Page, Panel, Pill, Table } from "../components/ui";
import { when } from "../lib/format";
import { useApi } from "../lib/hooks";

const levelTone: Record<string, string> = { ERROR: "loss", CRITICAL: "loss", WARNING: "warn", INFO: "info", DEBUG: "mute" };

export default function Logs() {
  const [f, setF] = useState({ level: "", module: "", stock: "", algo_id: "", q: "" });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]).toString();
  const { data, error } = useApi<any[]>(`/api/logs?limit=300${qs ? "&" + qs : ""}`, { refetchInterval: 15_000 });
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return (
    <Page title="Logs" intro="Structured logs from the API and the background worker. Secrets are redacted before they are written.">
      <div className="flex flex-wrap gap-2">
        <select className="input w-auto" value={f.level} onChange={set("level")} aria-label="Level">
          {["", "ERROR", "WARNING", "INFO"].map((l) => <option key={l} value={l}>{l ? l.toLowerCase() : "all levels"}</option>)}</select>
        <select className="input w-auto" value={f.module} onChange={set("module")} aria-label="Module">
          {["", "syena.engine", "syena.news", "syena.algos", "syena.monitor", "syena.ai", "syena.mode", "syena.auth", "syena.kite", "syena.worker", "syena.portfolio", "syena.lessons", "syena.evals"].map((m) =>
            <option key={m} value={m}>{m ? m.replace("syena.", "") : "all modules"}</option>)}</select>
        <input className="input w-28" placeholder="Stock" value={f.stock} onChange={(e) => setF({ ...f, stock: e.target.value.toUpperCase() })} />
        <input className="input w-24" placeholder="Algo #" value={f.algo_id} onChange={set("algo_id")} inputMode="numeric" />
        <input className="input w-60" placeholder="Search messages" value={f.q} onChange={set("q")} />
      </div>
      <ErrorNote error={error} />
      <Panel pad={false}>
        <Table rows={data ?? []} rowKey={(r: any) => r.id} cols={[
          { key: "ts", label: "Time", render: (r: any) => when(r.ts) },
          { key: "level", label: "Level", render: (r: any) => <Pill tone={levelTone[r.level]}>{r.level.toLowerCase()}</Pill> },
          { key: "module", label: "Module", render: (r: any) => r.module.replace("syena.", "") },
          { key: "message", label: "Message", className: "whitespace-normal min-w-[240px]" },
          { key: "stock", label: "Stock", render: (r: any) => r.stock ?? "" },
          { key: "extra", label: "Details", className: "whitespace-normal min-w-[260px] text-[12.5px] text-parch-dim", render: (r: any) => (r.extra ? JSON.stringify(r.extra) : "") },
        ]} />
      </Panel>
    </Page>
  );
}
