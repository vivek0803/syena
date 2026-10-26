import { Newspaper } from "lucide-react";
import { Link } from "react-router-dom";
import { when } from "../lib/format";
import { useApi } from "../lib/hooks";
import { Panel, Pill } from "./ui";

/** The AI's latest news verdicts on everything you own (manual-invest trades + broker/CAS holdings). */
export default function HoldingNews() {
  const { data } = useApi<any>("/api/portfolio/news?days=14", { staleTime: 60_000 } as any);
  if (!data || !data.holdings) return null;
  return (
    <Panel title="News on your holdings" icon={<Newspaper size={17} />} aside={<span className="text-[13px] text-mute">last 14 days</span>}>
      {!data.with_news.length ? <p className="text-sm text-parch-dim">No analysed news on your {data.holdings} holdings in the last two weeks.</p> : (
        <ul className="divide-y divide-dusk-line/70">{data.with_news.map((h: any) => {
          const n = h.news[0];
          return (
            <li key={h.symbol} className="py-2.5">
              <div className="flex flex-wrap items-center gap-2">
                <Link to={`/stock/${h.symbol}`} className="font-semibold hover:underline">{h.symbol}</Link>
                <Pill tone={n.action === "BUY" ? "gain" : n.action === "SELL" ? "loss" : "mute"}>{n.action === "BUY" ? "positive" : n.action === "SELL" ? "negative" : "no clear edge"}</Pill>
                {n.event && n.event !== "other" && <span className="text-[12px] text-mute">{n.event.replace(/_/g, " ")}{n.materiality ? `, ${n.materiality} impact` : ""}</span>}
                <span className="ml-auto text-[12px] text-mute">{when(n.ts)}</span>
              </div>
              <p className="mt-0.5 text-sm text-parch-dim">{n.summary}</p>
              <p className="text-[12px] text-mute">{h.holding}{h.news.length > 1 ? `; ${h.news.length - 1} earlier item${h.news.length > 2 ? "s" : ""}` : ""}</p>
            </li>);
        })}</ul>)}
      {data.quiet.length > 0 && <p className="mt-2 text-[12px] text-mute">No news: {data.quiet.join(", ")}</p>}
    </Panel>
  );
}
