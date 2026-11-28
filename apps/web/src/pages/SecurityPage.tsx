import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { fetchSecurity, setupMfa, updateMfa, type SecurityStatus } from "../api";
import { useAuth } from "../contexts/AuthContext";
import { AccountData } from "./AccountData";

export function SecurityPage() {
  const { user } = useAuth();
  const [status, setStatus] = useState<SecurityStatus | null>(null);
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [deleted, setDeleted] = useState(false);
  useEffect(() => {
    let active = true;
    if (!user?.isDemo) fetchSecurity().then(s => { if (active) setStatus(s); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [user?.id, user?.isDemo]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    if (codes.length) window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [codes.length]);
  async function act(action: "setup" | "enable" | "disable" | "recovery") {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try {
      if (action === "setup") {
        setSetup(await setupMfa(password));
        setMessage("Add this key to your authenticator, then enter its six-digit code. Setup expires in 10 minutes.");
      } else {
        const result = await updateMfa(action, password, code.trim());
        setSetup(null); setCodes(result.recovery_codes); setPassword(""); setCode("");
        setStatus(s => s ? { ...s, mfa_enabled: result.mfa_enabled, recovery_codes_remaining: result.recovery_codes.length } : s);
        setMessage(action === "disable" ? "MFA disabled. Other devices were signed out." : "MFA is enabled. Save your recovery codes now. Other devices were signed out.");
      }
    } catch (err) { setError(err instanceof Error ? err.message : "Please try again."); }
    finally { setBusy(false); }
  }
  function submit(e: FormEvent) { e.preventDefault(); void act(setup ? "enable" : status?.mfa_enabled ? "recovery" : "setup"); }
  // The session is gone with the account. A plain link reloads the app so it starts signed out.
  if (deleted) return <main className="auth-page"><section className="auth-card security-card">
    <span className="auth-logo-name">Kairo</span>
    <h1 className="auth-title">Your account was deleted</h1>
    <p role="status">Your account and everything stored in it are gone, and Kairo’s access to any
      connected calendar was ended. A confirmation was sent to your email address.</p>
    <a href="/">Back to the Kairo home page</a>
  </section></main>;
  return <main className="auth-page"><section className="auth-card security-card">
    <Link to="/app" className="auth-logo-name">Kairo</Link>
    <h1 className="auth-title">Account security</h1>
    {user?.isDemo ? <p>Authenticator security is available for real accounts. Demo accounts expire after 24 hours.</p> : <>
      {status && <p>Email verified · Authenticator {status.mfa_enabled ? "enabled" : "not enabled"}
        {status.mfa_enabled && ` · ${status.recovery_codes_remaining} recovery codes remaining`}</p>}
      {message && <p role="status" className="auth-confirm">{message}</p>}
      {error && <p role="alert" className="auth-error">{error}</p>}
      {codes.length > 0 ? <div className="auth-form">
        <p>Store these codes in your password manager. Each works once, together with your password. These codes are shown only now; regenerating them invalidates the old set.</p>
        <label>Recovery codes<textarea readOnly rows={10} value={codes.join("\n")} aria-label="Recovery codes" /></label>
        <button className="auth-submit" onClick={() => { setCodes([]); setMessage("Recovery codes saved. Keep them somewhere safe."); }}>I have saved these codes</button>
      </div> : status && <form className="auth-form" onSubmit={submit}>
        <p>Use any authenticator that supports time-based codes. Keep backup codes in case you lose the device.</p>
        <label>Current password<input type="password" autoComplete="current-password" required maxLength={1024}
          value={password} onChange={e => setPassword(e.target.value)} disabled={busy} /></label>
        {setup && <div className="auth-form">
          <p>In your authenticator choose “Add account” → “Enter setup key”, select time-based codes, and paste this key:</p>
          <label>Setup key<input readOnly value={setup.secret} /></label>
          <a href={setup.otpauth_uri}>Open in an authenticator on this device</a>
        </div>}
        {(setup || status.mfa_enabled) && <label>{setup ? "Six-digit authenticator code" : "Authenticator or recovery code"}
          <input autoComplete="one-time-code" required maxLength={64} value={code} onChange={e => setCode(e.target.value)} disabled={busy} />
          <small>Codes cannot be reused. If you just signed in with an authenticator code, wait for the next one.</small>
        </label>}
        <button className="auth-submit" disabled={busy}>{busy ? "Please wait…" : setup ? "Enable authenticator" : status.mfa_enabled ? "Replace recovery codes" : "Set up authenticator"}</button>
        {status.mfa_enabled && <button type="button" disabled={busy || !password || !code} onClick={() => void act("disable")}>Disable authenticator</button>}
      </form>}
      {status && user && !codes.length && <AccountData email={user.email}
        mfaEnabled={status.mfa_enabled} onDeleted={() => setDeleted(true)} />}
    </>}
    <div className="auth-links">{!codes.length && <Link to="/app">Back to Kairo</Link>}<Link to="/forgot-password">Reset password</Link>
      <Link to="/privacy">Privacy policy</Link><Link to="/terms">Terms</Link></div>
  </section></main>;
}
