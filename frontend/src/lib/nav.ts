import { Briefcase, CalendarSearch, CandlestickChart, FlaskConical, Gauge, GraduationCap, HandCoins, LayoutDashboard, ListFilter, LucideIcon,
  MessagesSquare, Newspaper, NotebookPen, Flame, Layers, PieChart, ReceiptText, Scale, ScrollText, Settings, SquareTerminal, Workflow } from "lucide-react";

/** Each area of the app has its own accent: it tints the menu icons and the page header. */
export type Accent = { name: string; hex: string };
export const ACCENTS: Record<string, Accent> = {
  Overview: { name: "marigold", hex: "#F2A93B" },
  Trade: { name: "emerald", hex: "#3FC48A" },
  Understand: { name: "violet", hex: "#A98BF5" },
  System: { name: "sky", hex: "#7DB4F5" },
};

export type NavItem = { to: string; label: string; icon: LucideIcon; group: string };
export const NAV: { group: string; items: NavItem[] }[] = [
  ["Overview", [["/", "Dashboard", LayoutDashboard], ["/top-news", "Top news", Flame], ["/decisions", "Decision log", ScrollText]]],
  ["Trade", [["/algos", "Algo builder", Workflow], ["/pattern-lab", "Pattern lab", FlaskConical], ["/invest", "Manual invest", HandCoins],
    ["/screener", "Screener", ListFilter], ["/compare", "Compare stocks", Scale], ["/stock", "Stock charts", CandlestickChart], ["/options", "Option chain", Layers],
    ["/trades", "Trades & positions", Briefcase], ["/orders", "Orders", ReceiptText]]],
  ["Understand", [["/ask", "Syena AI", MessagesSquare], ["/notes", "My notes", NotebookPen], ["/news", "News feed", Newspaper], ["/review", "Daily review", CalendarSearch],
    ["/portfolio", "Portfolio X-Ray", PieChart], ["/evals", "Evals", Gauge], ["/learning", "Learning", GraduationCap]]],
  ["System", [["/logs", "Logs", SquareTerminal], ["/settings", "Settings", Settings]]],
].map(([group, items]: any) => ({ group, items: items.map(([to, label, icon]: any) => ({ to, label, icon, group })) }));

export function navFor(path: string): NavItem | undefined {
  const all = NAV.flatMap((g) => g.items);
  return all.find((i) => i.to === path) ?? all.filter((i) => i.to !== "/" && path.startsWith(i.to)).sort((a, b) => b.to.length - a.to.length)[0];
}
