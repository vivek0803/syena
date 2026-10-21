import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { ErrorNote, Field, Page, Panel, Pill } from "../components/ui";
import { api } from "../lib/api";
import { money } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const blank = { symbol: "", style: "SWING", entry_type: "market", entry_low: "", entry_high: "", entry_reason: "", pe: "",
  exit_condition: "", target_pct: "", stop_loss_pct: "", holding_days: "30" };

const STYLES: Record<string, { label: string; days: string; hint: string }> = {
  INTRADAY: { label: "Intraday (MIS)", days: "0", hint: "Bought and sold the same day; squared off automatically before close." },
  SWING: { label: "Swing (CNC)", days: "30", hint: "Held for days to weeks." },
  LONG_TERM: { label: "Long term (CNC)", days: "365", hint: "Held for months or years. Target and stop-loss are optional; 0 days means no time limit." },
};
const pct = (v: string) => (v === "" ? null : Number(v));

export default function Invest() {
  const [f, setF] = useState(blank);
  const [review, setReview] = useState<any>(null);
  const [placed, setPlaced] = useState<any>(null);
  const mode = useApi<any>("/api/mode");
  const sizing = useApi<any>("/api/settings");
  const toPlan = () => ({
    symbol: f.symbol.trim().toUpperCase(), style: f.style, entry_type: f.entry_type,
    entry_low: f.entry_type === "range" ? Number(f.entry_low) : null, entry_high: f.entry_type === "range" ? Number(f.entry_high) : null,
    entry_reason: f.entry_reason, pe: f.pe ? Number(f.pe) : null, exit_condition: f.exit_condition,
    target_pct: pct(f.target_pct), stop_loss_pct: pct(f.stop_loss_pct), holding_days: Number(f.holding_days || 0),
  });
  const doReview = useAct((p: any) => api("/api/invest/review", { body: p }));
  const confirm = useAct((b: any) => api("/api/invest/confirm", { body: b }), ["/api/dashboard", "/api/trades", "/api/decisions"]);
  const set = (k: string) => (e: any) => { setF({ ...f, [k]: e.target.value }); setReview(null); setPlaced(null); };
  const setStyle = (e: any) => { const v = e.target.value; setF({ ...f, style: v, holding_days: STYLES[v].days }); setReview(null); setPlaced(null); };
  const optional = f.style === "LONG_TERM";
  const submit = (e: FormEvent) => { e.preventDefault(); setPlaced(null); doReview.mutate(toPlan(), { onSuccess: setReview }); };
  const sz = sizing.data?.values?.sizing;
  const live = mode.data?.mode === "LIVE";

  return (
    <Page title="Manual invest" intro="Propose a trade. The AI reviews it against news, technicals and past lessons. Nothing is ordered until you confirm.">
      <div className="grid gap-6 lg:grid-cols-2">
        <Panel title="Your plan">
          <form onSubmit={submit} className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <Field label="Stock (NSE symbol)"><input className="input" required value={f.symbol} onChange={set("symbol")} placeholder="INFY" /></Field>
              <Field label="Style"><select className="input" value={f.style} onChange={setStyle}>
                {Object.entries(STYLES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}</select></Field>
            </div>
            <p className="-mt-1 text-[13px] text-mute">{STYLES[f.style].hint}</p>
            <Field label="Entry">
              <select className="input" value={f.entry_type} onChange={set("entry_type")}><option value="market">At market (marketable limit)</option><option value="range">Within a price range</option></select>
            </Field>
            {f.entry_type === "range" && (
              <div className="grid grid-cols-2 gap-3">
                <Field label="Lowest entry price"><input className="input" type="number" step="0.05" required value={f.entry_low} onChange={set("entry_low")} /></Field>
                <Field label="Highest entry price"><input className="input" type="number" step="0.05" required value={f.entry_high} onChange={set("entry_high")} /></Field>
              </div>
            )}
            <Field label="Why you want to enter"><textarea className="input min-h-[80px]" required value={f.entry_reason} onChange={set("entry_reason")} /></Field>
            <div className="grid grid-cols-3 gap-3">
              <Field label="Current P/E" hint="as you see it"><input className="input" type="number" step="0.01" value={f.pe} onChange={set("pe")} /></Field>
              <Field label="Target (% gain)" hint={optional ? "optional" : "e.g. 10"}>
                <input className="input" type="number" step="0.1" min="0.1" max="1000" required={!optional} value={f.target_pct} onChange={set("target_pct")} placeholder={optional ? "none" : "10"} /></Field>
              <Field label="Stop-loss (% drop)" hint={optional ? "optional" : "e.g. 5"}>
                <input className="input" type="number" step="0.1" min="0.1" max="99" required={!optional} value={f.stop_loss_pct} onChange={set("stop_loss_pct")} placeholder={optional ? "none" : "5"} /></Field>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Exit condition"><input className="input" value={f.exit_condition} onChange={set("exit_condition")} placeholder="e.g. results miss estimates" /></Field>
              <Field label="Holding period (days)" hint={optional ? "0 = no limit" : undefined}><input className="input" type="number" min={0} value={f.holding_days} onChange={set("holding_days")} /></Field>
            </div>
            {sz && <p className="text-[13px] text-mute">Position size is set automatically within {money(sz.min_amount, false)} to {money(sz.max_amount, false)}.</p>}
            <ErrorNote error={doReview.error} />
            <button className="btn-primary" disabled={doReview.isPending}>{doReview.isPending ? "Reviewing…" : "Review my plan"}</button>
          </form>
        </Panel>

        <Panel title="Review">
          {!review ? <p className="text-parch-dim">Submit your plan to get a verdict, flagged issues and suggested corrections.</p> : (
            <div className="space-y-4">
              <div className="flex items-center gap-3">
                <Pill tone={review.verdict === "proceed" ? "gain" : review.verdict === "avoid" ? "loss" : "warn"}>{review.verdict}</Pill>
                <span className="text-parch-dim">{review.symbol} last traded at {money(review.ltp)}</span>
              </div>
              {review.levels?.entry && (
                <p className="text-sm text-parch-dim">
                  From ~{money(review.levels.entry)}:{" "}
                  {review.levels.target ? <>target <b className="text-gain">{money(review.levels.target)}</b> (+{f.target_pct}%)</> : "no target"},{" "}
                  {review.levels.stop_loss ? <>stop-loss <b className="text-loss">{money(review.levels.stop_loss)}</b> (−{f.stop_loss_pct}%)</> : "no stop-loss"}.
                  {" "}Final prices are set from your actual entry price.
                </p>
              )}
              {review.summary && <p>{review.summary}</p>}
              {review.issues.length > 0 && (
                <ul className="space-y-1.5">{review.issues.map((i: any, k: number) => (
                  <li key={k} className="flex gap-2"><Pill tone={i.severity === "high" ? "loss" : i.severity === "medium" ? "warn" : "mute"}>{i.severity}</Pill><span><b>{i.field}</b>: {i.problem}</span></li>))}</ul>
              )}
              {Object.keys(review.suggested ?? {}).length > 0 && (
                <div><div className="text-sm text-mute">Suggested corrections</div>
                  <ul className="text-parch-dim">{Object.entries(review.suggested).map(([k, v]) => (
                    <li key={k}>{k}: {String(v)} <button className="text-saffron hover:underline" onClick={() => k in f && setF({ ...f, [k]: String(v) })}>use</button></li>))}</ul></div>
              )}
              {review.technicals?.available && <p className="text-sm text-parch-dim">Technicals: {review.technicals.reasons.join("; ")}</p>}
              {review.news.length > 0 && <div><div className="text-sm text-mute">Recent news considered</div><ul className="text-sm text-parch-dim">{review.news.map((n: any) => <li key={n.id}>{n.title}</li>)}</ul></div>}
              {review.lessons.length > 0 && <p className="text-sm text-parch-dim">Lessons consulted: {review.lessons.map((l: any) => l.code).join(", ")}</p>}
              {placed ? (
                <div className="rounded-lg bg-dusk p-3">
                  <Pill tone={placed.type === "PLACED" ? "gain" : "loss"}>{placed.type.toLowerCase()}</Pill> <span>{placed.summary}</span>
                  <div className="mt-2"><Link className="text-saffron hover:underline" to={`/decisions/${placed.id}`}>See the full decision</Link></div>
                </div>
              ) : (
                <div className="flex flex-wrap items-center gap-2 border-t border-dusk-line pt-4">
                  <button className={live ? "btn-danger" : "btn-primary"} disabled={confirm.isPending}
                    onClick={() => confirm.mutate({ review_id: review.review_id, plan: toPlan() }, { onSuccess: setPlaced })}>
                    {live ? "Yes, place a LIVE order" : "Yes, place a paper order"}
                  </button>
                  <button className="btn-ghost" onClick={() => setReview(null)}>Edit plan</button>
                  <ErrorNote error={confirm.error} />
                </div>
              )}
            </div>
          )}
        </Panel>
      </div>
    </Page>
  );
}
