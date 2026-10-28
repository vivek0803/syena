import { ArrowLeft, CandlestickChart, Check, ExternalLink, X } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import StockLink from "../components/StockLink";
import { Empty, ErrorNote, Loading, Page, Panel } from "../components/ui";
import { day, when } from "../lib/format";
import { useApi } from "../lib/hooks";

const n = (v: any, d = 2) => (v === null || v === undefined ? "" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }));
const SECTIONS = [["analysis", "Analysis"], ["peers", "Peers"], ["quarters", "Quarters"], ["profit-loss", "Profit & Loss"], ["balance-sheet", "Balance Sheet"],
  ["cash-flow", "Cash Flows"], ["ratios", "Ratios"], ["investors", "Investors"], ["documents", "Documents"]];

function Ratio({ label, value, suffix = "" }: { label: string; value: any; suffix?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 border-b border-dusk-line/50 py-2 text-sm">
      <span className="text-mute">{label}</span><span className="num font-medium text-parch">{value === "" || value == null ? "–" : <>{value}{suffix}</>}</span>
    </div>);
}

function StatementTable({ t, highlight = [] }: { t: any; highlight?: string[] }) {
  if (!t?.rows?.length) return <p className="text-sm text-mute">Not available yet: it arrives with the nightly company-figures update.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-right text-[13px]">
        <thead><tr className="text-mute"><th className="sticky left-0 bg-dusk-panel py-2 text-left font-medium" />{t.periods.map((p: string) => <th key={p} className="px-3 py-2 font-medium">{p}</th>)}</tr></thead>
        <tbody className="divide-y divide-dusk-line/60">{t.rows.map((r: any) => (
          <tr key={r.key} className={highlight.includes(r.key) ? "font-semibold text-parch" : "text-parch-dim"}>
            <td className="sticky left-0 bg-dusk-panel py-1.5 pr-4 text-left">{r.label}{r.label.endsWith("%") ? "" : ""}</td>
            {r.values.map((v: any, i: number) => <td key={i} className="num px-3">{n(v, r.key === "eps" ? 2 : r.key.endsWith("pct") || r.key === "opm" ? 1 : 0)}{v != null && (r.key === "opm" || r.key === "tax_pct") ? "%" : ""}</td>)}
          </tr>))}</tbody>
      </table>
    </div>);
}

export default function Company() {
  const { symbol = "" } = useParams();
  const q = useApi<any>(`/api/screener/company/${encodeURIComponent(symbol.toUpperCase())}`);
  const c = q.data;
  if (q.isLoading) return <Page title={symbol.toUpperCase()}><Loading /></Page>;
  if (!c) return <Page title={symbol.toUpperCase()}><ErrorNote error={q.error} /></Page>;
  const s = c.summary;
  const qChart = (c.quarterly?.periods ?? []).map((p: string, i: number) => ({ p, Sales: c.quarterly.rows.find((r: any) => r.key === "sales")?.values[i], "Net profit": c.quarterly.rows.find((r: any) => r.key === "net_profit")?.values[i] }));
  return (
    <Page title={c.name ?? c.symbol} intro={<span className="flex flex-wrap items-center gap-x-3 gap-y-1"><span className="font-mono text-parch">NSE: {c.symbol}</span>{c.industry && <span>{c.sector ? `${c.sector} · ` : ""}{c.industry}</span>}
      <Link to={`/stock/${c.symbol}`} className="inline-flex items-center gap-1 text-saffron hover:underline"><CandlestickChart size={14} /> Chart</Link>
      <Link to="/screener" className="inline-flex items-center gap-1 text-mute hover:text-parch"><ArrowLeft size={14} /> Screener</Link></span>}>
      <nav className="sticky top-0 z-10 -mx-4 mb-5 flex gap-1 overflow-x-auto border-b border-dusk-line bg-dusk/90 px-4 py-2 backdrop-blur md:-mx-8 md:px-8">
        {SECTIONS.map(([id, label]) => <a key={id} href={`#${id}`} className="whitespace-nowrap rounded-md px-2.5 py-1 text-[13px] text-parch-dim hover:bg-dusk-raised hover:text-parch">{label}</a>)}
      </nav>
      <div className="space-y-6">
        <Panel>
          <div className="flex flex-wrap items-baseline gap-3">
            <span className="num text-3xl font-semibold text-parch">₹ {n(s.price)}</span>
            {s.change != null && <span className={`num text-lg ${s.change >= 0 ? "text-gain" : "text-loss"}`}>{s.change >= 0 ? "+" : ""}{n(s.change)}%</span>}
            {c.price_day && <span className="text-[12px] text-mute">{day(c.price_day)} close</span>}
          </div>
          <div className="mt-3 grid gap-x-10 sm:grid-cols-2 lg:grid-cols-3">
            <Ratio label="Market Cap" value={s.market_cap != null ? `₹ ${n(s.market_cap, 0)}` : null} suffix=" Cr." />
            <Ratio label="Current Price" value={s.price != null ? `₹ ${n(s.price)}` : null} />
            <Ratio label="High / Low" value={s.high_52w != null ? `₹ ${n(s.high_52w)} / ${n(s.low_52w)}` : null} />
            <Ratio label="Stock P/E" value={s.loss_making === 1 ? "loss-making" : n(s.pe, 1)} />
            <Ratio label="PEG ratio" value={n(s.peg)} />
            <Ratio label="Book Value" value={s.book_value != null ? `₹ ${n(s.book_value)}` : null} />
            <Ratio label="Price to book" value={n(s.pb)} />
            <Ratio label="Dividend Yield" value={n(s.dividend_yield)} suffix=" %" />
            <Ratio label="ROE" value={n(s.roe, 1)} suffix=" %" />
            <Ratio label="ROCE (3y avg)" value={n(s.roce_3y, 1)} suffix=" %" />
            <Ratio label="EPS (TTM)" value={s.eps != null ? `₹ ${n(s.eps)}` : null} />
            <Ratio label="Debt to equity" value={n(s.debt_to_equity)} />
            <Ratio label="Sales growth (YoY)" value={n(s.sales_growth, 1)} suffix=" %" />
            <Ratio label="Profit growth (YoY)" value={n(s.profit_growth, 1)} suffix=" %" />
            <Ratio label="Promoter holding" value={n(s.promoter_holding, 1)} suffix=" %" />
          </div>
        </Panel>

        <div id="analysis" className="grid scroll-mt-20 gap-4 md:grid-cols-2">
          <section className="rounded-2xl border border-gain/30 bg-gain-soft/10 p-5">
            <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-gain">Pros</h3>
            {c.pros.length ? <ul className="space-y-1.5 text-sm text-parch-dim">{c.pros.map((x: string) => <li key={x} className="flex gap-2"><Check size={15} className="mt-0.5 shrink-0 text-gain" />{x}</li>)}</ul> : <p className="text-sm text-mute">None flagged.</p>}
          </section>
          <section className="rounded-2xl border border-loss/30 bg-loss-soft/10 p-5">
            <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-loss">Cons</h3>
            {c.cons.length ? <ul className="space-y-1.5 text-sm text-parch-dim">{c.cons.map((x: string) => <li key={x} className="flex gap-2"><X size={15} className="mt-0.5 shrink-0 text-loss" />{x}</li>)}</ul> : <p className="text-sm text-mute">None flagged.</p>}
          </section>
        </div>

        <div id="peers" className="scroll-mt-20"><Panel title="Peer comparison" aside={<span className="text-[12px] text-mute">{c.industry}</span>} pad={false}>
          {!c.peers.length ? <div className="p-5"><Empty title="No peers">Industry not known for this stock yet.</Empty></div> : (
            <div className="overflow-x-auto px-2 pb-3"><table className="w-full min-w-[760px] text-right text-[13px]">
              <thead className="text-mute"><tr>{["Name", "CMP ₹", "P/E", "PEG", "Mar Cap ₹ Cr", "Div Yld %", "ROE %", "ROCE 3y %", "Sales gr %", "Profit gr %"].map((h, i) => <th key={h} className={`px-3 py-2 font-medium ${i ? "" : "text-left"}`}>{h}</th>)}</tr></thead>
              <tbody className="divide-y divide-dusk-line/60">{c.peers.map((p: any) => (
                <tr key={p.symbol} className={p.symbol === c.symbol ? "bg-saffron/5" : ""}>
                  <td className="px-3 py-1.5 text-left"><Link to={`/company/${p.symbol}`} className="text-sky hover:underline">{p.name ?? p.symbol}</Link></td>
                  <td className="num px-3">{n(p.price)}</td><td className="num px-3">{p.loss_making === 1 ? "loss" : n(p.pe, 1)}</td><td className="num px-3">{n(p.peg)}</td>
                  <td className="num px-3">{n(p.market_cap, 0)}</td><td className="num px-3">{n(p.dividend_yield)}</td><td className="num px-3">{n(p.roe, 1)}</td>
                  <td className="num px-3">{n(p.roce_3y, 1)}</td><td className="num px-3">{n(p.sales_growth, 1)}</td><td className="num px-3">{n(p.profit_growth, 1)}</td>
                </tr>))}</tbody></table></div>)}
        </Panel></div>

        <div id="quarters" className="scroll-mt-20"><Panel title="Quarterly Results" aside={<span className="text-[12px] text-mute">₹ Crores</span>}>
          {qChart.length > 1 && <div className="mb-4 h-48"><ResponsiveContainer><BarChart data={qChart}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="p" tick={{ fontSize: 11 }} /><YAxis tick={{ fontSize: 11 }} width={48} /><Tooltip /><Bar dataKey="Sales" fill="#7DB4F5" radius={[3, 3, 0, 0]} /><Bar dataKey="Net profit" fill="#3FC48A" radius={[3, 3, 0, 0]} /></BarChart></ResponsiveContainer></div>}
          <StatementTable t={c.quarterly} highlight={["sales", "net_profit"]} />
        </Panel></div>

        <div id="profit-loss" className="scroll-mt-20"><Panel title="Profit & Loss" aside={<span className="text-[12px] text-mute">₹ Crores</span>}>
          <StatementTable t={c.profit_loss} highlight={["sales", "net_profit"]} />
          {c.growth && Object.keys(c.growth).length > 0 && (
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              {[["Compounded Sales Growth", c.growth.sales], ["Compounded Profit Growth", c.growth.profit]].map(([k, v]) => (
                <div key={k as string} className="rounded-lg border border-dusk-line p-3 text-sm"><div className="text-mute">{k} ({c.growth.years}y)</div><div className="num mt-1 text-lg font-semibold">{v != null ? `${v}%` : "–"}</div></div>))}
              <div className="rounded-lg border border-dusk-line p-3 text-sm"><div className="text-mute">Stock Price CAGR</div>
                <div className="num mt-1 text-[13px]">{["1y", "3y", "5y"].map((k) => <span key={k} className="mr-3">{k}: <b className={c.growth[`price_${k}`] >= 0 ? "text-gain" : "text-loss"}>{c.growth[`price_${k}`] != null ? `${c.growth[`price_${k}`]}%` : "–"}</b></span>)}</div></div>
            </div>)}
        </Panel></div>

        <div id="balance-sheet" className="scroll-mt-20"><Panel title="Balance Sheet" aside={<span className="text-[12px] text-mute">₹ Crores</span>}><StatementTable t={c.balance_sheet} highlight={["total_assets", "borrowings"]} /></Panel></div>
        <div id="cash-flow" className="scroll-mt-20"><Panel title="Cash Flows" aside={<span className="text-[12px] text-mute">₹ Crores</span>}><StatementTable t={c.cash_flow} highlight={["operating", "free_cash_flow"]} /></Panel></div>

        <div id="ratios" className="scroll-mt-20"><Panel title="Ratios">
          {!c.ratios.length ? <p className="text-sm text-mute">Not available yet.</p> : (
            <div className="overflow-x-auto"><table className="w-full min-w-[480px] text-right text-[13px]">
              <thead className="text-mute"><tr><th className="py-2 text-left font-medium" />{c.ratios.map((r: any) => <th key={r.period} className="px-3 py-2 font-medium">{r.period}</th>)}</tr></thead>
              <tbody className="divide-y divide-dusk-line/60">{[["roce", "ROCE %"], ["roe", "ROE %"], ["opm", "OPM %"], ["debtor_days", "Debtor Days"], ["inventory_days", "Inventory Days"]].map(([k, lbl]) => (
                <tr key={k} className="text-parch-dim"><td className="py-1.5 text-left">{lbl}</td>{c.ratios.map((r: any) => <td key={r.period} className="num px-3">{n(r[k], 1)}</td>)}</tr>))}</tbody></table></div>)}
        </Panel></div>

        <div id="investors" className="scroll-mt-20"><Panel title="Shareholding Pattern" aside={<span className="text-[12px] text-mute">Numbers in percentages · NSE filings</span>}>
          {!c.shareholding.length ? <p className="text-sm text-mute">Not loaded yet: new filings are read every 30 minutes, older ones a batch each night.</p> : (
            <div className="overflow-x-auto"><table className="w-full min-w-[480px] text-right text-[13px]">
              <thead className="text-mute"><tr><th className="py-2 text-left font-medium" />{[...c.shareholding].reverse().map((h: any) => <th key={h.as_of} className="px-3 py-2 font-medium">{new Date(h.as_of).toLocaleDateString("en-IN", { month: "short", year: "numeric" })}</th>)}</tr></thead>
              <tbody className="divide-y divide-dusk-line/60">{[["promoter", "Promoters"], ["fii", "FIIs"], ["dii", "DIIs"], ["mf", "  Mutual funds"], ["retail", "Public (retail)"], ["shareholders", "No. of Shareholders"]].map(([k, lbl]) => (
                <tr key={k} className="text-parch-dim"><td className="whitespace-pre py-1.5 text-left">{lbl}</td>{[...c.shareholding].reverse().map((h: any) => <td key={h.as_of} className="num px-3">{n(h[k], k === "shareholders" ? 0 : 2)}</td>)}</tr>))}</tbody></table></div>)}
        </Panel></div>

        <div id="documents" className="scroll-mt-20"><Panel title="Announcements & news">
          {!c.news.length ? <p className="text-sm text-mute">No relevant announcements in Syena's feed yet.</p> : (
            <ul className="divide-y divide-dusk-line/60">{c.news.map((x: any) => (
              <li key={x.id} className="flex items-start gap-3 py-2 text-sm"><a href={x.url} target="_blank" rel="noreferrer" className="flex-1 text-parch-dim hover:text-parch">{x.title} <ExternalLink size={11} className="inline" /></a><span className="whitespace-nowrap text-[12px] text-mute">{x.source} · {when(x.at)}</span></li>))}</ul>)}
        </Panel></div>
        <p className="text-[12px] text-mute">{c.note}{c.fundamentals_at ? ` Company figures updated ${when(c.fundamentals_at)}.` : ""} <StockLink symbol={c.symbol} /></p>
      </div>
    </Page>
  );
}
