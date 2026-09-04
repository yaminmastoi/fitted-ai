import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
// import { Link, useNavigate } from "react-router-dom";
import { Mail, LockKeyhole, ArrowRight, AlertCircle } from "lucide-react";
import { api, analytics } from "../lib/api";
import { supabase } from "../lib/supabase";
export default function Login({ refresh }: { refresh: () => void }) {
  //   const nav = useNavigate();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const pkg = params.get("package");
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/api/auth/preflight/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const r = await api<{ access_token: string; refresh_token: string }>(
        "/api/auth/login",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email, password }),
        },
      );
      await supabase.auth.setSession({
        access_token: r.access_token,
        refresh_token: r.refresh_token,
      });
      await api("/api/auth/convert-guest", { method: "POST" }).catch(() => {});
      analytics("login_completed");
      refresh();
      nav(pkg ? `/pricing?package=${pkg}` : '/dashboard');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="auth-page">
      <div className="auth-panel">
        <div className="pill">Welcome back</div>
        <h1>
          Log in to
          <br />
          <em>your looks.</em>
        </h1>
        <p>
          Access credits, saved try-ons, billing and your ad-free paid
          experience.
        </p>
      </div>
      <form className="auth-card" onSubmit={submit}>
        <h2>Log in</h2>
        <label>
          Email
          <div className="field">
            <Mail />
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </div>
        </label>
        <label>
          Password
          <div className="field">
            <LockKeyhole />
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
            />
          </div>
        </label>
        {error && (
          <div className="error-box">
            <AlertCircle size={17} />
            {error}
          </div>
        )}
        <button className="button large full" disabled={busy}>
          {busy ? (
            "Logging in…"
          ) : (
            <>
              Log in <ArrowRight size={18} />
            </>
          )}
        </button>
        <p className="form-foot">
          New to Fitted? <Link to="/signup">Create account</Link>
        </p>
      </form>
    </section>
  );
}
