import { Briefcase, CandlestickChart, FlaskConical, Gauge, GraduationCap, HandCoins, LayoutDashboard, ListFilter, LogOut, LucideIcon, Menu, MessagesSquare,
  Newspaper, PieChart, Power, ReceiptText, Scale, ScrollText, Settings, SquareTerminal, Workflow, X } from "lucide-react";
import { ReactNode, useCallback, useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import type { Me } from "../App";
import { api } from "../lib/api";
import { useAct, useApi, useLiveStream } from "../lib/hooks";
import { ModeBadge, Modal, useToast } from "./ui";

const nav: { group: string; items: [string, string, LucideIcon][] }[] = [
  { group: "Overview", items: [["/", "Dashboard", LayoutDashboard], ["/decisions", "Decision log", ScrollText]] },
  { group: "Trade", items: [["/algos", "Algo builder", Workflow], ["/pattern-lab", "Pattern lab", FlaskConical], ["/invest", "Manual invest", HandCoins],
    ["/screener", "Screener", ListFilter], ["/compare", "Compare stocks", Scale], ["/stock", "Stock charts", CandlestickChart],
    ["/trades", "Trades & positions", Briefcase], ["/orders", "Orders", ReceiptText]] },
  { group: "Understand", items: [["/ask", "Ask AI", MessagesSquare], ["/news", "News feed", Newspaper], ["/portfolio", "Portfolio X-Ray", PieChart],
    ["/evals", "Evals", Gauge], ["/learning", "Learning", GraduationCap]] },
  { group: "System", items: [["/logs", "Logs", SquareTerminal], ["/settings", "Settings", Settings]] },
];

/** NSE session in India time: open 09:15-15:30 on weekdays (holidays come from the dashboard's market flag). */
function useMarketClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => { const t = setInterval(() => setNow(new Date()), 30_000); return () => clearInterval(t); }, []);
  const ist = new Date(now.getTime() + (now.getTimezoneOffset() + 330) * 60_000);
  const mins = ist.getHours() * 60 + ist.getMinutes(), day = ist.getDay();
  const weekday = day >= 1 && day <= 5, open = weekday && mins >= 555 && mins < 930;
  const fmt = (m: number) => (m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`);
  if (open) return { open: true, text: `Market open, closes in ${fmt(930 - mins)}` };
  if (weekday && mins < 555) return { open: false, text: `Market opens in ${fmt(555 - mins)}` };
  return { open: false, text: "Market closed" };
}

const kiteMessages: Record<string, [string, string]> = {
  connected: ["Zerodha connected for today.", "ok"],
  cancelled: ["Zerodha login was cancelled.", "loss"],
  invalid: ["That Zerodha login link expired. Tap Connect Zerodha again.", "loss"],
  error: ["Zerodha rejected the login. Check the API key and secret on the server.", "loss"],
};

export default function Layout({ me, onLogout, children }: { me: Me; onLogout: () => void; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [haltOpen, setHaltOpen] = useState(false);
  const [reason, setReason] = useState("");
  const toast = useToast();
  const loc = useLocation();
  const navigate = useNavigate();
  const mode = useApi<any>("/api/mode", { refetchInterval: 30_000 });
  const kite = useApi<any>("/api/kite/status", { refetchInterval: 60_000 });

  const onEvent = useCallback((kind: string, data: any) => {
    if (kind === "alert") toast.show(data.message ?? `Alert: ${data.reason ?? data.kind}`);
    if (kind === "trade_closed") toast.show(`${data.symbol} closed: P&L ₹${Number(data.pnl).toFixed(2)}`);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useLiveStream(true, onEvent);

  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    const k = new URLSearchParams(loc.search).get("kite");
    if (k && kiteMessages[k]) {
      toast.show(...kiteMessages[k]);
      navigate(loc.pathname, { replace: true });
      kite.refetch();
    }
  }, [loc.search]); // eslint-disable-line react-hooks/exhaustive-deps

  const halt = useAct((r: string) => api("/api/mode/halt", { body: { reason: r || "manual kill switch" } }), ["/api/mode", "/api/dashboard"]);
  const connect = useAct(() => api<{ url: string }>("/api/kite/login-url", { method: "POST" }));
  const logout = async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => null); onLogout(); };

  const m = mode.data;
  const k = kite.data;
  const clock = useMarketClock();

  return (
    <div className="min-h-screen md:grid md:grid-cols-[248px_1fr]">
      <aside className={`${open ? "block" : "hidden"} fixed inset-0 z-40 overflow-y-auto bg-dusk-deep md:sticky md:top-0 md:block md:h-screen md:border-r md:border-dusk-line/70 md:bg-dusk-deep/60 md:backdrop-blur`}>
        <div className="flex items-center justify-between px-5 pb-4 pt-5">
          <NavLink to="/" className="flex items-center gap-2.5">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-saffron-soft ring-1 ring-saffron/30"><img src="/favicon.svg" alt="" className="h-6 w-6" /></span>
            <span className="font-display text-[21px] font-semibold tracking-tight">Syena</span>
          </NavLink>
          <button className="btn-ghost px-2 py-1.5 md:hidden" onClick={() => setOpen(false)} aria-label="Close menu"><X size={18} /></button>
        </div>
        <nav className="space-y-5 px-3 pb-6" aria-label="Main">
          {nav.map((g) => (
            <div key={g.group}>
              <div className="px-3 pb-1.5 text-[12px] font-medium text-mute/80">{g.group}</div>
              {g.items.map(([to, label, Icon]) => (
                <NavLink key={to} to={to} end={to === "/"}
                  className={({ isActive }) => `group relative flex items-center gap-3 rounded-xl px-3 py-[7px] text-[14.5px] transition-colors ${isActive
                    ? "bg-dusk-raised text-parch font-semibold before:absolute before:-left-3 before:top-1.5 before:bottom-1.5 before:w-1 before:rounded-r-full before:bg-saffron"
                    : "text-parch-dim hover:bg-dusk-raised/50 hover:text-parch"}`}>
                  {({ isActive }) => <><Icon size={17} strokeWidth={isActive ? 2.2 : 1.8} className={isActive ? "text-saffron" : "text-mute group-hover:text-parch-dim"} />{label}</>}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
        <div className="mx-3 mb-5 flex items-center gap-3 rounded-xl border border-dusk-line/70 bg-dusk-panel/60 px-3 py-2.5">
          <span className="grid h-8 w-8 place-items-center rounded-full bg-sky-soft font-display text-sm font-semibold uppercase text-sky">{me.username.slice(0, 1)}</span>
          <div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{me.username}</div><div className="text-2xs text-mute">signed in for 24 h</div></div>
          <button className="rounded-lg p-1.5 text-mute hover:bg-dusk-raised hover:text-parch" onClick={logout} aria-label="Sign out" title="Sign out"><LogOut size={16} /></button>
        </div>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex flex-wrap items-center gap-2.5 border-b border-dusk-line/70 bg-dusk/80 px-4 py-2.5 backdrop-blur-md md:px-8">
          <button className="btn-ghost px-2.5 py-1.5 md:hidden" onClick={() => setOpen(true)} aria-label="Open menu"><Menu size={18} /></button>
          {m && <ModeBadge mode={m.mode} halted={m.halted} />}
          <span className="chip hidden sm:inline-flex" title="NSE equity session, India time">
            <span className={`h-2 w-2 rounded-full ${clock.open ? "bg-gain shadow-[0_0_0_3px_rgba(63,196,138,0.2)]" : "bg-mute"}`} />{clock.text}
          </span>
          {k && (k.connected ? (
            <span className="chip hidden md:inline-flex"><span className="h-2 w-2 rounded-full bg-gain" />Zerodha{k.user_id ? ` ${k.user_id}` : ""}</span>
          ) : (
            <button className="btn-ghost py-1.5" disabled={connect.isPending || !k.configured}
              title={k.configured ? "Daily Kite login" : "Set SYENA_KITE_API_KEY on the server first"}
              onClick={() => connect.mutate(undefined, { onSuccess: (r) => (window.location.href = r.url), onError: (e) => toast.show(e.message, "loss") })}>
              <span className="sm:hidden">Zerodha</span><span className="hidden sm:inline">Connect Zerodha</span>
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            {m && !m.halted && <button className="btn-danger-ghost py-1.5" onClick={() => setHaltOpen(true)} aria-label="Halt trading"><Power size={15} /><span className="hidden sm:inline">Halt trading</span></button>}
            {m?.halted && <span className="chip border-loss/40 text-loss">Halted: {m.halt_reason}</span>}
          </div>
        </header>
        <main>{children}</main>
      </div>

      <Modal open={haltOpen} onClose={() => setHaltOpen(false)} title="Halt all trading">
        <p className="mb-3 text-parch-dim">
          Stops every new order immediately and switches to PAPER. Open positions keep being monitored. You can resume from Settings; going LIVE again needs your password and 2FA code.
        </p>
        <label className="block"><span className="label">Reason (optional)</span>
          <input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. market too volatile" /></label>
        <div className="mt-4 flex justify-end gap-2">
          <button className="btn-ghost" onClick={() => setHaltOpen(false)}>Cancel</button>
          <button className="btn-danger" onClick={() => halt.mutate(reason, { onSuccess: () => { setHaltOpen(false); toast.show("Trading halted. Mode is PAPER."); } })}>
            Halt now
          </button>
        </div>
      </Modal>
      {toast.node}
    </div>
  );
}
