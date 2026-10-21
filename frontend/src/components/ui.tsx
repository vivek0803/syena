import { ReactNode, useEffect, useRef, useState } from "react";

export function Page({ title, intro, actions, children }: { title: string; intro?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className="mx-auto w-full max-w-[1320px] px-4 pb-16 pt-6 md:px-8">
      <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-2xl">
          <h1 className="h1">{title}</h1>
          {intro && <p className="mt-1 text-parch-dim">{intro}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </header>
      <div className="space-y-6">{children}</div>
    </div>
  );
}

export function Panel({ title, aside, children, className = "", pad = true }: { title?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string; pad?: boolean }) {
  return (
    <section className={`panel ${className}`}>
      {(title || aside) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-dusk-line px-4 py-3">
          {title && <h2 className="h2">{title}</h2>}
          {aside}
        </div>
      )}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

export function ModeBadge({ mode, halted }: { mode: string; halted?: boolean }) {
  const live = mode === "LIVE";
  return (
    <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-bold ${live ? "bg-loss text-dusk" : "bg-gain/20 text-gain ring-1 ring-gain/50"}`}
      aria-label={`Trading mode ${mode}${halted ? ", halted" : ""}`}>
      <span className={`h-2 w-2 rounded-full ${live ? "bg-dusk animate-pulse" : "bg-gain"}`} />
      {live ? "LIVE" : "PAPER"}{halted ? ", halted" : ""}
    </span>
  );
}

const pillTones: Record<string, string> = {
  gain: "bg-gain-soft text-gain", loss: "bg-loss-soft text-loss", warn: "bg-saffron-soft text-saffron",
  info: "bg-sky/15 text-sky", mute: "bg-dusk-raised text-parch-dim",
};
export function Pill({ tone = "mute", children }: { tone?: keyof typeof pillTones | string; children: ReactNode }) {
  return <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-[13px] font-medium ${pillTones[tone] ?? pillTones.mute}`}>{children}</span>;
}

/** Simple outcome of a decision: buy, sell (short), rejected, error; plus exited / skipped / held for exits. */
export const decisionLabel = (d?: { type?: string; side?: string | null; summary?: string } | null) => {
  if (!d?.type) return "";
  if (d.type === "PLACED") return `${d.side === "SELL" ? "sell" : "buy"}${d.summary?.startsWith("QUEUED") ? " · queued" : ""}`;
  if (d.type === "HOLD" || d.type === "REJECTED") return "rejected";
  if (d.type === "HELD") return "exit pending";
  return d.type.toLowerCase();
};
export const decisionTone = (t?: string, side?: string | null) =>
  t === "PLACED" ? (side === "SELL" ? "loss" : "gain")
    : ({ EXITED: "info", REJECTED: "mute", HOLD: "mute", ERROR: "warn", SKIPPED: "mute", HELD: "warn" } as Record<string, string>)[t ?? ""] ?? "mute";

export function Stat({ label, value, sub, valueClass = "" }: { label: string; value: ReactNode; sub?: ReactNode; valueClass?: string }) {
  return (
    <div>
      <div className="text-sm text-mute">{label}</div>
      <div className={`font-display text-2xl font-semibold ${valueClass}`}>{value}</div>
      {sub && <div className="text-sm text-parch-dim">{sub}</div>}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-2 rounded-lg border border-dashed border-dusk-line p-6">
      <div className="font-semibold">{title}</div>
      {children && <div className="max-w-xl text-parch-dim">{children}</div>}
      {action}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <div role="alert" className="rounded-lg border border-loss/40 bg-loss-soft px-3 py-2 text-sm text-loss">{(error as Error).message ?? String(error)}</div>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="py-6 text-parch-dim" aria-live="polite">{what}…</div>;
}

export type Col<T> = { key: string; label: ReactNode; render?: (r: T) => ReactNode; align?: "right" | "left"; className?: string };

export function Table<T>({ cols, rows, onRow, empty, rowKey }: { cols: Col<T>[]; rows: T[]; onRow?: (r: T) => void; empty?: ReactNode; rowKey?: (r: T, i: number) => string | number }) {
  if (!rows.length) return <div className="p-4">{empty ?? <span className="text-parch-dim">Nothing here yet.</span>}</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-sm">
        <thead><tr>{cols.map((c) => <th key={c.key} className={`th ${c.align === "right" ? "text-right" : ""}`}>{c.label}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={rowKey ? rowKey(r, i) : i} onClick={onRow ? () => onRow(r) : undefined}
              onKeyDown={onRow ? (e) => { if (e.key === "Enter") onRow(r); } : undefined} tabIndex={onRow ? 0 : undefined}
              className={onRow ? "cursor-pointer hover:bg-dusk-raised/60" : ""}>
              {cols.map((c) => (
                <td key={c.key} className={`td ${c.align === "right" ? "text-right" : ""} ${c.className ?? ""}`}>
                  {c.render ? c.render(r) : String((r as any)[c.key] ?? "–")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export type CheckT = { name: string; passed: boolean; detail: string; data?: Record<string, unknown> };

const checkNames: Record<string, string> = {
  mode: "Mode", not_halted: "Kill switch", market_hours: "Market hours", intraday_cutoff: "Intraday entry cutoff", sizing: "Position size",
  max_open_positions: "Open positions limit", max_trades_per_day: "Trades per day limit", max_daily_loss: "Daily loss limit",
  per_stock_exposure: "Per-stock exposure", circuit_limit: "Circuit limit", signal_action: "Signal", confidence: "Confidence",
  technicals: "Technicals agree", market_context: "Market context", second_opinion: "Second opinion", broker: "Broker",
  rule_signal: "Rule fired", backtest_trades: "Backtest trades", backtest_win_rate: "Backtest win rate", backtest_sharpe: "Backtest Sharpe",
  backtest_drawdown: "Backtest drawdown", paper_trades: "Paper trades", paper_days: "Paper trading days", paper_vs_backtest: "Paper vs backtest", you_confirmed: "Your confirmation", entry_range: "Entry range", exit_reason: "Exit reason", price: "Live price",
};
export const checkLabel = (n: string) => (n.startsWith("lesson:") ? `Lesson ${n.slice(7)}` : n.startsWith("plan:") ? `Plan: ${n.slice(5)}` : checkNames[n] ?? n);

/** The decision trail: every gate a decision passed through, in order, ending at the outcome. */
export function Trail({ checks, outcome, outcomeTone, sequential = true }: { checks: CheckT[]; outcome?: ReactNode; outcomeTone?: string; sequential?: boolean }) {
  const firstFail = sequential ? checks.findIndex((c) => !c.passed) : -1;
  return (
    <ol className="trail space-y-2.5" aria-label="Checks this decision went through">
      {checks.map((c, i) => (
        <li key={i} className={`trail-node ${c.passed ? "pass" : "fail"}`}>
          <div className={`flex flex-wrap items-baseline gap-x-2 ${firstFail >= 0 && i > firstFail ? "opacity-50" : ""}`}>
            <span className={`font-semibold ${c.passed ? "text-parch" : "text-loss"}`}>{checkLabel(c.name)}</span>
            <span className="text-sm text-parch-dim">{c.passed ? "passed" : "failed"}</span>
          </div>
          <div className="text-sm text-parch-dim">{c.detail}</div>
        </li>
      ))}
      {outcome && (
        <li className={`trail-node end ${outcomeTone === "loss" ? "fail" : "pass"}`}>
          <div className="font-display text-base font-semibold text-parch">{outcome}</div>
        </li>
      )}
    </ol>
  );
}

export function Json({ value, label = "Raw data" }: { value: unknown; label?: string }) {
  return (
    <details className="rounded-lg border border-dusk-line bg-dusk">
      <summary className="cursor-pointer px-3 py-2 text-sm text-parch-dim">{label}</summary>
      <pre className="max-h-96 overflow-auto px-3 pb-3 text-[12.5px] leading-relaxed text-parch-dim">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input,button,select,textarea")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus(); };
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-6" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title}
        className={`panel max-h-[92vh] w-full overflow-y-auto ${wide ? "sm:max-w-4xl" : "sm:max-w-lg"} rounded-b-none sm:rounded-panel`}>
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-dusk-line bg-dusk-panel px-4 py-3">
          <h2 className="h2">{title}</h2>
          <button className="btn-ghost px-2 py-1" onClick={onClose} aria-label="Close">Close</button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[13px] text-mute">{hint}</span>}
    </label>
  );
}

export function Tabs({ tabs, value, onChange }: { tabs: { id: string; label: ReactNode }[]; value: string; onChange: (id: string) => void }) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1 rounded-lg bg-dusk p-1">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={`rounded-md px-3 py-1.5 text-sm font-medium ${value === t.id ? "bg-dusk-raised text-parch" : "text-mute hover:text-parch"}`}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function useToast() {
  const [msg, setMsg] = useState<{ text: string; tone: string } | null>(null);
  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(() => setMsg(null), 4500);
    return () => clearTimeout(t);
  }, [msg]);
  const node = msg ? (
    <div role="status" className={`fixed bottom-4 right-4 z-[60] max-w-sm rounded-lg px-4 py-3 text-sm font-medium shadow-lg ${msg.tone === "loss" ? "bg-loss text-dusk" : "bg-parch text-dusk"}`}>{msg.text}</div>
  ) : null;
  return { show: (text: string, tone = "ok") => setMsg({ text, tone }), node };
}
