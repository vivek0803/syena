import { ExternalLink, TrendingDown, TrendingUp, Waves } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorNote, Loading, Page, Pill, useToast } from "../components/ui";
import { api } from "../lib/api";
import { when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const DIR: Record<string, { label: string; tone: string; icon: any; ring: string }> = {
  positive: { label: "positive", tone: "gain", icon: TrendingUp, ring: "from-gain/25" },
  negative: { label: "negative", tone: "loss", icon: TrendingDown, ring: "from-loss/25" },
  mixed: { label: "mixed", tone: "warn", icon: Waves, ring: "from-saffron/25" },
};

function Card({ t }: { t: any }) {
  const d = DIR[t.direction] ?? DIR.mixed;
  const Icon = d.icon;
  const syms: string[] = Array.from(new Set([...(t.linked ?? []), ...(t.symbols ?? [])]));
  return (
    <li className={`relative overflow-hidden rounded-2xl border border-dusk-line/70 bg-gradient-to-br ${d.ring} via-transparent to-transparent p-4`}>
      <div className="flex items-start gap-3">
        <span className="num grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-dusk-raised text-[17px] font-semibold text-parch">{t.rank}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[16px] font-semibold leading-snug text-parch">{t.headline}</h3>
          </div>
          <p className="mt-1 text-sm text-parch-dim">{t.why}</p>
          <div className="mt-2.5 flex flex-wrap items-center gap-2 text-[13px]">
            <Pill tone={d.tone}><Icon size={13} className="-ml-0.5" /> {d.label}</Pill>
            <Pill tone={t.impact === "high" ? "loss" : t.impact === "medium" ? "warn" : "mute"} dot={false}>{t.impact} impact</Pill>
            {syms.map((s) => {
              const mv = t.moves?.[s];
              const known = /^[A-Z0-9&-]+$/.test(s);
              return known ? (
                <Link key={s} to={`/stock/${s}`} className="rounded-md bg-dusk px-2 py-0.5 font-medium text-parch hover:bg-dusk-raised">
                  {s}{mv != null && <span className={mv > 0 ? "text-gain" : mv < 0 ? "text-loss" : "text-mute"}> {mv > 0 ? "+" : ""}{mv}%</span>}
                </Link>) : <span key={s} className="rounded-md bg-dusk px-2 py-0.5 text-parch-dim">{s}</span>;
            })}
            {t.syena_verdict && <span className="text-mute">Syena AI: {t.syena_verdict}</span>}
          </div>
          <a href={t.url} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1 text-[12px] text-mute hover:text-parch">
            {t.source} · {when(t.published_at)} · “{t.title.slice(0, 110)}{t.title.length > 110 ? "…" : ""}” <ExternalLink size={11} />
          </a>
        </div>
      </div>
    </li>);
}

export default function TopNews() {
  const [day, setDay] = useState("");
  const toast = useToast();
  const q = useApi<any>(`/api/top-news${day ? `?day=${day}` : ""}`);
  const run = useAct(() => api("/api/top-news/run", { method: "POST" }), ["/api/top-news"]);
  const r = q.data?.report;
  return (
    <Page title="Top news" intro="The day's 10 most important stories for NSE investors, ranked by likely impact on prices. Built every evening at 6 PM from everything Syena read since 6 PM the day before, and sent on Telegram."
      actions={<>
        {q.data?.days?.length > 0 && <select className="input w-auto" value={day || r?.day} onChange={(e) => setDay(e.target.value)} aria-label="Day">{q.data.days.map((d: string) => <option key={d} value={d}>{d}</option>)}</select>}
        <button className="btn-ghost" disabled={run.isPending} onClick={() => run.mutate(undefined, { onError: (e) => toast.show(e.message, "loss"), onSuccess: () => setDay("") })}>{run.isPending ? "Building…" : "Build now"}</button>
      </>}>
      {toast.node}
      <ErrorNote error={q.error} />
      {q.isLoading ? <Loading /> : !r ? <Empty title="No list yet">It is built automatically at 6 PM. You can also build it now from today's news so far.</Empty> : (
        <div className="space-y-5">
          <section className="panel-hero p-5">
            <div className="text-[13px] text-mute">{r.day} · {r.considered} stories considered · built {when(r.at)}</div>
            {r.market_mood && <p className="mt-1.5 text-[18px] font-medium leading-snug text-parch">{r.market_mood}</p>}
          </section>
          {!r.top.length ? <Empty title="Nothing stood out">No story was important enough to make the list.</Empty> :
            <ol className="grid gap-3 lg:grid-cols-2">{r.top.map((t: any) => <Card key={t.news_id} t={t} />)}</ol>}
          <p className="text-[12px] text-mute">Ranked by the AI from Syena's news feed; not investment advice. Each story links to its source and to the stock pages, with today's price move where known.</p>
        </div>)}
    </Page>
  );
}
