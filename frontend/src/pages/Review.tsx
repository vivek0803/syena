import { ArrowDownRight, ArrowUpRight, Lightbulb } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Empty, ErrorNote, Loading, Page, Panel, SegmentBar, useToast } from "../components/ui";
import { api } from "../lib/api";
import { when } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

// what happened to each big mover, best outcome first
const CLS: Record<string, { label: string; dot: string; text: string }> = {
  traded_right: { label: "Traded right", dot: "bg-gain", text: "text-gain" },
  rejected: { label: "Caught, rejected", dot: "bg-saffron", text: "text-saffron" },
  misread: { label: "AI misread", dot: "bg-[#A98BF5]", text: "text-[#A98BF5]" },
  traded_wrong: { label: "Traded wrong way", dot: "bg-loss", text: "text-loss" },
  error: { label: "Error", dot: "bg-loss", text: "text-loss" },
  dropped: { label: "Dropped early", dot: "bg-sky", text: "text-sky" },
  sector: { label: "Sector move", dot: "bg-parch-dim", text: "text-parch-dim" },
  market: { label: "Market move", dot: "bg-parch-dim", text: "text-parch-dim" },
  no_news: { label: "No news found", dot: "bg-mute", text: "text-mute" },
};

function Mover({ m, up }: { m: any; up: boolean }) {
  const c = CLS[m.class] ?? CLS.no_news;
  return (
    <li className="rounded-xl border border-dusk-line/70 bg-dusk-deep/30 p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`grid h-7 w-7 place-items-center rounded-lg ${up ? "bg-gain-soft text-gain" : "bg-loss-soft text-loss"}`}>{up ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}</span>
        <Link to={`/stock/${m.symbol}`} className="font-semibold hover:underline">{m.symbol}</Link>
        <span className={`num text-[15px] font-semibold ${up ? "text-gain" : "text-loss"}`}>{m.change_pct > 0 ? "+" : ""}{m.change_pct}%</span>
        {m.sector && <span className="text-2xs text-mute">{m.sector}</span>}
        <span className={`ml-auto inline-flex items-center gap-1.5 text-[13px] font-medium ${c.text}`}><span className={`h-2 w-2 rounded-full ${c.dot}`} />{c.label}</span>
      </div>
      {m.why && <p className="mt-1.5 text-sm text-parch-dim">{m.why}</p>}
      {m.news?.length > 0 && <ul className="mt-2 space-y-1 border-l-2 border-dusk-line pl-3 text-[13px]">{m.news.map((n: any) => (
        <li key={n.news_id} className="text-mute"><span className="text-parch-dim">{n.title}</span> ({n.source}, {when(n.published_at)}){n.decision_id && <> <Link to={`/decisions/${n.decision_id}`} className="text-saffron hover:underline">decision</Link></>}</li>))}</ul>}
    </li>);
}

export default function Review() {
  const [day, setDay] = useState<string>("");
  const toast = useToast();
  const q = useApi<any>(`/api/review/missed${day ? `?day=${day}` : ""}`);
  const run = useAct(() => api("/api/review/missed/run", { method: "POST" }), ["/api/review"]);
  const r = q.data?.report;
  const order = Object.keys(CLS);
  return (
    <Page title="Daily review" intro="The day's biggest gainers and losers, and what Syena's news pipeline did about each: traded it, rejected it, misread it, dropped the news early, or never saw any news. Built every trading day at 5 pm."
      actions={<>
        {q.data?.days?.length > 0 && <select className="input w-auto" value={day || r?.day} onChange={(e) => setDay(e.target.value)} aria-label="Day">{q.data.days.map((d: string) => <option key={d} value={d}>{d}</option>)}</select>}
        <button className="btn-ghost" disabled={run.isPending} onClick={() => run.mutate(undefined, { onError: (e) => toast.show(e.message, "loss") })}>{run.isPending ? "Building…" : "Build for latest day"}</button>
      </>}>
      {toast.node}
      <ErrorNote error={q.error} />
      {q.isLoading ? <Loading /> : !r ? <Empty title="No review yet">It's built automatically after the close on trading days, once the day's prices are in. You can also build it now.</Empty> : (<>
        <section className="panel-hero p-6">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <div className="text-[13px] text-mute">{r.day}: movers of ±{r.min_move_pct}% or more, market cap ₹{Number(r.min_market_cap_cr).toLocaleString("en-IN")} Cr+</div>
              <div className="num mt-1 text-[34px] font-semibold leading-tight">{(r.counts.traded_right ?? 0)} of {r.gainers.length + r.losers.length} <span className="text-[17px] font-normal text-parch-dim">big moves traded the right way</span></div>
            </div>
            <div className="text-right text-[13px] text-mute">Median stock {r.market_median_pct > 0 ? "+" : ""}{r.market_median_pct}% of {r.stocks_priced}</div>
          </div>
          <div className="mt-5"><SegmentBar label="What happened" parts={order.map((k) => ({ name: CLS[k].label, value: r.counts[k] ?? 0, className: CLS[k].dot }))} /></div>
          <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[13px]">{order.filter((k) => r.counts[k]).map((k) => (
            <li key={k} className="flex items-center gap-1.5 text-parch-dim"><span className={`h-2 w-2 rounded-full ${CLS[k].dot}`} />{r.counts[k]} {CLS[k].label.replace(/^([A-Z])(?!I )/, (c: string) => c.toLowerCase())}</li>))}</ul>
        </section>
        {r.lessons && <Panel title="What to fix" icon={<Lightbulb size={17} />}>
          <div className="space-y-1.5 text-[15px] text-parch-dim">{r.lessons.split("\n").filter((l: string) => l.trim()).map((l: string, i: number) => <p key={i}>{l.replace(/^\s*[-•]\s*/, "• ")}</p>)}</div>
        </Panel>}
        <div className="grid gap-6 lg:grid-cols-2">
          <Panel title={`Top gainers (${r.gainers.length})`} icon={<ArrowUpRight size={17} />}>{!r.gainers.length ? <p className="text-parch-dim">No stock rose that much.</p> : <ul className="space-y-2.5">{r.gainers.map((m: any) => <Mover key={m.symbol} m={m} up />)}</ul>}</Panel>
          <Panel title={`Top losers (${r.losers.length})`} icon={<ArrowDownRight size={17} />}>{!r.losers.length ? <p className="text-parch-dim">No stock fell that much.</p> : <ul className="space-y-2.5">{r.losers.map((m: any) => <Mover key={m.symbol} m={m} up={false} />)}</ul>}</Panel>
        </div>
      </>)}
    </Page>
  );
}
