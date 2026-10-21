import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import AiVerdict from "../components/AiVerdict";
import StockLink from "../components/StockLink";
import { Empty, ErrorNote, Json, Loading, Page, Panel, Pill, Trail, decisionLabel, decisionTone } from "../components/ui";
import { money, signed, tone, when } from "../lib/format";
import { useApi } from "../lib/hooks";

const types = ["", "BUY", "SELL", "REJECTED", "ERROR", "EXITED"];

export default function Decisions() {
  const { id } = useParams();
  const nav = useNavigate();
  const [f, setF] = useState({ type: "", symbol: "", source: "" });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]).toString();
  const list = useApi<any[]>(`/api/decisions?limit=200${qs ? "&" + qs : ""}`, { refetchInterval: 30_000 });

  return (
    <Page title="Decision log" intro="Every decision the system made, including the ones where it chose not to trade. Open one to see exactly why.">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)]">
        <div className={`${id ? "hidden lg:block" : ""} space-y-3`}>
          <div className="flex flex-wrap gap-2">
            <select className="input w-auto" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} aria-label="Decision type">
              {types.map((t) => <option key={t} value={t}>{t ? t.toLowerCase() : "all outcomes"}</option>)}
            </select>
            <select className="input w-auto" value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })} aria-label="Source">
              {["", "algo", "news", "manual"].map((t) => <option key={t} value={t}>{t || "all sources"}</option>)}
            </select>
            <input className="input w-32" placeholder="Symbol" value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value.toUpperCase() })} />
          </div>
          <div className="panel overflow-hidden">
            {list.isLoading && <div className="p-4"><Loading /></div>}
            <ErrorNote error={list.error} />
            {list.data?.length === 0 && <div className="p-4"><Empty title="No decisions match">Try clearing the filters.</Empty></div>}
            <ol>
              {list.data?.map((x) => (
                <li key={x.id}>
                  <Link to={`/decisions/${x.id}`} className={`block border-t border-dusk-line/70 px-4 py-3 first:border-t-0 hover:bg-dusk-raised/50 ${String(x.id) === id ? "bg-dusk-raised" : ""}`}>
                    <div className="flex items-center gap-2">
                      <Pill tone={decisionTone(x.type, x.side)}>{decisionLabel(x)}</Pill>
                      <span className="font-semibold">{x.symbol}</span>
                      <span className="ml-auto text-[13px] text-mute">{when(x.ts)}</span>
                    </div>
                    <div className="mt-1 line-clamp-2 text-sm text-parch-dim">{x.summary}</div>
                    <div className="mt-1 text-[13px] text-mute">{x.source} · {x.mode}{x.confidence != null ? ` · confidence ${x.confidence}` : ""}{x.lessons_applied?.length ? ` · ${x.lessons_applied.length} lesson(s) applied` : ""}</div>
                  </Link>
                </li>
              ))}
            </ol>
          </div>
        </div>
        <div className="lg:sticky lg:top-16 lg:max-h-[calc(100vh-5rem)] lg:self-start lg:overflow-y-auto lg:pr-1">
          {id ? <DecisionDetail id={id} onBack={() => nav("/decisions")} /> : (
            <div className="hidden lg:block"><Empty title="Pick a decision">You'll see the inputs, the reasoning, every check it passed or failed, the lessons consulted and the resulting order.</Empty></div>
          )}
        </div>
      </div>
    </Page>
  );
}

function DecisionDetail({ id, onBack }: { id: string; onBack: () => void }) {
  const { data, error, isLoading } = useApi<any>(`/api/decisions/${id}`);
  if (isLoading) return <Loading />;
  if (error || !data) return <ErrorNote error={error} />;
  const d = data.decision;
  const r = d.reason_json ?? {};
  const inputs = d.inputs_json ?? {};
  const failing = (d.checks_json ?? []).find((c: any) => !c.passed);

  return (
    <div className="space-y-4">
      <button className="btn-ghost lg:hidden" onClick={onBack}>Back to list</button>
      <Panel title={<span className="flex flex-wrap items-center gap-2"><Pill tone={decisionTone(d.type, d.side)}>{decisionLabel(d)}</Pill>{d.side === "SELL" && d.type === "PLACED" && <span className="text-[13px] text-mute">short, intraday</span>}<StockLink symbol={d.symbol} /></span>}
        aside={<span className="text-sm text-mute">{when(d.ts)} · {d.source} · {d.mode}</span>}>
        <p className="text-[17px] leading-relaxed">{d.summary}</p>
        {r.score_breakdown ? (
          <div className="mt-3 rounded-lg bg-dusk p-3">
            <div className="text-sm text-parch-dim">Weighted score used by the gate: <b className="text-lg text-parch">{r.weighted_score}</b></div>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">{(["news", "technicals", "market"] as const).map((k) => {
              const v = r.score_breakdown.parts[k], w = r.score_breakdown.weights[k];
              return (
                <div key={k} className="text-sm">
                  <div className="flex justify-between"><span className="capitalize text-parch-dim">{k} <span className="text-mute">×{w}%</span></span><b className="tabular-nums">{v}</b></div>
                  <div className="mt-1 h-1.5 rounded-full bg-dusk-line"><div className="h-full rounded-full bg-saffron" style={{ width: `${v}%` }} /></div>
                  <div className="mt-0.5 text-[12px] text-mute">adds {(v * w / 100).toFixed(1)}</div>
                </div>);
            })}</div>
            {r.model_confidence != null && r.score_breakdown.parts.news !== r.model_confidence && <p className="mt-2 text-[12px] text-mute">News: the AI said {r.model_confidence}, adjusted by lessons to {r.score_breakdown.parts.news}.</p>}
          </div>
        ) : d.confidence != null && (
          <p className="mt-2 text-parch-dim">Confidence used by the gate: <b className="text-parch">{d.confidence}</b>
            {r.model_confidence != null && r.model_confidence !== d.confidence && <> (model said {r.model_confidence}, adjusted by lessons)</>}</p>
        )}
      </Panel>

      <Panel title="Why: the checks, in order">
        <Trail checks={d.checks_json ?? []} outcomeTone={failing ? "loss" : "gain"}
          outcome={failing ? `Rejected at ${failing.name.replace(/_/g, " ")}: ${failing.detail}` : d.type === "PLACED" ? (d.side === "SELL" ? "Short sold (intraday)" : "Order placed") : d.type === "EXITED" ? "Position closed" : d.type === "ERROR" ? "Error: nothing was decided" : decisionLabel(d)} />
      </Panel>

      {r.action && (r.summary || r.reasoning) && (
        <Panel title="The AI's analysis">
          <AiVerdict a={{ action: r.action, confidence: r.model_confidence, summary: r.summary, reasoning: r.reasoning, risks: r.risks,
                          model: r.model, prompt_version: r.prompt_version, technicals: inputs.technicals ? { score: inputs.technicals.score, reasons: inputs.technicals.reasons } : null }} />
        </Panel>
      )}

      {!r.action && (r.summary || r.reasoning || r.rule || r.entry_reason || r.exit_reason) && (
        <Panel title="Reasoning">
          <div className="space-y-3">
            {r.rule && <pre className="whitespace-pre-wrap rounded-lg bg-dusk p-3 text-sm">{r.rule}</pre>}
            {r.summary && <p>{r.summary}</p>}
            {r.reasoning && <p className="text-parch-dim">{r.reasoning}</p>}
            {r.entry_reason && <p><span className="text-mute">Your entry reason: </span>{r.entry_reason}</p>}
            {r.ai_review && <p><span className="text-mute">AI review: </span>{r.ai_review}</p>}
            {r.exit_reason && <p><span className="text-mute">Exit reason: </span>{r.exit_reason} ({r.actor})</p>}
            {r.risks?.length > 0 && <div><div className="text-sm text-mute">Risks the model flagged</div><ul className="list-disc pl-5 text-parch-dim">{r.risks.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>}
            {r.model && <p className="text-[13px] text-mute">Model {r.model}, prompt {r.prompt_version}</p>}
            {r.indicators && (
              <table className="text-sm"><tbody>{r.indicators.map((v: any) => <tr key={v.operand}><td className="pr-4 text-parch-dim">{v.operand}</td><td className="text-right">{v.value ?? "–"}</td></tr>)}</tbody></table>
            )}
          </div>
        </Panel>
      )}

      {(data.news.length > 0 || inputs.technicals || inputs.market_context) && (
        <Panel title="Inputs it saw">
          <div className="space-y-4">
            {data.news.length > 0 && (
              <div>
                <div className="mb-1 text-sm text-mute">News{r.cited?.length ? ` (cited: ${r.cited.map((c: number) => `#${c}`).join(", ")})` : ""}</div>
                <ul className="space-y-1">{data.news.map((n: any) => <li key={n.id}><span className="text-mute">#{n.id}</span> {n.title}</li>)}</ul>
              </div>
            )}
            {inputs.technicals?.available && (
              <div>
                <div className="mb-1 text-sm text-mute">Technicals (score {inputs.technicals.score})</div>
                <p className="text-parch-dim">{inputs.technicals.reasons?.join("; ")}</p>
              </div>
            )}
            {inputs.market_context && (
              <div>
                <div className="mb-1 text-sm text-mute">Market context score {inputs.market_context.score}</div>
                <p className="text-parch-dim">Nifty {inputs.market_context.raw?.nifty_pct?.toFixed?.(2) ?? "–"}%, Sensex {inputs.market_context.raw?.sensex_pct?.toFixed?.(2) ?? "–"}%, India VIX {inputs.market_context.raw?.vix_pct?.toFixed?.(2) ?? "–"}%</p>
              </div>
            )}
          </div>
        </Panel>
      )}

      <Panel title="Lessons">
        {!d.lessons_consulted?.length && !d.lessons_would_apply?.length ? <p className="text-parch-dim">No lessons matched this setup.</p> : (
          <div className="space-y-3">
            {d.lessons_applied?.map((a: any) => (
              <div key={a.lesson} className="rounded-lg border border-saffron/40 bg-saffron-soft/40 p-3">
                <div className="font-semibold">Applied {a.lesson}: {a.detail}</div>
                <div className="text-sm text-parch-dim">{d.lessons_consulted.find((c: any) => c.code === a.lesson)?.text}</div>
              </div>
            ))}
            {d.lessons_would_apply?.map((a: any) => (
              <div key={a.lesson} className="rounded-lg border border-dusk-line p-3">
                <div className="font-semibold">Testing {a.lesson} would have: {a.would_have}</div>
                <div className="text-sm text-parch-dim">Shadow mode: logged only, no effect on this decision.</div>
              </div>
            ))}
            <div className="text-sm text-mute">Consulted: {d.lessons_consulted?.map((c: any) => `${c.code} (${c.status.toLowerCase()}, ${c.support} for / ${c.contradiction} against)`).join("; ") || "none"}</div>
          </div>
        )}
      </Panel>

      {d.similar_trades?.length > 0 && (
        <Panel title="Similar past trades shown to the analysis">
          <ul className="space-y-1 text-sm">{d.similar_trades.map((s: any) => (
            <li key={s.trade_id}>#{s.trade_id} {s.stock}: <span className={tone(s.pnl)}>{signed(s.pnl)}</span> ({s.exit_reason}){s.classification ? `, reviewed as ${s.classification}` : ""}</li>
          ))}</ul>
        </Panel>
      )}

      {(data.trade || data.orders.length > 0) && (
        <Panel title="Resulting order">
          {data.trade && (
            <p className="mb-3">{data.trade.qty} × {data.trade.stock} @ {money(data.trade.entry_price)} ({money(data.trade.invested_amount)}), stop {money(data.trade.stop_loss)}, target {money(data.trade.target)}, status {data.trade.status.toLowerCase()}
              {data.trade.profit_loss != null && <>, P&L <span className={tone(data.trade.profit_loss)}>{signed(data.trade.profit_loss)}</span></>}</p>
          )}
          <ul className="space-y-2 text-sm">
            {data.orders.map((o: any) => (
              <li key={o.id} className="rounded-lg bg-dusk p-3">
                <div><b>{o.purpose.toLowerCase()}</b> {o.side} {o.qty} {o.order_type.toLowerCase()} {o.price ? `@ ${o.price}` : ""} via {o.broker}: {o.status.toLowerCase()}{o.fill_price ? ` @ ${o.fill_price}` : ""}, charges {money(o.charges)}</div>
                {o.message && <div className="text-parch-dim">{o.message}</div>}
              </li>
            ))}
          </ul>
        </Panel>
      )}

      {data.review && (
        <Panel title="Post-trade review">
          <p><Pill tone={data.review.decision_quality === "sound" ? "gain" : "loss"}>{data.review.classification}</Pill> <span className="text-parch-dim">{data.review.summary}</span></p>
        </Panel>
      )}

      {data.ai_calls.map((c: any) => <Json key={c.id} value={{ model: c.model, prompt_version: c.prompt_version, cost_usd: c.cost_usd, latency_ms: c.latency_ms, prompt: c.prompt, response: c.response_raw }} label={`AI call #${c.id}: exact prompt and response`} />)}
      <Json value={data} label="Full decision record" />
    </div>
  );
}
