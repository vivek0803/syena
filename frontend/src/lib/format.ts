const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const inr0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const money = (v?: number | null, dp = true) => (v == null ? "–" : `₹${(dp ? inr : inr0).format(v)}`);
export const signed = (v?: number | null) => (v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}₹${inr.format(Math.abs(v))}`);
export const pct = (v?: number | null, dp = 1) => (v == null ? "–" : `${v.toFixed(dp)}%`);
export const ratio = (v?: number | null) => (v == null ? "–" : `${(v * 100).toFixed(1)}%`);
export const num = (v?: number | null, dp = 2) => (v == null ? "–" : Number(v).toFixed(dp));
export const tone = (v?: number | null) => (v == null || v === 0 ? "text-parch" : v > 0 ? "text-gain" : "text-loss");

export const when = (iso?: string | null) => {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
};
export const day = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric" }) : "–";
export const ago = (iso?: string | null) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
};
