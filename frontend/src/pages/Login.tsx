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
    <main className="grid min-h-screen place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="h-10 w-10" />
          <div>
            <div className="font-display text-3xl font-semibold tracking-tight">Syena</div>
            <div className="text-parch-dim">Patient. Fast. Precise.</div>
          </div>
        </div>
        <form onSubmit={submit} className="panel space-y-4 p-5">
          <label className="block"><span className="label">Username</span>
            <input className="input" autoComplete="username" required value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} /></label>
          <label className="block"><span className="label">Password</span>
            <input className="input" type="password" autoComplete="current-password" required value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></label>
          <label className="block"><span className="label">6-digit code from your authenticator</span>
            <input className="input tracking-[0.4em]" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required
              value={form.totp} onChange={(e) => setForm({ ...form, totp: e.target.value.replace(/\D/g, "") })} /></label>
          {err && <p role="alert" className="text-sm text-loss">{err}</p>}
          <button className="btn-primary w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </div>
    </main>
  );
}
