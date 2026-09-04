import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  Mail,
  LockKeyhole,
  UserRound,
  ArrowRight,
  AlertCircle,
} from "lucide-react";
import { api, analytics } from "../lib/api";
import { supabase } from "../lib/supabase";
export default function Signup({ refresh }: { refresh: () => void }) {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const pkg = params.get("package");
  const [success, setSuccess] = useState("");
  const [name, setName] = useState(""),
    [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    analytics("signup_started");
    try {
      await api("/api/auth/preflight/signup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          data: {
            display_name: name,
          },
        },
      });

      if (error) throw error;

      if (!data.session) {
        setSuccess(
          "Account created. Check your email and click the verification link before logging in.",
        );
        return;
      }

      await api("/api/auth/convert-guest", {
        method: "POST",
      }).catch(() => {});

      refresh();

      analytics("signup_completed");

      nav(pkg ? `/pricing?package=${pkg}` : "/dashboard");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="auth-page">
      <div className="auth-panel">
        <div className="pill">Create your account</div>
        <h1>
          Keep every
          <br />
          <em>look you love.</em>
        </h1>
        <p>
          Your guest try-ons can follow you into your account, then paid credits
          unlock the full experience.
        </p>
      </div>
      <form className="auth-card" onSubmit={submit}>
        <h2>Create account</h2>
        <label>
          Name
          <div className="field">
            <UserRound />
            <input
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Your name"
            />
          </div>
        </label>
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
              minLength={8}
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="At least 8 characters"
            />
          </div>
        </label>
        {error && (
          <div className="error-box">
            <AlertCircle size={17} />
            {error}
          </div>
        )}
        {success && <div className="success-box">{success}</div>}
        <button className="button large full" disabled={busy}>
          {busy ? (
            "Creating…"
          ) : (
            <>
              Create account <ArrowRight size={18} />
            </>
          )}
        </button>
        <p className="form-foot">
          Already have an account?{" "}
          <Link to={pkg ? `/login?package=${pkg}` : "/login"}>Log in</Link>
        </p>
      </form>
    </section>
  );
}
