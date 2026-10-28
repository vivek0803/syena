/** Web pages the AI read for live market data. */
export default function Sources({ items }: { items?: { title: string; url: string }[] }) {
  if (!items?.length) return null;
  const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
  return (
    <div className="mt-2 border-t border-dusk-line/60 pt-2 text-[12px] text-mute">
      <span>Live sources:</span>
      <ol className="mt-1 space-y-0.5">{items.slice(0, 8).map((s, i) => (
        <li key={s.url}>{i + 1}. <a href={s.url} target="_blank" rel="noreferrer" className="text-sky hover:underline">{s.title}</a> <span className="opacity-70">· {host(s.url)}</span></li>))}</ol>
    </div>);
}
