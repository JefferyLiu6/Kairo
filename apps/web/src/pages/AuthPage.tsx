import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

import { EntryBrand } from "./EntryBrand";

export function AuthPage({ signup = false }: { signup?: boolean }) {
  const { user, loading, login, signup: createAccount } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState(false);

  const [factorStep, setFactorStep] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (signup) {
        await createAccount(email.trim(), password, name.trim());
        setPassword("");
        setCreated(true);
      } else {
        await login(email.trim(), password, code.trim());
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Please try again.";
      if (!signup && !factorStep && message.startsWith("Enter a valid authenticator or recovery code")) {
        setFactorStep(true);
        setShowPassword(false);
      } else {
        setError(message);
      }
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="auth-loading" role="status">Loading…</div>;
  if (user) return <Navigate to="/app" replace />;

  return (
    <main className="auth-page entry-auth">
      <section className="auth-card">
        <EntryBrand />
        <h1 className="auth-title">{created ? "Check your inbox" : factorStep ? "Verify your sign-in" : signup ? "Create your account" : "Welcome back"}</h1>
        {created ? <>
          <p role="status">If this account needs verification, a link will arrive at <strong>{email}</strong>. Open it to verify your address and set your password.</p>
          <Link to="/login" className="entry-action">Continue to sign in</Link>
          <Link to="/verify-email/request">Resend verification email</Link>
        </> : <>
          <form className="auth-form" onSubmit={submit}>
            {factorStep ? <>
              <p>Enter an authenticator or recovery code for <strong>{email}</strong>.</p>
              <label>Authenticator or recovery code
                <input autoFocus required autoComplete="one-time-code" maxLength={64} value={code}
                  onChange={e => setCode(e.target.value)} disabled={busy} />
              </label>
            </> : <>
              {signup && <label>Display name<input required autoComplete="name" maxLength={100} value={name}
                onChange={e => setName(e.target.value)} disabled={busy} /></label>}
              <label>Email address<input required type="email" autoComplete="username" maxLength={254} value={email}
                onChange={e => setEmail(e.target.value)} disabled={busy} /></label>
              <div className="entry-field">
                <div className="entry-field-head"><label htmlFor="entry-password">Password</label>
                  {!signup && <Link to="/forgot-password">Forgot password?</Link>}</div>
                <div className="entry-password"><input id="entry-password" required type={showPassword ? "text" : "password"}
                  autoComplete={signup ? "new-password" : "current-password"} minLength={signup ? 8 : undefined}
                  maxLength={1024} value={password} onChange={e => setPassword(e.target.value)} disabled={busy}
                  aria-describedby={signup ? "password-hint" : undefined} />
                  <button type="button" aria-label={showPassword ? "Hide password" : "Show password"}
                    onClick={() => setShowPassword(!showPassword)}>{showPassword ? "Hide" : "Show"}</button></div>
                {signup && <small id="password-hint">Use at least 8 characters.</small>}
              </div>
            </>}
            {error && <div role="alert" className="entry-error">{error}
              {error.startsWith("Verify your email") && <> <Link to="/verify-email/request">Resend verification email</Link></>}
            </div>}
            <button className="auth-submit" disabled={busy}>{busy ? "Please wait…" : factorStep ? "Verify and sign in" : signup ? "Create account" : "Sign in"}</button>
            {factorStep && <button className="entry-back" type="button" disabled={busy}
              onClick={() => { setFactorStep(false); setCode(""); setPassword(""); setError(""); }}>Back to sign in</button>}
          </form>
          {!factorStep && <div className="entry-auth-foot">
            <p>{signup ? "Already have an account? " : "New to Kairo? "}<Link to={signup ? "/login" : "/signup"}>{signup ? "Sign in" : "Create an account"}</Link></p>
            <Link to="/demo">Try a temporary demo ↗</Link>
          </div>}
        </>}
      </section>
    </main>
  );
}
