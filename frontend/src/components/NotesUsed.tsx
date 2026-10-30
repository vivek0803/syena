import { NotebookPen } from "lucide-react";
import { Link } from "react-router-dom";

export type NoteRef = { id: number; title: string; influenced?: boolean };

/** Which of the owner's notes the AI read for this answer, and which it said changed its view. */
export default function NotesUsed({ items }: { items?: NoteRef[] | null }) {
  if (!items?.length) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[12px] text-mute">
      <NotebookPen size={13} className="text-[#A98BF5]" aria-hidden /><span>Your notes:</span>
      {items.map((n) => (
        <Link key={n.id} to={`/notes#note-${n.id}`} title={n.influenced ? "The AI said this note changed its view" : "Given to the AI as context"}
          className={`rounded-md border px-1.5 py-0.5 hover:text-parch ${n.influenced ? "border-[#A98BF5]/50 bg-[#A98BF5]/10 text-parch" : "border-dusk-line text-parch-dim"}`}>
          {n.influenced && <span aria-hidden>★ </span>}{n.title}</Link>))}
    </div>);
}
