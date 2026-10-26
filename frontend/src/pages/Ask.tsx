import { FormEvent, Fragment, ReactNode, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ErrorNote, Page } from "../components/ui";
import { api } from "../lib/api";

type Msg = { role: "user" | "assistant"; content: string; symbols?: string[]; refused?: boolean };
const KEY = "syena.ask.history";
const IDEAS = ["How is my portfolio doing against the recent news?", "Compare TCS and INFOSYS on valuation and growth",
  "What do support and resistance zones say about RELIANCE?", "Explain what a QIP means for shareholders"];

/** **bold** and "- " bullets, nothing else (answers are plain text). */
function Rich({ text }: { text: string }) {
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/g).map((p, i) => p.startsWith("**") && p.endsWith("**") ? <b key={i}>{p.slice(2, -2)}</b> : <Fragment key={i}>{p}</Fragment>);
  const out: ReactNode[] = [];
  let bullets: string[] = [];
  const flush = () => { if (bullets.length) { out.push(<ul key={out.length} className="my-1 list-disc space-y-0.5 pl-5">{bullets.map((b, i) => <li key={i}>{inline(b)}</li>)}</ul>); bullets = []; } };
  for (const line of text.split("\n")) {
    const m = line.match(/^\s*[-•*]\s+(.*)/);
    if (m) { bullets.push(m[1]); continue; }
    flush();
    if (line.trim()) out.push(<p key={out.length} className="my-1">{inline(line)}</p>);
  }
  flush();
  return <>{out}</>;
}

export default function Ask() {
  const [params] = useSearchParams();
  const [msgs, setMsgs] = useState<Msg[]>(() => { try { return JSON.parse(sessionStorage.getItem(KEY) || "[]"); } catch { return []; } });
  const [q, setQ] = useState(params.get("q") ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { try { sessionStorage.setItem(KEY, JSON.stringify(msgs.slice(-30))); } catch { /* private mode */ } end.current?.scrollIntoView({ block: "end" }); }, [msgs]);

  const send = async (e?: FormEvent, text?: string) => {
    e?.preventDefault();
    const question = (text ?? q).trim();
    if (!question || busy) return;
    const history = msgs.filter((m) => !m.refused).slice(-6).map(({ role, content }) => ({ role, content }));
    setMsgs((m) => [...m, { role: "user", content: question }]);
    setQ(""); setBusy(true); setErr(null);
    try {
      const r = await api<any>("/api/ask", { body: { question, history } });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, symbols: r.symbols, refused: !r.in_scope }]);
    } catch (ex) { setErr(ex); } finally { setBusy(false); }
  };

  return (
    <Page title="Ask AI" intro="Questions about stocks, the market and your portfolio, answered with Syena's own data: company figures, charts, news verdicts and your holdings. Market questions only."
      actions={msgs.length > 0 ? <button className="btn-ghost" onClick={() => setMsgs([])}>New chat</button> : undefined}>
      <div className="mx-auto max-w-3xl space-y-4">
        {!msgs.length && (
          <div className="grid gap-2 sm:grid-cols-2">{IDEAS.map((i) => (
            <button key={i} type="button" onClick={() => send(undefined, i)} className="panel p-3 text-left text-sm text-parch-dim hover:text-parch">{i}</button>))}</div>)}
        {msgs.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : ""}>
            <div className={m.role === "user" ? "max-w-[85%] rounded-xl bg-dusk-raised px-4 py-2.5" : `panel px-4 py-3 text-[15px] leading-relaxed ${m.refused ? "text-parch-dim" : ""}`}>
              {m.role === "user" ? m.content : <Rich text={m.content} />}
              {m.symbols && m.symbols.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2 text-[12px] text-mute">Used Syena data on {m.symbols.map((s) => <Link key={s} to={`/stock/${s}`} className="text-saffron hover:underline">{s}</Link>)}</div>)}
            </div>
          </div>))}
        {busy && <div className="panel px-4 py-3 text-sm text-mute">Looking at the data…</div>}
        <ErrorNote error={err} />
        <div ref={end} />
        <form onSubmit={send} className="sticky bottom-3 flex gap-2 rounded-xl border border-dusk-line bg-dusk-panel p-2 shadow-lg">
          <input className="input flex-1" value={q} onChange={(e) => setQ(e.target.value)} maxLength={1000} autoFocus
            placeholder="Ask about a stock, the market or your holdings…" aria-label="Your question" />
          <button className="btn-primary" disabled={busy || !q.trim()}>Ask</button>
        </form>
        <p className="text-center text-[12px] text-mute">Answers use Syena's latest data and can be wrong. Not investment advice.</p>
      </div>
    </Page>
  );
}
