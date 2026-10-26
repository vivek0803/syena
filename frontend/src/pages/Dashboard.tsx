import { ArrowDownRight, ArrowUpRight, Bell, Briefcase, CircleAlert, CircleCheck, CircleX, ScrollText, TrendingDown, TrendingUp } from "lucide-react";
import { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import HoldingNews from "../components/HoldingNews";
import { Empty, ErrorNote, Loading, Page, Panel, Pill, SegmentBar, Table, decisionLabel } from "../components/ui";
import { ago, money, signed, tone, when } from "../lib/format";
import { useApi } from "../lib/hooks";

const plain = (s: string) =>
  s.replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&amp;/g, "&");

// Decision outcomes: icon, colour and the bar segment they fill
const OUTCOME: Record<string, { icon: typeof ArrowUpRight; cls: string; bar: string }> = {
  buy: { icon: ArrowUpRight, cls: "text-gain bg-gain-soft", bar: "bg-gain" },
  sell: { icon: ArrowDownRight, cls: "text-loss bg-loss-soft", bar: "bg-loss" },
  rejected: { icon: CircleX, cls: "text-mute bg-dusk-raised", bar: "bg-mute/60" },
  error: { icon: CircleAlert, cls: "text-saffron bg-saffron-soft", bar: "bg-saffron" },
  exited: { icon: CircleCheck, cls: "text-sky bg-sky-soft", bar: "bg-sky" },
};
const outcomeOf = (x: any) => decisionLabel(x).split(" ")[0] as keyof typeof OUTCOME;

/** Where the price sits between the stop-loss and the target: the risk of each position at a glance. */
function StopTargetTrack({ stop, target, last, entry }: { stop?: number; target?: number; last?: number; entry?: number }) {
  if (stop == null || target == null || last == null || stop === target) return <span className="text-mute">–</span>;
  const at = (v: number) => Math.max(0, Math.min(1, (v - stop) / (target - stop))) * 100;
  const p = at(last), e = entry != null ? at(entry) : null;
  const near = p < 20 ? "text-loss" : p > 80 ? "text-gain" : "text-parch";
  return (
    <div className="w-[190px]" aria-label={`price ${last} between stop ${stop} and target ${target}`}>
      <div className="relative h-1.5 rounded-full bg-gradient-to-r from-loss/50 via-dusk-line to-gain/50">
        {e != null && <span className="absolute top-1/2 h-3 w-px -translate-y-1/2 bg-parch-dim/60" style={{ left: `${e}%` }} title={`entry ${entry}`} />}
        <span className={`absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-dusk-panel ${p < 20 ? "bg-loss" : p > 80 ? "bg-gain" : "bg-parch"}`} style={{ left: `${p}%` }} />
      </div>
      <div className="mt-1.5 flex justify-between text-2xs text-mute"><span>{money(stop)}</span><span className={near}>{Math.round(p)}%</span><span>{money(target)}</span></div>
    </div>
  );
}

function HealthChip({ ok, children, title }: { ok: boolean; children: ReactNode; title?: string }) {
  return (
    <span className={`chip ${ok ? "" : "border-saffron/40 text-saffron"}`} title={title}>
      {ok ? <CircleCheck size={14} className="text-gain" /> : <CircleAlert size={14} />}{children}
    </span>
  );
}

export default function Dashboard() {
  const { data: d, error, isLoading } = useApi<any>("/api/dashboard", { refetchInterval: 20_000 });
  const nav = useNavigate();
  if (isLoading) return <Page title="Dashboard"><Loading /></Page>;
  if (error || !d) return <Page title="Dashboard"><ErrorNote error={error} /></Page>;

  const h = d.health;
  const live = d.mode.mode === "LIVE";
  const main = d.pnl[live ? "LIVE" : "PAPER"], other = d.pnl[live ? "PAPER" : "LIVE"];
  const today = main.today.total, unreal = today - main.today.realised;
  const dt = d.decisions_today as Record<string, number>;
  const counts = { buy: 0, sell: 0, rejected: (dt.REJECTED ?? 0) + (dt.HOLD ?? 0), error: dt.ERROR ?? 0, exited: dt.EXITED ?? 0 };
  counts.buy = Math.max(0, (dt.PLACED ?? 0) - d.recent_decisions.filter((x: any) => x.type === "PLACED" && x.side === "SELL").length);
  counts.sell = (dt.PLACED ?? 0) - counts.buy;
  const totalDecisions = Object.values(dt).reduce((a, b) => a + b, 0);
  const used = d.wallet.starting_balance ? Math.min(100, (d.wallet.invested / d.wallet.starting_balance) * 100) : 0;
  const Trend = today >= 0 ? TrendingUp : TrendingDown;

  return (
    <Page title="Dashboard" intro={live ? "Real orders are being placed for LIVE-enabled strategies." : "Everything runs exactly as live, with orders simulated against your paper wallet."}>
      <section className="panel-hero overflow-hidden" aria-label="Today">
        <div className="grid gap-6 p-6 lg:grid-cols-[1.25fr_1fr_1fr] lg:gap-0 lg:divide-x lg:divide-dusk-line/70">
          <div className="lg:pr-8">
            <div className="text-[13px] text-mute">{live ? "Live" : "Paper"} P&L today</div>
            <div className={`num mt-1 flex items-center gap-3 text-[44px] font-semibold leading-none ${tone(today)}`}>
              {signed(today)}<Trend size={30} strokeWidth={2.2} aria-hidden />
            </div>
            <dl className="mt-4 flex flex-wrap gap-x-7 gap-y-2 text-sm">
              <div><dt className="text-2xs text-mute">Realised</dt><dd className={`num text-[17px] ${tone(main.today.realised)}`}>{signed(main.today.realised)}</dd></div>
              <div><dt className="text-2xs text-mute">Open positions</dt><dd className={`num text-[17px] ${tone(unreal)}`}>{signed(unreal)}</dd></div>
              <div><dt className="text-2xs text-mute">All time</dt><dd className={`num text-[17px] ${tone(main.realised_all_time)}`}>{signed(main.realised_all_time)}</dd></div>
            </dl>
            <p className="mt-3 text-2xs text-mute">{live ? "Paper" : "Live"} today: {signed(other.today.total)}</p>
          </div>

          <div className="lg:px-8">
            <div className="text-[13px] text-mute">Paper wallet</div>
            <div className="num mt-1 text-[30px] font-semibold leading-tight">{money(d.wallet.cash, false)} <span className="text-[15px] font-normal text-mute">cash</span></div>
            <div className="mt-4 h-2 overflow-hidden rounded-full bg-dusk-deep" role="img" aria-label={`${Math.round(used)}% of the wallet invested`}>
              <div className="h-full rounded-full bg-gradient-to-r from-saffron-deep to-saffron" style={{ width: `${used}%` }} />
            </div>
            <p className="mt-2 text-[13px] text-parch-dim">{money(d.wallet.invested, false)} invested in {d.open_positions.length} position{d.open_positions.length === 1 ? "" : "s"}, {Math.round(used)}% of {money(d.wallet.starting_balance, false)}</p>
          </div>

          <div className="lg:pl-8">
            <div className="flex items-baseline justify-between"><span className="text-[13px] text-mute">Decisions today</span><Link to="/decisions" className="text-[13px] text-saffron hover:underline">Open log</Link></div>
            <div className="num mt-1 text-[30px] font-semibold leading-tight">{totalDecisions}</div>
            <div className="mt-4"><SegmentBar label="Decisions today" parts={(Object.keys(counts) as (keyof typeof counts)[]).map((k) => ({ value: counts[k], className: OUTCOME[k].bar, name: k }))} /></div>
            <ul className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1 text-[13px]">
              {(Object.keys(counts) as (keyof typeof counts)[]).map((k) => (
                <li key={k} className="flex items-center gap-1.5 text-parch-dim"><span className={`h-2 w-2 rounded-full ${OUTCOME[k].bar}`} />{counts[k]} {k}</li>))}
            </ul>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-dusk-line/70 bg-dusk-deep/40 px-6 py-3">
          <HealthChip ok={h.worker === "ok"}>Worker {h.worker === "ok" ? "running" : h.worker}</HealthChip>
          <HealthChip ok={!!d.prices?.ready}>Prices: {d.prices?.provider === "angel" ? "Angel One" : "Zerodha"}</HealthChip>
          <HealthChip ok={d.angel.connected} title={d.angel.connected ? `until ${when(d.angel.expires_at)}` : undefined}>Angel {d.angel.connected ? "connected" : "not connected"}</HealthChip>
          <HealthChip ok={d.kite.connected}>Zerodha {d.kite.connected ? "connected" : "not today"}</HealthChip>
          <HealthChip ok={h.ai_configured}>{h.ai_configured ? `AI $${h.ai_spend_today_usd.toFixed(2)} of $${h.ai_budget_usd}` : "AI off"}</HealthChip>
          <HealthChip ok={h.telegram}>Telegram {h.telegram ? "on" : "off"}</HealthChip>
          {h.heartbeats.fast_loop && <span className="ml-auto text-2xs text-mute">prices checked {ago(h.heartbeats.fast_loop.ts)}</span>}
        </div>
      </section>

      <Panel title={`Open positions`} icon={<Briefcase size={17} />} aside={<div className="flex items-center gap-3"><span className="text-[13px] text-mute">{d.open_positions.length} open</span><Link to="/trades" className="text-[13px] text-saffron hover:underline">All trades</Link></div>} pad={false}>
        <Table rows={d.open_positions} rowKey={(r: any) => r.id}
          onRow={(r: any) => r.decision_id && nav(`/decisions/${r.decision_id}`)}
          empty={<div className="px-1 pb-1"><Empty title="No open positions">New entries appear here as soon as a strategy or manual invest places an order.</Empty></div>}
          cols={[
            { key: "stock", label: "Stock", render: (r: any) => (
              <div className="flex items-center gap-2.5">
                <span className={`grid h-8 w-8 place-items-center rounded-lg ${r.side === "SELL" ? "bg-loss-soft text-loss" : "bg-gain-soft text-gain"}`}>{r.side === "SELL" ? <ArrowDownRight size={16} /> : <ArrowUpRight size={16} />}</span>
                <div><div className="font-semibold">{r.stock}</div><div className="text-2xs text-mute">{r.side === "SELL" ? "short" : "long"}, {r.style.toLowerCase().replace("_", "-")}{r.mode === "LIVE" ? ", LIVE" : ""}</div></div>
              </div>) },
            { key: "qty", label: "Qty", align: "right" },
            { key: "prices", label: "Entry → last", align: "right", render: (r: any) => <span><span className="text-parch-dim">{money(r.entry_price)}</span> <span className="text-mute">→</span> {money(r.last_price)}</span> },
            { key: "unrealised", label: "P&L", align: "right", render: (r: any) => <span className={`num text-[15px] font-semibold ${tone(r.unrealised)}`}>{signed(r.unrealised)}</span> },
            { key: "track", label: "Stop to target", render: (r: any) => <StopTargetTrack stop={r.stop_loss} target={r.target} last={r.last_price} entry={r.entry_price} /> },
            { key: "auto_sell", label: "Exit", render: (r: any) => <Pill tone={r.auto_sell ? "info" : "warn"}>{r.auto_sell ? "automatic" : "asks you"}</Pill> },
          ]} />
      </Panel>

      <div className="grid gap-6 lg:grid-cols-[1.45fr_1fr]">
        <Panel title="Latest decisions" icon={<ScrollText size={17} />} aside={<Link to="/decisions" className="text-[13px] text-saffron hover:underline">Decision log</Link>} pad={false}>
          {d.recent_decisions.length === 0 ? <div className="px-5 pb-5 text-parch-dim">No decisions yet. They appear the moment a rule fires, news is analysed or you invest manually.</div> : (
            <ul className="px-2 pb-2">
              {d.recent_decisions.map((x: any) => {
                const o = OUTCOME[outcomeOf(x)] ?? OUTCOME.rejected;
                const Icon = o.icon;
                return (
                  <li key={x.id}>
                    <Link to={`/decisions/${x.id}`} className="flex items-start gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-dusk-raised/50">
                      <span className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg ${o.cls}`}><Icon size={16} /></span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline gap-2"><span className="font-semibold">{x.symbol}</span><span className="text-[13px] text-mute">{decisionLabel(x)}</span><span className="ml-auto whitespace-nowrap text-2xs text-mute">{when(x.ts)}</span></div>
                        <div className="truncate text-[14px] text-parch-dim">{x.summary.replace(/^(PLACED|REJECTED|EXITED|ERROR|QUEUED)\s*(by\s+[a-z_]+:?)?\s*/i, "")}</div>
                        <div className="text-2xs text-mute">from {x.source}{x.mode === "LIVE" ? ", LIVE" : ""}</div>
                      </div>
                    </Link>
                  </li>);
              })}
            </ul>)}
        </Panel>

        <div className="space-y-6">
          <HoldingNews />
          <Panel title="Alerts" icon={<Bell size={17} />}>
            {d.alerts.length === 0 ? <p className="text-parch-dim">No alerts.</p> : (
              <ul className="space-y-3">
                {d.alerts.map((a: any) => (
                  <li key={a.id} className="border-l-2 border-dusk-line pl-3 text-sm">
                    <div className="flex items-center gap-2"><Pill tone={a.kind === "sell" ? "warn" : a.kind === "drift" ? "loss" : "info"}>{a.kind.replace(/_/g, " ")}</Pill>
                      <span className="text-2xs text-mute">{when(a.created_at)}</span></div>
                    <div className="mt-1 text-parch-dim">{plain(a.message)}</div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </Page>
  );
}
