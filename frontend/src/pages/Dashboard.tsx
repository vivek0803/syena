import { Link, useNavigate } from "react-router-dom";
import { Empty, ErrorNote, Loading, Page, Panel, Pill, Stat, Table, decisionLabel, decisionTone } from "../components/ui";
import { ago, money, signed, tone, when } from "../lib/format";
import { useApi } from "../lib/hooks";

const plain = (s: string) =>
  s.replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&amp;/g, "&");

export default function Dashboard() {
  const { data: d, error, isLoading } = useApi<any>("/api/dashboard", { refetchInterval: 20_000 });
  const nav = useNavigate();
  if (isLoading) return <Page title="Dashboard"><Loading /></Page>;
  if (error || !d) return <Page title="Dashboard"><ErrorNote error={error} /></Page>;

  const h = d.health;
  const healthRows: [string, string, boolean][] = [
    ["Background worker", h.worker === "ok" ? "running" : h.worker, h.worker === "ok"],
    ["Zerodha session", d.kite.connected ? `valid until ${when(d.kite.expires_at)}` : d.kite.configured ? "not connected today" : "API key not set", d.kite.connected],
    ["Price data", `${d.prices?.provider === "angel" ? "Angel One (free)" : "Zerodha Kite"}${d.prices?.ready ? "" : ": not set up"}`, !!d.prices?.ready],
    ["Angel One", d.angel.connected ? `connected until ${when(d.angel.expires_at)}` : "not connected", d.angel.connected],
    ["AI models", h.ai_configured ? `$${h.ai_spend_today_usd.toFixed(3)} of $${h.ai_budget_usd} today` : "no API key: AI features off", h.ai_configured],
    ["Telegram alerts", h.telegram ? "configured" : "not configured", h.telegram],
    ["Market", d.market_open ? "open" : "closed", d.market_open],
  ];

  return (
    <Page title="Dashboard" intro={d.mode.mode === "LIVE" ? "Real orders are being placed for LIVE-enabled strategies." : "Everything runs exactly as live, but orders are simulated against your paper wallet."}>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {(["PAPER", "LIVE"] as const).map((m) => (
          <div key={m} className="panel p-4">
            <Stat label={`${m === "PAPER" ? "Paper" : "Live"} P&L today`} value={signed(d.pnl[m].today.total)} valueClass={tone(d.pnl[m].today.total)}
              sub={`Realised ${signed(d.pnl[m].today.realised)} · all time ${signed(d.pnl[m].realised_all_time)}`} />
          </div>
        ))}
        <div className="panel p-4">
          <Stat label="Paper wallet cash" value={money(d.wallet.cash, false)} sub={`${money(d.wallet.invested, false)} invested of ${money(d.wallet.starting_balance, false)}`} />
        </div>
        <div className="panel p-4">
          <Stat label="Decisions today" value={Object.values<number>(d.decisions_today).reduce((a, b) => a + b, 0)}
            sub={Object.entries(d.decisions_today).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ") || "none yet"} />
        </div>
      </div>

      <Panel title={`Open positions (${d.open_positions.length})`} aside={<Link to="/trades" className="text-sm text-saffron hover:underline">All trades</Link>} pad={false}>
        <Table rows={d.open_positions} rowKey={(r: any) => r.id}
          onRow={(r: any) => r.decision_id && nav(`/decisions/${r.decision_id}`)}
          empty={<Empty title="No open positions">New entries appear here as soon as a strategy or manual invest places an order.</Empty>}
          cols={[
            { key: "stock", label: "Stock", render: (r: any) => <span className="font-semibold">{r.stock}</span> },
            { key: "mode", label: "Mode", render: (r: any) => <Pill tone={r.mode === "LIVE" ? "loss" : "gain"}>{r.mode}</Pill> },
            { key: "style", label: "Style", render: (r: any) => r.style.toLowerCase() },
            { key: "qty", label: "Qty", align: "right" },
            { key: "entry_price", label: "Entry", align: "right", render: (r: any) => money(r.entry_price) },
            { key: "last_price", label: "Last", align: "right", render: (r: any) => money(r.last_price) },
            { key: "unrealised", label: "Unrealised", align: "right", render: (r: any) => <span className={tone(r.unrealised)}>{signed(r.unrealised)}</span> },
            { key: "levels", label: "Stop / target", align: "right", render: (r: any) => `${money(r.stop_loss)} / ${money(r.target)}` },
            { key: "auto_sell", label: "Exit", render: (r: any) => (r.auto_sell ? "automatic" : "asks you") },
          ]} />
      </Panel>

      <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <Panel title="Latest decisions" aside={<Link to="/decisions" className="text-sm text-saffron hover:underline">Decision log</Link>} pad={false}>
          <ul>
            {d.recent_decisions.length === 0 && <li className="p-4 text-parch-dim">No decisions yet. They appear here the moment a rule fires, news is analysed or you invest manually.</li>}
            {d.recent_decisions.map((x: any) => (
              <li key={x.id}>
                <Link to={`/decisions/${x.id}`} className="flex items-start gap-3 border-t border-dusk-line/70 px-4 py-2.5 first:border-t-0 hover:bg-dusk-raised/50">
                  <Pill tone={decisionTone(x.type, x.side)}>{decisionLabel(x)}</Pill>
                  <div className="min-w-0 flex-1">
                    <div className="truncate"><span className="font-semibold">{x.symbol}</span> <span className="text-parch-dim">{x.summary}</span></div>
                    <div className="text-[13px] text-mute">{x.source} · {x.mode} · {when(x.ts)}</div>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </Panel>

        <div className="space-y-6">
          <Panel title="System health">
            <dl className="space-y-2">
              {healthRows.map(([k, v, ok]) => (
                <div key={k} className="flex items-baseline justify-between gap-3">
                  <dt className="flex items-center gap-2"><span className={`h-2 w-2 rounded-full ${ok ? "bg-gain" : "bg-saffron"}`} />{k}</dt>
                  <dd className="text-right text-sm text-parch-dim">{v}</dd>
                </div>
              ))}
            </dl>
            {h.heartbeats.fast_loop && <p className="mt-3 text-[13px] text-mute">Last price check {ago(h.heartbeats.fast_loop.ts)}</p>}
          </Panel>
          <Panel title="Alerts">
            {d.alerts.length === 0 ? <p className="text-parch-dim">No alerts.</p> : (
              <ul className="space-y-2">
                {d.alerts.map((a: any) => (
                  <li key={a.id} className="text-sm">
                    <div className="flex items-center gap-2"><Pill tone={a.kind === "sell" ? "warn" : a.kind === "drift" ? "loss" : "info"}>{a.kind}</Pill>
                      <span className="text-mute">{when(a.created_at)} · {a.status.toLowerCase()}</span></div>
                    <div className="mt-0.5 text-parch-dim">{plain(a.message)}</div>
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
