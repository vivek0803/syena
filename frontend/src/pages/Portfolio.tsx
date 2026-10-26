import { FormEvent, Fragment, useState } from "react";
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import HoldingNews from "../components/HoldingNews";
import { Empty, ErrorNote, Field, Loading, Modal, Page, Panel, Pill, Stat, Table, Tabs, useToast } from "../components/ui";
import { api } from "../lib/api";
import { ago, money, pct, signed, tone } from "../lib/format";
import { useAct, useApi } from "../lib/hooks";

const sourceName: Record<string, string> = { zerodha: "Zerodha", angel: "Angel One", cas: "Mutual funds (CAS)" };
const sliceColors = ["#E8A33D", "#7FA8D8", "#56B488", "#C58BD6", "#E36B5D", "#B9B3A6"];

export default function Portfolio() {
  const { data: p, error, isLoading } = useApi<any>("/api/portfolio");
  const [tab, setTab] = useState("lookthrough");
  const [open, setOpen] = useState<string | null>(null);
  const [connect, setConnect] = useState<"" | "angel" | "cas" | "amc">("");
  const toast = useToast();
  const inv = ["/api/portfolio", "/api/dashboard"];
  const syncZ = useAct(() => api("/api/portfolio/sync/zerodha", { method: "POST" }), inv);
  const syncA = useAct(() => api("/api/portfolio/sync/angel", { method: "POST" }), inv);

  if (isLoading) return <Page title="Portfolio X-Ray"><Loading /></Page>;
  if (error || !p) return <Page title="Portfolio X-Ray"><ErrorNote error={error} /></Page>;

  const sources = (
    <Panel title="Sources" aside={<span className="text-sm text-mute">each sync keeps a snapshot</span>}>
      <div className="grid gap-4 md:grid-cols-3">
        {(["zerodha", "angel", "cas"] as const).map((s) => {
          const st = p.sources[s];
          return (
            <div key={s} className="rounded-lg bg-dusk p-3">
              <div className="flex items-center justify-between"><b>{sourceName[s]}</b><span className="text-sm text-mute">{st.count} holdings</span></div>
              <div className="text-sm text-parch-dim">{st.synced_at ? `Updated ${ago(st.synced_at)}` : "Not loaded yet"}{st.note ? `, ${st.note}` : ""}</div>
              <div className="mt-2 flex flex-wrap gap-2">
                {s === "zerodha" && <button className="btn-ghost py-1.5" disabled={syncZ.isPending} onClick={() => syncZ.mutate(undefined, { onError: (e) => toast.show(e.message, "loss") })}>Sync holdings and Coin MFs</button>}
                {s === "angel" && <>
                  <button className="btn-ghost py-1.5" onClick={() => setConnect("angel")}>Log in and sync</button>
                  {st.synced_at && <button className="btn-ghost py-1.5" onClick={() => syncA.mutate(undefined, { onError: (e) => toast.show(e.message, "loss") })}>Sync</button>}
                </>}
                {s === "cas" && <button className="btn-ghost py-1.5" onClick={() => setConnect("cas")}>Upload CAS PDF</button>}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-dusk-line pt-4">
        <span className="text-parch-dim">To see which stocks your funds hold, load each AMC's monthly portfolio file.</span>
        <button className="btn-ghost py-1.5" onClick={() => setConnect("amc")}>Add AMC portfolio file</button>
      </div>
    </Panel>
  );

  if (p.total_value === 0) {
    return (
      <Page title="Portfolio X-Ray" intro="Zerodha, Angel One and your mutual funds in one place, including the stocks hidden inside your funds.">
        {sources}
        <HoldingNews />
        <Empty title="Nothing loaded yet">Start with Zerodha (needs today's login), then upload your CAMS / KFintech CAS PDF. Ask for a detailed CAS at camsonline.com or mfcentral.com; it arrives by email, protected with a password you choose.</Empty>
        <ConnectModals which={connect} onClose={() => setConnect("")} toast={toast.show} />
        {toast.node}
      </Page>
    );
  }

  const classData = Object.entries(p.by_asset_class).map(([k, v]: any) => ({ name: k === "mf" ? "Mutual funds" : k === "etf" ? "ETFs" : "Stocks", value: v.value }));
  const sourceData = Object.entries(p.by_source).map(([k, v]: any) => ({ name: sourceName[k] ?? k, value: v.value }));

  return (
    <Page title="Portfolio X-Ray" intro="Zerodha, Angel One and your mutual funds in one place, including the stocks hidden inside your funds.">
      <HoldingNews />
      <div className="grid gap-4 md:grid-cols-[1.1fr_1fr_1fr]">
        <div className="panel p-4">
          <Stat label="Total value" value={money(p.total_value, false)} sub={`Top 10 stocks (look-through) are ${pct(p.top10_pct)} of everything`} />
          <ul className="mt-3 space-y-1 text-sm">
            {Object.entries(p.by_source).map(([k, v]: any) => <li key={k} className="flex justify-between"><span className="text-parch-dim">{sourceName[k] ?? k}</span><span>{money(v.value, false)} ({pct(v.pct)})</span></li>)}
          </ul>
        </div>
        <Donut title="By asset class" data={classData} />
        <Donut title="By account" data={sourceData} />
      </div>

      {p.warnings.length > 0 && (
        <section className="rounded-panel border border-saffron/40 bg-saffron-soft/30 p-4" aria-label="Concentration warnings">
          <h2 className="h2 mb-2">Worth a look</h2>
          <ul className="list-disc space-y-1 pl-5">{p.warnings.map((w: string, i: number) => <li key={i}>{w}</li>)}</ul>
        </section>
      )}

      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "lookthrough", label: "Stocks, incl. inside funds" }, { id: "holdings", label: "Every holding" },
        { id: "overlap", label: "Fund overlap" }, { id: "sectors", label: "Sectors" },
      ]} />

      {tab === "lookthrough" && (
        <Panel title="Your real exposure to each stock" aside={<span className="flex items-center gap-3 text-sm text-mute"><span className="flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-saffron" />direct</span><span className="flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-sky" />via funds</span><span>tap a row for the funds</span></span>} pad={false}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>
                <th className="th">Stock</th><th className="th text-right">Direct</th><th className="th text-right">Via funds</th>
                <th className="th text-right">Total</th><th className="th">Share of portfolio</th><th className="th text-right">Held by</th>
              </tr></thead>
              <tbody>
                {p.look_through.map((e: any) => (
                  <Fragment key={e.key}>
                    <tr className="cursor-pointer hover:bg-dusk-raised/60" tabIndex={0} onClick={() => setOpen(open === e.key ? null : e.key)}
                      onKeyDown={(k) => k.key === "Enter" && setOpen(open === e.key ? null : e.key)} aria-expanded={open === e.key}>
                      <td className="td"><b>{e.symbol ?? e.name}</b>{e.symbol && e.name !== e.symbol && <span className="ml-2 text-mute">{e.name}</span>}{e.sector && <div className="text-[13px] text-mute">{e.sector}</div>}</td>
                      <td className="td text-right">{e.direct_value ? `${money(e.direct_value, false)} (${pct(e.direct_pct)})` : "–"}</td>
                      <td className="td text-right">{e.via_funds_value ? `${money(e.via_funds_value, false)} (${pct(e.via_funds_pct)})` : "–"}</td>
                      <td className="td text-right font-semibold">{pct(e.total_pct, 2)}</td>
                      <td className="td w-48"><ShareBar direct={e.direct_pct} funds={e.via_funds_pct} /></td>
                      <td className="td text-right">{e.n_funds ? `${e.n_funds} fund${e.n_funds > 1 ? "s" : ""}` : "only direct"}</td>
                    </tr>
                    {open === e.key && e.funds.length > 0 && (
                      <tr><td colSpan={6} className="bg-dusk px-6 py-3">
                        <div className="mb-1 text-sm text-mute">{e.symbol ?? e.name} appears in {e.n_funds} of your funds</div>
                        <ul className="space-y-0.5 text-sm">{e.funds.map((f: any) => (
                          <li key={f.isin} className="flex justify-between gap-4"><span>{f.scheme}</span><span className="text-parch-dim">{pct(f.pct_of_fund, 2)} of the fund = {money(f.value, false)}</span></li>))}</ul>
                      </td></tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
          {p.missing_disclosures.length > 0 && (
            <p className="border-t border-dusk-line px-4 py-3 text-sm text-parch-dim">
              {p.missing_disclosures.length} fund(s) worth {money(p.unexplained_mf_value, false)} have no AMC file loaded, so their stocks aren't counted above: {p.missing_disclosures.map((m: any) => m.scheme).join("; ")}.
            </p>
          )}
        </Panel>
      )}

      {tab === "holdings" && (
        <Panel title="Every holding" pad={false}>
          <Table rows={p.allocation} cols={[
            { key: "name", label: "Holding", className: "whitespace-normal min-w-[220px]", render: (r: any) => <><b>{r.symbol ?? r.name}</b>{r.symbol && r.asset_class === "mf" ? <div className="text-[13px] text-mute">{r.name}</div> : null}</> },
            { key: "source", label: "Account", render: (r: any) => sourceName[r.source] ?? r.source },
            { key: "asset_class", label: "Type", render: (r: any) => (r.asset_class === "mf" ? "fund" : r.asset_class === "etf" ? "ETF" : "stock") },
            { key: "qty", label: "Qty / units", align: "right", render: (r: any) => Number(r.qty).toLocaleString("en-IN", { maximumFractionDigits: 3 }) },
            { key: "ltp", label: "Price / NAV", align: "right", render: (r: any) => money(r.ltp) },
            { key: "value", label: "Value", align: "right", render: (r: any) => money(r.value, false) },
            { key: "pct", label: "% of total", align: "right", render: (r: any) => pct(r.pct, 2) },
            { key: "pnl", label: "Gain", align: "right", render: (r: any) => <span className={tone(r.pnl)}>{signed(r.pnl)}</span> },
          ]} />
        </Panel>
      )}

      {tab === "overlap" && (
        <Panel title="How much your funds overlap" aside={<span className="text-sm text-mute">sum of the smaller weight in each shared stock</span>} pad={false}>
          <Table rows={p.fund_overlap} empty={<p className="p-1 text-parch-dim">Load at least two funds' AMC files to compare them.</p>} cols={[
            { key: "fund_a", label: "Fund", className: "whitespace-normal" },
            { key: "fund_b", label: "Overlaps with", className: "whitespace-normal" },
            { key: "common_stocks", label: "Shared stocks", align: "right" },
            { key: "overlap_pct", label: "Overlap", align: "right", render: (r: any) => <Pill tone={r.overlap_pct >= 50 ? "loss" : r.overlap_pct >= 25 ? "warn" : "mute"}>{pct(r.overlap_pct)}</Pill> },
          ]} />
          <div className="border-t border-dusk-line p-4">
            <h3 className="mb-2 font-semibold">Fund coverage</h3>
            <Table rows={p.funds} cols={[
              { key: "scheme", label: "Fund", className: "whitespace-normal" },
              { key: "value", label: "Value", align: "right", render: (r: any) => `${money(r.value, false)} (${pct(r.pct)})` },
              { key: "n_stocks", label: "Stocks", align: "right" },
              { key: "equity_pct", label: "Equity", align: "right", render: (r: any) => pct(r.equity_pct) },
              { key: "disclosure_as_of", label: "Portfolio as of" },
            ]} />
          </div>
        </Panel>
      )}

      {tab === "sectors" && (
        <Panel title="Sector exposure (look-through)">
          <div style={{ height: Math.max(220, p.sectors.length * 30) }}>
            <ResponsiveContainer>
              <BarChart data={p.sectors} layout="vertical" margin={{ left: 12, right: 24 }}>
                <XAxis type="number" stroke="#8B93A7" fontSize={12} tickFormatter={(v) => `${v}%`} />
                <YAxis type="category" dataKey="sector" stroke="#8B93A7" fontSize={12} width={170} />
                <Tooltip contentStyle={{ background: "#1B2130", border: "1px solid #2F384C", borderRadius: 8 }} formatter={(v: number) => [`${v.toFixed(2)}%`, "Share"]} />
                <Bar dataKey="pct" fill="#E8A33D" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Panel>
      )}

      {sources}
      <ConnectModals which={connect} onClose={() => setConnect("")} toast={toast.show} />
      {toast.node}
    </Page>
  );
}

function ShareBar({ direct, funds }: { direct: number; funds: number }) {
  const scale = 100 / Math.max(10, direct + funds);
  return (
    <div className="flex h-2 w-full overflow-hidden rounded-full bg-dusk" title={`direct ${direct.toFixed(1)}%, via funds ${funds.toFixed(1)}%`}>
      <div className="bg-saffron" style={{ width: `${direct * scale}%` }} />
      <div className="bg-sky" style={{ width: `${funds * scale}%` }} />
    </div>
  );
}

function Donut({ title, data }: { title: string; data: { name: string; value: number }[] }) {
  const total = data.reduce((s, d) => s + d.value, 0);
  return (
    <div className="panel flex items-center gap-4 p-4">
      <div className="h-28 w-28 shrink-0">
        <ResponsiveContainer>
          <PieChart><Pie data={data} dataKey="value" innerRadius={34} outerRadius={52} stroke="none">
            {data.map((_, i) => <Cell key={i} fill={sliceColors[i % sliceColors.length]} />)}</Pie></PieChart>
        </ResponsiveContainer>
      </div>
      <div className="min-w-0">
        <div className="text-sm text-mute">{title}</div>
        <ul className="mt-1 space-y-0.5 text-sm">{data.map((d, i) => (
          <li key={d.name} className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-sm" style={{ background: sliceColors[i % sliceColors.length] }} />
            <span className="truncate">{d.name}</span><span className="ml-auto pl-2 text-parch-dim">{pct((d.value / total) * 100)}</span></li>))}</ul>
      </div>
    </div>
  );
}

function ConnectModals({ which, onClose, toast }: { which: string; onClose: () => void; toast: (t: string, tone?: string) => void }) {
  const inv = ["/api/portfolio"];
  const [angel, setAngel] = useState({ client_code: "", pin: "", totp: "" });
  const [casPw, setCasPw] = useState("");
  const [asOf, setAsOf] = useState(() => { const d = new Date(); d.setDate(0); return d.toISOString().slice(0, 10); });
  const [url, setUrl] = useState("");
  const [result, setResult] = useState<any>(null);
  const loginA = useAct((b: any) => api("/api/portfolio/angel/login", { body: b }), inv);
  const cas = useAct((fd: FormData) => api("/api/portfolio/cas", { form: fd }), inv);
  const amc = useAct((fd: FormData) => api("/api/portfolio/disclosure", { form: fd }), inv);
  const amcUrl = useAct((b: any) => api("/api/portfolio/disclosure-url", { body: b }), inv);
  const schemes = useApi<any[]>(which === "amc" ? "/api/portfolio/schemes" : null);

  const submitCas = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    cas.mutate(fd, { onSuccess: (r: any) => { toast(`CAS imported (${r.note ?? "ok"})`); onClose(); } });
  };
  const submitAmc = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    amc.mutate(new FormData(e.currentTarget), { onSuccess: setResult });
  };

  return (
    <>
      <Modal open={which === "angel"} onClose={onClose} title="Angel One">
        <p className="mb-3 text-parch-dim">Read-only: Syena only fetches holdings. Your client code and PIN are used once and never stored; the session ends at midnight.</p>
        <div className="space-y-3">
          <Field label="Client code"><input className="input" value={angel.client_code} onChange={(e) => setAngel({ ...angel, client_code: e.target.value.toUpperCase() })} /></Field>
          <Field label="PIN"><input className="input" type="password" value={angel.pin} onChange={(e) => setAngel({ ...angel, pin: e.target.value })} /></Field>
          <Field label="Angel One authenticator code"><input className="input" inputMode="numeric" maxLength={6} value={angel.totp} onChange={(e) => setAngel({ ...angel, totp: e.target.value.replace(/\D/g, "") })} /></Field>
          <ErrorNote error={loginA.error} />
          <button className="btn-primary" disabled={loginA.isPending} onClick={() => loginA.mutate(angel, { onSuccess: () => { toast("Angel One holdings synced"); onClose(); } })}>Log in and sync</button>
        </div>
      </Modal>

      <Modal open={which === "cas"} onClose={onClose} title="Upload your CAS">
        <form onSubmit={submitCas} className="space-y-3">
          <p className="text-parch-dim">Use a <b>detailed</b> Consolidated Account Statement from CAMS, KFintech or MF Central. A depository CAS from NSDL/CDSL also works and adds stocks held at other brokers.</p>
          <Field label="CAS PDF"><input className="input" type="file" name="file" accept="application/pdf" required /></Field>
          <Field label="PDF password"><input className="input" type="password" name="password" required value={casPw} onChange={(e) => setCasPw(e.target.value)} /></Field>
          <ErrorNote error={cas.error} />
          <button className="btn-primary" disabled={cas.isPending}>{cas.isPending ? "Reading…" : "Import"}</button>
        </form>
      </Modal>

      <Modal open={which === "amc"} onClose={() => { setResult(null); onClose(); }} title="AMC monthly portfolio" wide>
        <div className="grid gap-6 md:grid-cols-2">
          <div className="space-y-4">
            <p className="text-parch-dim">Every fund house publishes an Excel file each month listing what each scheme holds. Download it from the AMC's website (usually “Monthly portfolio” under Downloads / Statutory disclosures) and upload it here, or paste the file's link.</p>
            <form onSubmit={submitAmc} className="space-y-3">
              <Field label="Excel file (.xlsx / .xls)"><input className="input" type="file" name="file" accept=".xlsx,.xls" required /></Field>
              <Field label="Portfolio date"><input className="input" type="date" name="as_of" value={asOf} onChange={(e) => setAsOf(e.target.value)} required /></Field>
              <input type="hidden" name="mapping" value="{}" />
              <button className="btn-primary" disabled={amc.isPending}>{amc.isPending ? "Reading…" : "Import file"}</button>
            </form>
            <div className="flex gap-2">
              <input className="input" placeholder="https://… link to the .xlsx" value={url} onChange={(e) => setUrl(e.target.value)} />
              <button className="btn-ghost" disabled={!url || amcUrl.isPending} onClick={() => amcUrl.mutate({ url, as_of: asOf }, { onSuccess: setResult })}>Fetch</button>
            </div>
            <ErrorNote error={amc.error || amcUrl.error} />
          </div>
          <div>
            {result ? (
              <div className="space-y-2">
                <p><b>{result.matched}</b> of {result.schemes_in_file} schemes in the file matched your funds.</p>
                <ul className="max-h-72 space-y-1 overflow-y-auto text-sm">{result.details.map((d: any, i: number) => (
                  <li key={i} className={d.matched ? "" : "text-mute"}>{d.matched ? `✓ ${d.matched} (${d.stored} holdings)` : `– ${d.scheme} (not one of your funds)`}</li>))}</ul>
              </div>
            ) : (
              <>
                <div className="mb-2 text-sm text-mute">Your funds</div>
                <ul className="space-y-1 text-sm">{(schemes.data ?? []).map((s) => (
                  <li key={s.isin ?? s.name} className="flex justify-between gap-3"><span>{s.name}</span><span className={s.disclosure_as_of ? "text-gain" : "text-saffron"}>{s.disclosure_as_of ?? "missing"}</span></li>))}</ul>
                {schemes.data?.length === 0 && <p className="text-parch-dim">Import your CAS first so files can be matched to your funds.</p>}
              </>
            )}
          </div>
        </div>
      </Modal>
    </>
  );
}
