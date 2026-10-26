import { useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Empty, ErrorNote, Field, Loading, Modal, Page, Panel, Pill, Table, Tabs, useToast } from "../components/ui";
import { api } from "../lib/api";
import { day, signed, tone, when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const statuses = ["TESTING", "PROPOSED", "ACTIVE", "RETIRED"];
const statusAbout: Record<string, string> = {
  PROPOSED: "No effect. Gathering evidence from independent trades.",
  TESTING: "Shadow mode: logged as “would have applied”, no effect. Needs out-of-sample validation and your approval.",
  ACTIVE: "Changes decisions (confidence, extra checks, tighter stops). Re-validated every 30 days.",
  RETIRED: "Kept for the record. Never deleted.",
};
const classColors: Record<string, string> = {
  "good-entry/good-exit": "#3FC48A", "good-entry/bad-exit": "#7FA8D8", "bad-entry": "#F2685A", "bad-luck": "#B9B3A6",
  "rule-failure": "#C58BD6", "news-misread": "#F2A93B", "stop-too-tight": "#D98A4E", "target-too-greedy": "#8BC6C0",
  "correctly-avoided": "#3E8E6A", "missed-opportunity": "#A9874A",
};

export default function Learning() {
  const [tab, setTab] = useState("TESTING");
  const lessons = useApi<any[]>("/api/learning/lessons");
  const reviews = useApi<any[]>("/api/learning/reviews?limit=100");
  const charts = useApi<any>("/api/learning/charts");
  const toast = useToast();
  const inv = ["/api/learning"];
  const validate = useAct((id: number) => api(`/api/learning/lessons/${id}/validate`, { method: "POST" }), inv);
  const approve = useAct((id: number) => api(`/api/learning/lessons/${id}/approve`, { method: "POST" }), inv);
  const mine = useAct(() => api("/api/learning/mine", { method: "POST" }), inv);
  const [retire, setRetire] = useState<{ id: number; kind: "reject" | "retire" } | null>(null);
  const [review, setReview] = useState<any>(null);
  const list = (lessons.data ?? []).filter((l) => l.status === tab);
  const counts = Object.fromEntries(statuses.map((s) => [s, (lessons.data ?? []).filter((l) => l.status === s).length]));
  const err = validate.error || approve.error || mine.error;

  return (
    <Page title="Learning" intro="Every closed trade is reviewed, separating good decisions from good luck. Patterns become lessons only with evidence, out-of-sample proof and your approval."
      actions={<button className="btn-ghost" disabled={mine.isPending} onClick={() => mine.mutate(undefined, { onSuccess: (r: any) => toast.show(`${r.patterns.length} patterns, ${r.proposed.length} lessons proposed`) })}>Find patterns now</button>}>
      <ErrorNote error={err} />
      <Panel title="Lessons" aside={<Tabs value={tab} onChange={setTab} tabs={statuses.map((s) => ({ id: s, label: `${s.toLowerCase()} (${counts[s] ?? 0})` }))} />}>
        <p className="mb-4 text-parch-dim">{statusAbout[tab]}</p>
        {lessons.isLoading ? <Loading /> : list.length === 0 ? <Empty title={`No ${tab.toLowerCase()} lessons`} /> : (
          <ul className="space-y-3">
            {list.map((l) => (
              <li key={l.id} className="rounded-lg border border-dusk-line p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <b className="font-display text-lg">{l.code}</b>
                  <Pill tone="info">{l.action.type.replace(/_/g, " ")}{l.action.value != null ? ` ${l.action.value}` : ""}</Pill>
                  <span className="text-sm text-mute">from {l.source} · created {day(l.created_at)}{l.expires_at ? ` · re-validate by ${day(l.expires_at)}` : ""}</span>
                </div>
                <p className="mt-1.5">{l.text}</p>
                <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-sm">
                  <span><span className="text-gain">{l.support_count}</span> supporting</span>
                  <span><span className="text-loss">{l.contradiction_count}</span> contradicting</span>
                  <span>{l.distinct_days} distinct days</span>
                  <span>{l.applications} decisions touched</span>
                  <span className="text-parch-dim">when: {Object.entries(l.trigger).map(([k, v]) => `${k} ${JSON.stringify(v)}`).join(", ")}</span>
                </div>
                {l.validation && (
                  <p className={`mt-2 text-sm ${l.validation.passed ? "text-gain" : "text-parch-dim"}`}>
                    Out-of-sample check {l.validation.passed ? "passed" : l.validation.note ?? "did not pass"}: {l.validation.n} unseen trades, avg {signed(l.validation.avg_pnl_delta)} per trade, total {signed(l.validation.total_pnl_delta)} (holdout days excluded).
                  </p>
                )}
                {l.retired_reason && <p className="mt-2 text-sm text-mute">Retired: {l.retired_reason}</p>}
                {l.evidence?.length > 0 && (
                  <details className="mt-2 text-sm"><summary className="cursor-pointer text-saffron">Evidence ({l.evidence.length})</summary>
                    <ul className="mt-1 space-y-0.5">{l.evidence.map((e: any) => (
                      <li key={e.id}><span className={e.supports ? "text-gain" : "text-loss"}>{e.supports ? "supports" : "contradicts"}</span> · {e.day} · trade #{e.trade_id ?? "–"}{e.note ? ` · ${e.note}` : ""}</li>))}</ul>
                  </details>
                )}
                {l.status !== "RETIRED" && (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {l.status === "TESTING" && <>
                      <button className="btn-ghost py-1.5" onClick={() => validate.mutate(l.id)}>Run out-of-sample check</button>
                      <button className="btn-primary py-1.5" disabled={!l.validation?.passed} title={l.validation?.passed ? "" : "needs a passed out-of-sample check"}
                        onClick={() => approve.mutate(l.id, { onSuccess: () => toast.show(`${l.code} is now active`) })}>Approve</button>
                    </>}
                    <button className="btn-ghost py-1.5" onClick={() => setRetire({ id: l.id, kind: l.status === "ACTIVE" ? "retire" : "reject" })}>{l.status === "ACTIVE" ? "Retire" : "Reject"}</button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {charts.data && (
        <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
          <Panel title="Review outcomes by week">
            {charts.data.mistakes_by_week.length === 0 ? <p className="text-parch-dim">Appears after the first closed trades are reviewed.</p> : (
              <div className="h-64"><ResponsiveContainer>
                <BarChart data={charts.data.mistakes_by_week}>
                  <CartesianGrid stroke="#283350" vertical={false} />
                  <XAxis dataKey="week" stroke="#8B93A7" fontSize={12} tickFormatter={(v) => day(v).slice(0, 6)} />
                  <YAxis stroke="#8B93A7" fontSize={12} allowDecimals={false} />
                  <Tooltip cursor={{ fill: "#1D2740" }} contentStyle={{ background: "#151D2E", border: "1px solid #283350", borderRadius: 8 }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  {Object.keys(classColors).filter((c) => charts.data.mistakes_by_week.some((w: any) => w[c])).map((c) => <Bar key={c} dataKey={c} stackId="a" fill={classColors[c]} />)}
                </BarChart>
              </ResponsiveContainer></div>
            )}
          </Panel>
          <Panel title="With vs without lessons">
            <dl className="grid grid-cols-3 gap-y-2 text-sm">
              <dt /><dd className="text-mute">With</dd><dd className="text-mute">Without</dd>
              <dt className="text-mute">Trades</dt><dd>{charts.data.with_lessons.n}</dd><dd>{charts.data.without_lessons.n}</dd>
              <dt className="text-mute">Win rate</dt><dd>{fmtRate(charts.data.with_lessons.win_rate)}</dd><dd>{fmtRate(charts.data.without_lessons.win_rate)}</dd>
              <dt className="text-mute">Total P&L</dt><dd className={tone(charts.data.with_lessons.pnl)}>{signed(charts.data.with_lessons.pnl)}</dd><dd className={tone(charts.data.without_lessons.pnl)}>{signed(charts.data.without_lessons.pnl)}</dd>
            </dl>
            {charts.data.lesson_impact.length > 0 && (
              <ul className="mt-4 space-y-1 text-sm">{charts.data.lesson_impact.map((i: any) => (
                <li key={i.code} className="flex justify-between"><span>{i.code} <span className="text-mute">({i.status.toLowerCase()})</span></span><span className={tone(i.total_pnl_delta)}>{signed(i.total_pnl_delta)} over {i.n} trades</span></li>))}</ul>
            )}
          </Panel>
        </div>
      )}

      <Panel title="Trade reviews" pad={false}>
        <Table rows={reviews.data ?? []} rowKey={(r: any) => r.id} onRow={setReview}
          empty={<Empty title="No reviews yet">Every closed trade (paper or live) is reviewed within ten minutes.</Empty>}
          cols={[
            { key: "created_at", label: "Reviewed", render: (r: any) => when(r.created_at) },
            { key: "stock", label: "Stock", render: (r: any) => <b>{r.stock}</b> },
            { key: "kind", label: "Kind", render: (r: any) => r.kind.replace("_", " ") },
            { key: "classification", label: "Classification", render: (r: any) => <span style={{ color: classColors[r.classification] }}>{r.classification}</span> },
            { key: "decision_quality", label: "Decision", render: (r: any) => <Pill tone={r.decision_quality === "sound" ? "gain" : "loss"}>{r.decision_quality}</Pill> },
            { key: "pnl", label: "P&L", align: "right", render: (r: any) => <span className={tone(r.pnl)}>{signed(r.pnl)}</span> },
            { key: "summary", label: "Summary", className: "whitespace-normal min-w-[280px] text-parch-dim" },
          ]} />
      </Panel>

      <ReviewModal review={review} onClose={() => setReview(null)} />
      <RetireModal target={retire} onClose={() => setRetire(null)} />
      {toast.node}
    </Page>
  );
}

const fmtRate = (v: number | null) => (v == null ? "–" : `${(v * 100).toFixed(0)}%`);

function ReviewModal({ review, onClose }: { review: any; onClose: () => void }) {
  const label = useAct((b: any) => api("/api/labels", { body: b }));
  const [mine, setMine] = useState("");
  if (!review) return null;
  const cf = review.counterfactual ?? {};
  return (
    <Modal open onClose={onClose} title={`Review: ${review.stock ?? ""}`} wide>
      <div className="space-y-4">
        <p><Pill tone={review.decision_quality === "sound" ? "gain" : "loss"}>{review.decision_quality} decision</Pill> <b>{review.classification}</b>, outcome {review.outcome?.replace("_", " ")}</p>
        {review.summary && <p className="text-parch-dim">{review.summary}</p>}
        <div className="grid gap-4 md:grid-cols-2">
          <div><h3 className="font-semibold text-gain">What was right</h3><ul className="list-disc pl-5 text-sm">{(review.right ?? []).map((x: any, i: number) => <li key={i}>{x.point} <span className="text-mute">({x.evidence})</span></li>)}</ul></div>
          <div><h3 className="font-semibold text-loss">What was wrong</h3><ul className="list-disc pl-5 text-sm">{(review.wrong ?? []).map((x: any, i: number) => <li key={i}>{x.point} <span className="text-mute">({x.evidence})</span></li>)}</ul></div>
        </div>
        <div>
          <h3 className="font-semibold">Counterfactuals</h3>
          <table className="text-sm"><tbody>{Object.entries(cf).map(([k, v]: any) => (
            <tr key={k}><td className="pr-4 text-parch-dim">{k.replace(/_/g, " ")}</td><td>{typeof v === "object" && v ? `${signed(v.pnl)}${v.by ? ` (${v.by})` : ""}` : String(v)}</td></tr>))}</tbody></table>
        </div>
        {review.hindsight_flags?.length > 0 && <p className="text-sm text-saffron">Hindsight flagged: {review.hindsight_flags.join("; ")}</p>}
        {review.decision_id && <Link className="text-saffron hover:underline" to={`/decisions/${review.decision_id}`}>Open the original decision</Link>}
        <div className="border-t border-dusk-line pt-3">
          <Field label="Your classification (used to check the reviewer)">
            <div className="flex gap-2">
              <select className="input" value={mine} onChange={(e) => setMine(e.target.value)}>
                <option value="">Choose…</option>{Object.keys(classColors).map((c) => <option key={c} value={c}>{c}</option>)}</select>
              <button className="btn-ghost" disabled={!mine || label.isSuccess} onClick={() => label.mutate({ kind: "review", ref_id: review.id, label: { classification: mine } })}>{label.isSuccess ? "Saved" : "Save"}</button>
            </div>
          </Field>
        </div>
      </div>
    </Modal>
  );
}

function RetireModal({ target, onClose }: { target: { id: number; kind: string } | null; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const go = useAct((b: any) => api(`/api/learning/lessons/${b.id}/${b.kind}`, { body: { reason: b.reason || "by you" } }), ["/api/learning"]);
  if (!target) return null;
  return (
    <Modal open onClose={onClose} title={target.kind === "retire" ? "Retire lesson" : "Reject lesson"}>
      <Field label="Reason"><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <p className="mt-2 text-sm text-parch-dim">The lesson is kept with its evidence and stops affecting decisions.</p>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn-ghost" onClick={onClose}>Cancel</button>
        <button className="btn-primary" onClick={() => go.mutate({ ...target, reason }, { onSuccess: onClose })}>{target.kind === "retire" ? "Retire" : "Reject"}</button>
      </div>
    </Modal>
  );
}
