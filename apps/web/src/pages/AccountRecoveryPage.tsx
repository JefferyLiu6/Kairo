import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { confirmAccountEmail, requestAccountEmail } from "../api";

export function AccountRecoveryPage({ mode }: { mode: "forgot" | "resend" | "verify" | "reset" }) {
  const confirm = mode === "verify" || mode === "reset";
  const [token, setToken] = useState(() => new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    // Keep email tokens in memory only, and out of history/address-bar copies.
    if (window.location.hash) window.history.replaceState(null, "", window.location.pathname);
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setError("");
    if (confirm && password !== repeat) { setError("Passwords do not match."); return; }
    setBusy(true);
    try {
      const result = confirm
        ? await confirmAccountEmail(token, password, mode === "verify")
        : await requestAccountEmail(email.trim(), mode === "resend");
      setMessage(result.detail);
      setToken(""); setPassword(""); setRepeat("");
    } catch (err) { setError(err instanceof Error ? err.message : "Please try again."); }
    finally { setBusy(false); }
  }
  const title = { forgot: "Recover your account", resend: "Resend verification email", verify: "Verify email and set password", reset: "Set a new password" }[mode];
  return <main className="auth-page"><section className="auth-card">
    <Link to="/" className="auth-logo-name">Kairo</Link>
    <h1 className="auth-title">{title}</h1>
    {message ? <p role="status" className="auth-confirm">{message}</p> : confirm && !token ?
      <p role="alert">This page needs the link from your email. Reopen that link or request a new one below.</p> :
      <form className="auth-form" onSubmit={submit}>
        {confirm ? <>
          <p>Choose your password to confirm this request. Existing sessions will be signed out. An enabled authenticator remains required.</p>
          <label>New password<input type="password" autoComplete="new-password" required minLength={8} maxLength={1024}
            value={password} onChange={e => setPassword(e.target.value)} disabled={busy} /></label>
          <label>Confirm password<input type="password" autoComplete="new-password" required minLength={8} maxLength={1024}
            value={repeat} onChange={e => setRepeat(e.target.value)} disabled={busy} /></label>
        </> : <label>Email<input type="email" autoComplete="email" required maxLength={254}
          value={email} onChange={e => setEmail(e.target.value)} disabled={busy} /></label>}
        {error && <p role="alert" className="auth-error">{error}</p>}
        <button className="auth-submit" disabled={busy}>{busy ? "Please wait…" : confirm ? "Save password" : "Send email"}</button>
      </form>}
    <div className="auth-links">
      <Link to="/login">Back to sign in</Link>
      {confirm && <Link to={mode === "verify" ? "/verify-email/request" : "/forgot-password"}>Request a new link</Link>}
    </div>
  </section></main>;
}
