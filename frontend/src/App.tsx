import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { api, setCsrf } from "./lib/api";
import Algos from "./pages/Algos";
import Dashboard from "./pages/Dashboard";
import Decisions from "./pages/Decisions";
import Evals from "./pages/Evals";
import Invest from "./pages/Invest";
import Learning from "./pages/Learning";
import Login from "./pages/Login";
import Logs from "./pages/Logs";
import News from "./pages/News";
import Orders from "./pages/Orders";
import Portfolio from "./pages/Portfolio";
import Screener from "./pages/Screener";
import Compare from "./pages/Compare";
import Ask from "./pages/Ask";
import PatternLab from "./pages/PatternLab";
import Stock from "./pages/Stock";
import SettingsPage from "./pages/Settings";
import Trades from "./pages/Trades";

export type Me = { username: string; csrf_token: string };

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  const qc = useQueryClient();

  const check = useCallback(() => {
    api<Me>("/api/auth/me").then((m) => { setCsrf(m.csrf_token); setMe(m); }).catch(() => setMe(null));
  }, []);

  useEffect(() => {
    check();
    const out = () => { setMe(null); qc.clear(); };
    window.addEventListener("syena:logout", out);
    return () => window.removeEventListener("syena:logout", out);
  }, [check, qc]);

  if (me === undefined) return <div className="p-8 text-parch-dim">Loading…</div>;
  if (me === null) return <Login onDone={(m) => { setCsrf(m.csrf_token); setMe(m); }} />;

  return (
    <Layout me={me} onLogout={() => { setMe(null); qc.clear(); }}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/decisions" element={<Decisions />} />
        <Route path="/decisions/:id" element={<Decisions />} />
        <Route path="/news" element={<News />} />
        <Route path="/algos" element={<Algos />} />
        <Route path="/algos/:id" element={<Algos />} />
        <Route path="/invest" element={<Invest />} />
        <Route path="/trades" element={<Trades />} />
        <Route path="/orders" element={<Orders />} />
        <Route path="/portfolio" element={<Portfolio />} />
        <Route path="/screener" element={<Screener />} />
        <Route path="/screener/:id" element={<Screener />} />
        <Route path="/compare" element={<Compare />} />
        <Route path="/compare/:id" element={<Compare />} />
        <Route path="/stock" element={<Stock />} />
        <Route path="/stock/:symbol" element={<Stock />} />
        <Route path="/ask" element={<Ask />} />
        <Route path="/pattern-lab" element={<PatternLab />} />
        <Route path="/evals" element={<Evals />} />
        <Route path="/learning" element={<Learning />} />
        <Route path="/logs" element={<Logs />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
