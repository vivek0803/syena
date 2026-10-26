import { FormEvent, useState } from "react";
import type { Me } from "../App";
import { api } from "../lib/api";

export default function Login({ onDone }: { onDone: (m: Me) => void }) {
  const [form, setForm] = useState({ username: "", password: "", totp: "" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api<{ username: string; csrf_token: string }>("/api/auth/login", { body: form });
      onDone(r);
    } catch (e: any) {
      setErr(e.status === 401 ? "Those details didn't work. Check your password and the current code in your authenticator app." : e.message);
      setForm((f) => ({ ...f, totp: "" }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <section className="relative hidden overflow-hidden border-r border-dusk-line/70 bg-dusk-deep lg:flex lg:flex-col lg:justify-between lg:p-12" aria-hidden>
        {/* a falcon's flight path that is also a price line */}
        <svg className="absolute inset-x-0 top-[16%] h-[42%] w-full" viewBox="0 0 600 400" preserveAspectRatio="none">
          <defs>
            <linearGradient id="flight" x1="0" x2="1"><stop offset="0" stopColor="#F2A93B" stopOpacity="0" /><stop offset="1" stopColor="#F2A93B" /></linearGradient>
            <linearGradient id="under" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stopColor="#F2A93B" stopOpacity="0.14" /><stop offset="1" stopColor="#F2A93B" stopOpacity="0" /></linearGradient>
          </defs>
          <path d="M0 330 C60 320 90 290 130 300 S200 340 240 290 S300 200 340 230 S410 260 450 170 S530 90 600 56 L600 400 L0 400 Z" fill="url(#under)" />
          <path d="M0 330 C60 320 90 290 130 300 S200 340 240 290 S300 200 340 230 S410 260 450 170 S530 90 600 56" fill="none" stroke="url(#flight)" strokeWidth="2.5" />
        </svg>
        <div className="relative flex items-center gap-3">
          <span className="grid h-11 w-11 place-items-center rounded-2xl bg-saffron-soft ring-1 ring-saffron/30"><img src="/favicon.svg" alt="" className="h-7 w-7" /></span>
          <span className="font-display text-2xl font-semibold tracking-tight">Syena</span>
        </div>
        <div className="relative max-w-md">
          <p className="font-display text-[44px] font-semibold leading-[1.05] tracking-tight">Patient. Fast. Precise.</p>
          <p className="mt-4 text-[16px] leading-relaxed text-parch-dim">Your NSE trading desk: news read the moment it lands, rules that never sleep, and every decision written down with its reasons.</p>
        </div>
      </section>

      <section className="grid place-items-center px-4 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 lg:hidden">
            <img src="/favicon.svg" alt="" className="h-10 w-10" />
            <div className="font-display text-3xl font-semibold tracking-tight">Syena</div>
          </div>
          <h1 className="h1">Sign in</h1>
          <p className="mb-6 mt-1 text-parch-dim">You stay signed in for 24 hours.</p>
          <form onSubmit={submit} className="space-y-4">
            <label className="block"><span className="label">Username</span>
              <input className="input py-2.5" autoComplete="username" required value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
            <label className="block"><span className="label">Password</span>
              <input className="input py-2.5" type="password" autoComplete="current-password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
            <label className="block"><span className="label">6-digit code from your authenticator</span>
              <input className="input py-2.5 text-center font-display text-lg tracking-[0.5em]" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required
                value={form.totp} onChange={(e) => setForm({ ...form, totp: e.target.value.replace(/\D/g, "") })} /></label>
            {err && <p role="alert" className="rounded-xl border border-loss/40 bg-loss-soft px-3 py-2 text-sm text-loss">{err}</p>}
            <button className="btn-primary w-full py-2.5" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
          </form>
        </div>
      </section>
    </main>
  );
}
