import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { Empty, ErrorNote, Field, Json, Loading, Modal, Page, Panel, Pill, Table, useToast } from "../components/ui";
import { api } from "../lib/api";
import { when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const suiteInfo: Record<string, { name: string; about: string; metric: (m: any) => number | null; fmt: (v: number | null) => string }> = {
  parser: { name: "Rule parser", about: "Plain English to rule: exact schema, same signals on the same candles, and clarifying instead of guessing.",
    metric: (m) => m?.accuracy ?? null, fmt: (v) => (v == null ? "–" : `${(v * 100).toFixed(1)}% correct`) },
  signal_replay: { name: "News signals (replay)", about: "Re-runs analysis on stored news with only the data available at that time, then checks what price did.",
    metric: (m) => m?.["1d"]?.direction_accuracy?.value ?? null, fmt: (v) => (v == null ? "–" : `${(v * 100).toFixed(1)}% direction (1 day)`) },
  triage: { name: "Triage & stock mapping", about: "Relevant vs noise, and which stocks an article is about. Missed material news counts separately.",
    metric: (m) => m?.relevance_recall ?? null, fmt: (v) => (v == null ? "–" : `${(v * 100).toFixed(0)}% recall`) },
  reasoning_judge: { name: "Reasoning quality", about: "A different model grades explanations for groundedness, consistency and invented facts.",
    metric: (m) => m?.avg_groundedness ?? null, fmt: (v) => (v == null ? "–" : `${v.toFixed(2)} / 5 grounded`) },
  learning: { name: "Learning loop", about: "Do lesson-influenced decisions beat comparable ones, and how precise is each lesson?",
    metric: (m) => m?.with_lessons?.avg_pnl ?? null, fmt: (v) => (v == null ? "–" : `₹${v.toFixed(0)} avg per lesson trade`) },
};

export default function Evals() {
  const { data, error, isLoading } = useApi<any>("/api/evals", { refetchInterval: 15_000 });
  const online = useApi<any>("/api/evals/online");
  const toast = useToast();
  const run = useAct((b: any) => api("/api/evals/run", { body: b }), ["/api/evals"]);
  const [runId, setRunId] = useState<number | null>(null);
  const [compareOpen, setCompareOpen] = useState(false);

  if (isLoading) return <Page title="Evals"><Loading /></Page>;
  const replay = data?.signal_replay?.latest?.metrics?.["1d"];

  return (
    <Page title="Evals" intro="Is each AI part correct, calibrated and actually profitable? Results below a threshold block deploys and block going LIVE."
      actions={<>
        <button className="btn-ghost" onClick={() => setCompareOpen(true)}>Compare models</button>
        <button className="btn-primary" disabled={run.isPending} onClick={() => Object.keys(suiteInfo).forEach((s) => run.mutate({ suite: s }))}>Run all evals</button>
      </>}>
      <ErrorNote error={error || run.error} />
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {Object.entries(suiteInfo).map(([suite, info]) => {
          const s = data?.[suite];
          const latest = s?.latest;
          const trend = (s?.trend ?? []).map((t: any) => ({ at: t.at, v: info.metric(t.metrics) })).filter((t: any) => t.v != null);
          return (
            <section key={suite} className="panel flex flex-col p-4">
              <div className="flex items-start justify-between gap-2">
                <h2 className="h2">{info.name}</h2>
                {latest ? <Pill tone={latest.status === "ERROR" ? "loss" : latest.passed === true ? "gain" : latest.passed === false ? "loss" : "warn"}>
                  {latest.status === "ERROR" ? "error" : latest.status === "RUNNING" ? "running" : latest.passed === true ? "passing" : latest.passed === false ? "failing" : "insufficient data"}</Pill>
                  : <Pill>never run</Pill>}
              </div>
              <p className="mt-1 text-sm text-parch-dim">{info.about}</p>
              <div className="mt-3 font-display text-xl font-semibold">{latest ? info.fmt(info.metric(latest.metrics)) : "–"}</div>
              {latest && <div className="text-[13px] text-mute">{when(latest.started_at)} · {latest.n_cases} cases · ${Number(latest.cost_usd).toFixed(3)}</div>}
              {latest?.error && <p className="mt-1 text-sm text-loss">{latest.error}</p>}
              {trend.length > 1 && (
                <div className="mt-2 h-14"><ResponsiveContainer><LineChart data={trend}><Line dataKey="v" stroke="#E8A33D" dot={false} strokeWidth={2} /><YAxis hide domain={["auto", "auto"]} /></LineChart></ResponsiveContainer></div>
              )}
              <div className="mt-auto flex gap-2 pt-3">
                <button className="btn-ghost py-1.5" onClick={() => run.mutate({ suite }, { onSuccess: () => toast.show(`${info.name} started`) })}>Run</button>
                {latest && <button className="btn-ghost py-1.5" onClick={() => setRunId(latest.id)}>Results</button>}
              </div>
            </section>
          );
        })}
      </div>

      {replay && replay.n > 0 && <ReplayDetail m={replay} />}

      <Panel title="Live accuracy (online evals)" aside={<span className="text-sm text-mute">scored as outcomes arrive: 1 hour, 1 day, 5 days</span>}>
        {!online.data ? <Loading /> : (
          <div className="space-y-4">
            <Table rows={Object.entries(online.data.last_14d).map(([k, v]: any) => ({ k, ...v, prev: online.data.prior_28d[k] }))}
              empty={<p className="text-parch-dim">No scored signals yet. Signals are scored once a day has passed.</p>}
              cols={[
                { key: "k", label: "Source | model", render: (r: any) => r.k.replace("|shadow", " (shadow)") },
                { key: "acc", label: "Accuracy, last 14 days", align: "right", render: (r: any) => r.accuracy.value == null ? "–" : `${(r.accuracy.value * 100).toFixed(1)}% (n=${r.accuracy.n})` },
                { key: "prev", label: "Previous 4 weeks", align: "right", render: (r: any) => r.prev?.accuracy?.value == null ? "–" : `${(r.prev.accuracy.value * 100).toFixed(1)}%` },
                { key: "ece", label: "Calibration error", align: "right", render: (r: any) => r.calibration.ece ?? "–" },
              ]} />
            <div>
              <h3 className="mb-1 font-semibold">Drift alerts</h3>
              {online.data.drift_alerts.length === 0 ? <p className="text-parch-dim">None. Accuracy, calibration, parser clarification rate and cost per decision are within thresholds.</p> : (
                <ul className="space-y-1">{online.data.drift_alerts.map((a: any) => <li key={a.id}><span className="text-mute">{when(a.created_at)}</span> {a.message}</li>)}</ul>
              )}
            </div>
          </div>
        )}
      </Panel>

      <ComparisonTable data={data} />
      <RunResults id={runId} onClose={() => setRunId(null)} />
      <CompareModal open={compareOpen} onClose={() => setCompareOpen(false)} toast={toast.show} />
      {toast.node}
    </Page>
  );
}

function ReplayDetail({ m }: { m: any }) {
  const bins = m.calibration.bins.filter((b: any) => b.n > 0).map((b: any) => ({ x: b.predicted * 100, y: b.observed * 100, n: b.n }));
  const labels = ["BUY", "SELL", "HOLD"];
  return (
    <Panel title="News signals: calibration and confusion (1 day)" aside={m.status !== "ok" && <Pill tone="warn">insufficient data: n={m.n}, not proven</Pill>}>
      <div className="grid gap-6 lg:grid-cols-2">
        <div>
          <div className="mb-1 text-sm text-mute">When the model says X% confident, is it right X% of the time? Points on the diagonal are well calibrated.</div>
          <div className="h-64">
            <ResponsiveContainer>
              <ScatterChart margin={{ left: 0, right: 16, top: 8 }}>
                <CartesianGrid stroke="#2F384C" />
                <XAxis type="number" dataKey="x" domain={[0, 100]} name="Confidence" unit="%" stroke="#8B93A7" fontSize={12} />
                <YAxis type="number" dataKey="y" domain={[0, 100]} name="Hit rate" unit="%" stroke="#8B93A7" fontSize={12} />
                <ZAxis dataKey="n" range={[40, 400]} name="Signals" />
                <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 100, y: 100 }]} stroke="#8B93A7" strokeDasharray="4 4" />
                <Tooltip contentStyle={{ background: "#1B2130", border: "1px solid #2F384C", borderRadius: 8 }} />
                <Scatter data={bins} fill="#E8A33D" />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
          <p className="text-sm text-parch-dim">Expected calibration error {m.calibration.ece ?? "–"}</p>
        </div>
        <div className="space-y-4">
          <table className="text-sm">
            <caption className="mb-1 text-left text-mute">Rows: what happened. Columns: what the model said.</caption>
            <thead><tr><th className="th" />{labels.map((l) => <th key={l} className="th text-right">{l.toLowerCase()}</th>)}</tr></thead>
            <tbody>{labels.map((a) => (
              <tr key={a}><td className="td text-mute">{a.toLowerCase()}</td>{labels.map((p) => (
                <td key={p} className={`td text-right ${a === p ? "font-semibold text-gain" : ""}`}>{m.confusion[a][p]}</td>))}</tr>))}</tbody>
          </table>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <dt className="text-mute">Direction accuracy</dt><dd>{fmtAcc(m.direction_accuracy)}</dd>
            <dt className="text-mute">Random baseline</dt><dd>{fmtAcc(m.baselines.random)}</dd>
            <dt className="text-mute">Always-buy baseline</dt><dd>{fmtAcc(m.baselines.always_buy)}</dd>
            <dt className="text-mute">Buy precision / recall</dt><dd>{m.buy.precision ?? "–"} / {m.buy.recall ?? "–"}</dd>
            <dt className="text-mute">Sell precision / recall</dt><dd>{m.sell.precision ?? "–"} / {m.sell.recall ?? "–"}</dd>
            <dt className="text-mute">Avg return per signal after costs</dt><dd>{m.avg_return_per_signal_after_costs_pct ?? "–"}%</dd>
            <dt className="text-mute">Nifty same windows</dt><dd>{m.baselines.nifty_avg_return_pct ?? "–"}%</dd>
          </dl>
        </div>
      </div>
    </Panel>
  );
}

const fmtAcc = (a: any) => (a?.value == null ? "–" : `${(a.value * 100).toFixed(1)}% (95% CI ${a.ci95 ? `${(a.ci95[0] * 100).toFixed(0)}–${(a.ci95[1] * 100).toFixed(0)}%` : "–"}, n=${a.n})`);

function ComparisonTable({ data }: { data: any }) {
  const rows: any[] = [];
  for (const suite of ["signal_replay", "triage", "parser"]) {
    for (const t of data?.[suite]?.trend ?? []) {
      if (t.config?.model || t.config?.use_llm != null) rows.push({ suite, ...t });
    }
  }
  if (!rows.length) return null;
  return (
    <Panel title="Model and prompt comparison" pad={false}>
      <Table rows={rows.reverse()} cols={[
        { key: "at", label: "Run", render: (r: any) => when(r.at) },
        { key: "suite", label: "Suite", render: (r: any) => suiteInfo[r.suite].name },
        { key: "model", label: "Model / prompt", render: (r: any) => `${r.config.model ?? (r.config.use_llm ? "with AI fallback" : "grammar only")}${r.config.prompt ? ` · ${r.config.prompt}` : ""}` },
        { key: "metric", label: "Accuracy", align: "right", render: (r: any) => suiteInfo[r.suite].fmt(suiteInfo[r.suite].metric(r.metrics)) },
        { key: "cal", label: "Calibration error", align: "right", render: (r: any) => r.metrics?.["1d"]?.calibration?.ece ?? "–" },
        { key: "cost", label: "Cost", align: "right", render: (r: any) => (r.metrics?._cost_usd != null ? `$${r.metrics._cost_usd}` : "–") },
        { key: "lat", label: "Avg latency", align: "right", render: (r: any) => (r.metrics?._avg_latency_ms != null ? `${r.metrics._avg_latency_ms} ms` : "–") },
      ]} />
    </Panel>
  );
}

function RunResults({ id, onClose }: { id: number | null; onClose: () => void }) {
  const [failed, setFailed] = useState(true);
  const { data } = useApi<any>(id ? `/api/evals/runs/${id}?only_failed=${failed}` : null);
  return (
    <Modal open={id != null} onClose={onClose} title={data ? `${suiteInfo[data.run.suite]?.name ?? data.run.suite}: run #${data.run.id}` : "Results"} wide>
      {!data ? <Loading /> : (
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={failed} onChange={(e) => setFailed(e.target.checked)} /> Only failed cases</label>
          {data.results.length === 0 ? <Empty title={failed ? "No failed cases" : "No cases"} /> : (
            <ul className="space-y-2">{data.results.map((r: any) => (
              <li key={r.id} className="rounded-lg bg-dusk p-3 text-sm">
                <div className="flex items-center gap-2"><Pill tone={r.passed ? "gain" : "loss"}>{r.passed ? "pass" : "fail"}</Pill><b>{r.case_id}</b>{r.score != null && <span className="text-mute">score {r.score}</span>}</div>
                {r.input && <div className="mt-1 text-parch-dim">{r.input.text ?? r.input.title ?? JSON.stringify(r.input)}</div>}
                <div className="mt-2 grid gap-2 md:grid-cols-2">
                  <div><div className="text-mute">Expected</div><pre className="whitespace-pre-wrap text-[12.5px]">{JSON.stringify(r.expected, null, 1)}</pre></div>
                  <div><div className="text-mute">Model output</div><pre className="whitespace-pre-wrap text-[12.5px]">{JSON.stringify(r.actual, null, 1)}</pre></div>
                </div>
                {r.detail && <div className="mt-1 text-mute">{r.detail}</div>}
              </li>))}</ul>
          )}
          <Json value={data.run.metrics} label="All metrics" />
        </div>
      )}
    </Modal>
  );
}

function CompareModal({ open, onClose, toast }: { open: boolean; onClose: () => void; toast: (s: string) => void }) {
  const [suite, setSuite] = useState("signal_replay");
  const [models, setModels] = useState("claude-haiku-5-5, claude-sonnet-5-5");
  const go = useAct((b: any) => api("/api/evals/compare", { body: b }), ["/api/evals"]);
  return (
    <Modal open={open} onClose={onClose} title="Compare models on the same dataset">
      <div className="space-y-3">
        <Field label="Dataset"><select className="input" value={suite} onChange={(e) => setSuite(e.target.value)}>
          <option value="signal_replay">News signals (replay)</option><option value="triage">Triage & mapping</option></select></Field>
        <Field label="Models (2–4, comma separated)" hint="Each run is logged with model, prompt version, accuracy, calibration, cost and latency."><input className="input" value={models} onChange={(e) => setModels(e.target.value)} /></Field>
        <ErrorNote error={go.error} />
        <button className="btn-primary" onClick={() => go.mutate({ suite, configs: models.split(",").map((m) => ({ model: m.trim() })).filter((c) => c.model) },
          { onSuccess: () => { toast("Comparison started"); onClose(); } })}>Start comparison</button>
      </div>
    </Modal>
  );
}
