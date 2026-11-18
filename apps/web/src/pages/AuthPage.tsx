import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

export function AuthPage({ signup = false }: { signup?: boolean }) {
  const { user, loading, login, signup: createAccount } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [created, setCreated] = useState(false);

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
      setError(err instanceof Error ? err.message : "Unable to sign in. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <div className="auth-loading" role="status">Loading…</div>;
  if (user) return <Navigate to="/app" replace />;

  return (
    <main className="auth-page">
      <div className="auth-card">
        <Link to="/" className="auth-logo">
          <img src="/kairo-logo.svg" alt="" width={32} height={32} />
          <span className="auth-logo-name">Kairo</span>
        </Link>
        <h1 className="auth-title">{signup ? "Create your account" : "Sign in to Kairo"}</h1>
        {created ? (
          <>
            <p className="auth-confirm" role="status">
              If this account needs verification, an email will arrive shortly. Open the link to verify your email and set your password before signing in.
            </p>
            <Link to="/login">Continue to sign in</Link>
          </>
        ) : (
          <form className="auth-form" onSubmit={submit}>
            {signup && (
              <label>Display name
                <input autoComplete="name" required maxLength={100} value={name}
                  onChange={(event) => setName(event.target.value)} disabled={busy} />
              </label>
            )}
            <label>Email
              <input type="email" autoComplete="username" required maxLength={254} value={email}
                onChange={(event) => setEmail(event.target.value)} disabled={busy} />
            </label>
            <label>Password
              <input type="password" autoComplete={signup ? "new-password" : "current-password"}
                required minLength={signup ? 8 : undefined} maxLength={1024} value={password}
                onChange={(event) => setPassword(event.target.value)} disabled={busy}
                aria-describedby={signup ? "password-hint" : undefined} />
            </label>
            {signup && <p id="password-hint">Use at least 8 characters. A long, unique passphrase is best.</p>}
            {!signup && <label>Authenticator or recovery code (if enabled)
              <input autoComplete="one-time-code" maxLength={64} value={code}
                onChange={(event) => setCode(event.target.value)} disabled={busy} />
            </label>}
            {error && <p className="auth-error" role="alert">{error}</p>}
            <button className="auth-submit" disabled={busy} type="submit">
              {busy ? "Please wait…" : signup ? "Create account" : "Sign in"}
            </button>
          </form>
        )}
        <div className="auth-links">
          <Link to={signup ? "/login" : "/signup"}>
            {signup ? "Already have an account? Sign in" : "Create an account"}
          </Link>
          <Link to="/forgot-password">Forgot password?</Link>
          <Link to="/verify-email/request">Resend verification email</Link>
          <Link to="/demo">Try a temporary demo</Link>
        </div>
      </div>
    </main>
  );
}
