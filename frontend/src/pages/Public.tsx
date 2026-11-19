import { Suspense, lazy, useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router-dom";
import { Logo } from "../components/Layout";
import { Field } from "../components/ui";
import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";

// three.js is only downloaded when the landing page is shown
const EyeHero = lazy(() => import("../components/EyeHero"));


/** Intro: only the 3D eye. Click, press Enter/Space/Esc, or wait 5 s to continue to sign-in (or the app if signed in). */
const INTRO_MS = 5000;

export function Landing() {
  const { user } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const from = (loc.state as { from?: string } | null)?.from;
  const next = user ? from || "/app" : "/login";
  const go = useCallback(() => nav(next, { state: from ? { from } : undefined }), [nav, next, from]);
  useEffect(() => {
    const t = window.setTimeout(go, INTRO_MS);
    const onKey = (e: KeyboardEvent) => { if (["Enter", " ", "Escape"].includes(e.key)) go(); };
    window.addEventListener("keydown", onKey);
    return () => { window.clearTimeout(t); window.removeEventListener("keydown", onKey); };
  }, [go]);
  return (
    <div className="relative h-[100svh] min-h-[420px] overflow-hidden bg-ink cursor-pointer" onClick={go}>
      <h1 className="sr-only">TruthLens AI</h1>
      <Suspense fallback={<div className="eye-hero eye-hero--full" aria-hidden="true" />}>
        <EyeHero centered className="eye-hero--full" />
      </Suspense>
      <button type="button" onClick={go}
        className="sr-only focus:not-sr-only focus:absolute focus:bottom-6 focus:left-1/2 focus:-translate-x-1/2 focus:rounded-lg focus:bg-cyan focus:text-ink focus:px-4 focus:py-2 focus:font-semibold">
        {user ? "Continue to the dashboard" : "Continue to sign in"}
      </button>
    </div>
  );
}

export function AuthPage({ mode }: { mode: "login" | "register" }) {
  const { user, login, register } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to={(loc.state as { from?: string })?.from || "/app"} replace />;

  const validate = () => {
    const fe: Record<string, string> = {};
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) fe.email = "Enter a valid email address";
    if (mode === "register") {
      if (name.trim().length < 2) fe.full_name = "Enter your full name";
      if (password.length < 10 || !/[A-Z]/.test(password) || !/[a-z]/.test(password) || !/\d/.test(password)) fe.password = "At least 10 characters with upper-case, lower-case and a digit";
    } else if (!password) fe.password = "Enter your password";
    setFieldErrors(fe);
    return Object.keys(fe).length === 0;
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!validate()) return;
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(email, name, password);
      nav("/app");
    } catch (err) {
      const ae = err as ApiError;
      setError(ae.message);
      if (ae.fields?.length) setFieldErrors(Object.fromEntries(ae.fields.map((f) => [f.field, f.message])));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid-bg min-h-screen grid place-items-center p-4">
      <main id="main" className="w-full max-w-md">
        <Link to="/" className="block mb-6 text-center"><Logo className="text-xl" /></Link>
        <form onSubmit={submit} noValidate className="card p-6 grid gap-4 fade-up" aria-labelledby="auth-h">
          <h1 id="auth-h" className="text-xl font-bold">{mode === "login" ? "Sign in to your workspace" : "Create an investigator account"}</h1>
          {error && <p role="alert" className="text-bad text-sm">{error}</p>}
          {mode === "register" && (
            <Field label="Full name" error={fieldErrors.full_name}>
              {(id, d) => <input id={id} className="input" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} aria-invalid={!!fieldErrors.full_name} aria-describedby={d} />}
            </Field>
          )}
          <Field label="Email" error={fieldErrors.email}>
            {(id, d) => <input id={id} type="email" className="input" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} aria-invalid={!!fieldErrors.email} aria-describedby={d} />}
          </Field>
          <Field label="Password" error={fieldErrors.password} hint={mode === "register" ? "10+ characters, mixed case and a digit" : undefined}>
            {(id, d) => <input id={id} type="password" className="input" autoComplete={mode === "login" ? "current-password" : "new-password"} value={password} onChange={(e) => setPassword(e.target.value)} aria-invalid={!!fieldErrors.password} aria-describedby={d} />}
          </Field>
          <button className="btn btn-primary" disabled={busy} type="submit">{busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}</button>
          <p className="text-sm text-muted text-center">
            {mode === "login" ? <>No account? <Link className="text-cyan underline" to="/register">Register</Link></> : <>Already registered? <Link className="text-cyan underline" to="/login">Sign in</Link></>}
          </p>
        </form>
      </main>
    </div>
  );
}
