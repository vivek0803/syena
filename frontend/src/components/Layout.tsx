import { ReactNode, useCallback, useEffect, useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import type { Me } from "../App";
import { api } from "../lib/api";
import { useAct, useApi, useLiveStream } from "../lib/hooks";
import { ModeBadge, Modal, useToast } from "./ui";

const nav: { group: string; items: [string, string][] }[] = [
  { group: "Overview", items: [["/", "Dashboard"], ["/decisions", "Decision log"]] },
  { group: "Trade", items: [["/algos", "Algo builder"], ["/invest", "Manual invest"], ["/screener", "Screener"], ["/compare", "Compare stocks"], ["/stock", "Stock charts"], ["/trades", "Trades & positions"], ["/orders", "Orders"]] },
  { group: "Understand", items: [["/ask", "Ask AI"], ["/news", "News feed"], ["/portfolio", "Portfolio X-Ray"], ["/evals", "Evals"], ["/learning", "Learning"]] },
  { group: "System", items: [["/logs", "Logs"], ["/settings", "Settings"]] },
];

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

  return (
    <div className="min-h-screen md:grid md:grid-cols-[232px_1fr]">
      <aside className={`${open ? "block" : "hidden"} fixed inset-0 z-40 overflow-y-auto bg-dusk md:static md:block md:border-r md:border-dusk-line`}>
        <div className="flex items-center justify-between px-5 py-5">
          <NavLink to="/" className="flex items-center gap-2.5">
            <img src="/favicon.svg" alt="" className="h-8 w-8" />
            <span className="font-display text-xl font-semibold tracking-tight">Syena</span>
          </NavLink>
          <button className="btn-ghost px-2 py-1 md:hidden" onClick={() => setOpen(false)}>Close</button>
        </div>
        <nav className="space-y-5 px-3 pb-8" aria-label="Main">
          {nav.map((g) => (
            <div key={g.group}>
              <div className="px-2 pb-1 text-[13px] text-mute">{g.group}</div>
              {g.items.map(([to, label]) => (
                <NavLink key={to} to={to} end={to === "/"}
                  className={({ isActive }) => `block rounded-lg px-2.5 py-1.5 text-[15px] ${isActive ? "bg-dusk-raised font-semibold text-parch" : "text-parch-dim hover:text-parch"}`}>
                  {label}
                </NavLink>
              ))}
            </div>
          ))}
          <div className="border-t border-dusk-line px-2 pt-4 text-sm text-mute">
            Signed in as {me.username}
            <button className="mt-2 block text-parch-dim underline-offset-2 hover:underline" onClick={logout}>Sign out</button>
          </div>
        </nav>
      </aside>

      <div className="min-w-0">
        <header className="sticky top-0 z-30 flex flex-wrap items-center gap-2 border-b border-dusk-line bg-dusk/95 px-4 py-2.5 backdrop-blur md:px-8">
          <button className="btn-ghost px-2.5 py-1.5 md:hidden" onClick={() => setOpen(true)} aria-label="Open menu">Menu</button>
          {m && <ModeBadge mode={m.mode} halted={m.halted} />}
          {k && (k.connected ? (
            <span className="text-sm text-parch-dim">Zerodha connected{k.user_id ? ` (${k.user_id})` : ""}</span>
          ) : (
            <button className="btn-ghost py-1.5" disabled={connect.isPending || !k.configured}
              title={k.configured ? "Daily Kite login" : "Set SYENA_KITE_API_KEY on the server first"}
              onClick={() => connect.mutate(undefined, { onSuccess: (r) => (window.location.href = r.url), onError: (e) => toast.show(e.message, "loss") })}>
              Connect Zerodha
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            {m && !m.halted && <button className="btn-danger py-1.5" onClick={() => setHaltOpen(true)}>Halt trading</button>}
            {m?.halted && <span className="text-sm text-loss">Halted: {m.halt_reason}</span>}
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
