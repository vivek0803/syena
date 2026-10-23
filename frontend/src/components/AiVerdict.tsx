import { useState } from "react";
import { Link } from "react-router-dom";
import { when } from "../lib/format";
import StockLink from "./StockLink";

export type Verdict = {
  symbol?: string; action?: string; confidence?: number | null; summary?: string | null; reasoning?: string | null;
  risks?: string[]; sentiment?: number | null; event?: string | null; materiality?: string | null; materiality_basis?: string | null; model?: string | null; prompt_version?: string | null; ts?: string;
  technicals?: { score?: number | null; reasons?: string[] | null } | null;
  decision?: { id: number; type: string; summary: string; failed?: { name: string; detail?: string } | null } | null;
  news?: { id: number; title: string; url?: string; source?: string } | null;
};

const LOOK: Record<string, { glyph: string; label: string; text: string; ring: string; bar: string; soft: string }> = {
  BUY: { glyph: "▲", label: "Buy", text: "text-gain", ring: "ring-gain/40", bar: "bg-gain", soft: "bg-gain-soft" },
  SELL: { glyph: "▼", label: "Sell short", text: "text-loss", ring: "ring-loss/40", bar: "bg-loss", soft: "bg-loss-soft" },
  HOLD: { glyph: "■", label: "Hold", text: "text-sky", ring: "ring-sky/30", bar: "bg-sky", soft: "bg-sky/10" },
};

export const prettyModel = (m?: string | null) =>
  !m ? "" : m.replace(/^claude-/, "Claude ").replace(/-(\d+)-(\d+)$/, " $1.$2").replace(/(^|\s)([a-z])/g, (_, s, c) => s + c.toUpperCase());

/** The AI's answer on one stock, presented as a card: verdict, confidence, why, risks, and what Syena did with it. */
export default function AiVerdict({ a, threshold, compact = false }: { a: Verdict; threshold?: number; compact?: boolean }) {
  const [open, setOpen] = useState(!compact);
  const look = LOOK[a.action ?? ""] ?? LOOK.HOLD;
  const conf = a.confidence ?? null;
  const d = a.decision;
  return (
    <article className={`relative overflow-hidden rounded-xl border border-dusk-line bg-dusk-panel ring-1 ring-inset ${look.ring}`}>
      <span className={`absolute inset-y-0 left-0 w-1 ${look.bar}`} aria-hidden />
      <div className="space-y-3 py-3.5 pl-5 pr-4">
        <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <span className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 font-display text-[15px] font-semibold ${look.soft} ${look.text}`}>
            <span aria-hidden>{look.glyph}</span>{look.label}
          </span>
          {a.symbol && <StockLink symbol={a.symbol} className="font-display text-lg font-semibold" />}
          {conf != null && <Confidence value={conf} threshold={threshold} tone={look.bar} />}
          {a.sentiment != null && <Sentiment value={Number(a.sentiment)} />}
          {a.ts && <span className="ml-auto text-[12px] text-mute">{when(a.ts)}</span>}
        </header>

        {a.event && a.event !== "other" && (
          <p className="flex flex-wrap items-center gap-2 text-[13px]">
            <span className="rounded-md border border-dusk-line px-2 py-0.5 text-parch-dim">{a.event.replace(/_/g, " ")}</span>
            {a.materiality && <span className={a.materiality === "high" ? "text-parch" : "text-mute"}>{a.materiality} impact</span>}
            {a.materiality_basis && <span className="text-mute">· {a.materiality_basis}</span>}
          </p>)}
        {a.summary && <p className="text-[15px] leading-relaxed text-parch">{a.summary}</p>}

        {a.reasoning && (open ? <p className="text-sm leading-relaxed text-parch-dim">{a.reasoning}</p> : (
          <button type="button" className="text-sm text-saffron hover:underline" onClick={() => setOpen(true)}>Why? Show the reasoning</button>))}

        {open && a.risks && a.risks.length > 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Risks the AI flagged">{a.risks.map((r, i) => (
            <li key={i} className="rounded-md border border-saffron/25 bg-saffron-soft/60 px-2 py-0.5 text-[13px] text-parch-dim"><span className="text-saffron" aria-hidden>⚠ </span>{r}</li>))}
          </ul>
        )}

        {open && a.technicals?.reasons && a.technicals.reasons.length > 0 && (
          <p className="text-[13px] text-mute"><span className="text-parch-dim">Technicals{a.technicals.score != null ? ` (score ${a.technicals.score})` : ""}:</span> {a.technicals.reasons.join(" · ")}</p>
        )}

        {a.news && <p className="text-[13px] text-mute">On: {a.news.url ? <a href={a.news.url} target="_blank" rel="noreferrer noopener" className="text-parch-dim hover:underline">{a.news.title}</a> : a.news.title}{a.news.source ? ` · ${a.news.source}` : ""}</p>}

        {d && <Outcome d={d} />}

        {(a.model || a.prompt_version) && <footer className="text-[12px] text-mute">{prettyModel(a.model)}{a.prompt_version ? ` · prompt ${a.prompt_version}` : ""}</footer>}
      </div>
    </article>
  );
}

function Confidence({ value, threshold, tone }: { value: number; threshold?: number; tone: string }) {
  return (
    <span className="flex items-center gap-2" aria-label={`confidence ${value} of 100${threshold != null ? `, threshold ${threshold}` : ""}`}>
      <span className="text-[12px] text-mute">confidence</span>
      <span className="relative h-1.5 w-24 rounded-full bg-dusk-line">
        <span className={`absolute inset-y-0 left-0 rounded-full ${tone}`} style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
        {threshold != null && <span className="absolute -top-1 h-3.5 w-0.5 rounded bg-parch/70" style={{ left: `${threshold}%` }} title={`your threshold ${threshold}`} />}
      </span>
      <b className="tabular-nums text-parch">{Math.round(value)}</b>
    </span>
  );
}

function Sentiment({ value }: { value: number }) {
  const label = value > 0.25 ? "positive" : value < -0.25 ? "negative" : "neutral";
  return <span className="text-[12px] text-mute">sentiment <span className="text-parch-dim">{label} ({value > 0 ? "+" : ""}{value.toFixed(2)})</span></span>;
}

function Outcome({ d }: { d: NonNullable<Verdict["decision"]> }) {
  const placed = d.type === "PLACED", rejected = d.type === "REJECTED" || d.type === "HOLD", error = d.type === "ERROR";
  const cls = placed ? "border-gain/30 bg-gain-soft/50" : error ? "border-saffron/30 bg-saffron-soft/50" : rejected ? "border-dusk-line bg-dusk" : "border-dusk-line bg-dusk";
  const icon = placed ? "✓" : error ? "!" : rejected ? "✕" : "–";
  const text = rejected && d.failed ? `Rejected at ${d.failed.name.replace(/_/g, " ")}: ${d.failed.detail ?? ""}` : d.summary;
  return (
    <Link to={`/decisions/${d.id}`} className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm hover:brightness-110 ${cls}`}>
      <span className={placed ? "text-gain" : error ? "text-saffron" : "text-mute"} aria-hidden>{icon}</span>
      <span className="text-parch-dim">{text}</span>
    </Link>
  );
}
