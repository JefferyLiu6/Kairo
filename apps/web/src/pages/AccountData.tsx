import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { deleteAccount, exportAccount } from "../api";

function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** Take your data with you, or delete the account. Both ask for the password again. */
export function AccountData({ email, mfaEnabled, onDeleted }: {
  email: string;
  mfaEnabled: boolean;
  onDeleted: () => void;
}) {
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [typedEmail, setTypedEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const emailMatches = typedEmail.trim().toLowerCase() === email.toLowerCase();

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true); setError(""); setMessage("");
    try { await action(); }
    catch (err) { setError(err instanceof Error ? err.message : "Please try again."); }
    finally { setBusy(false); }
  }

  function download(event: FormEvent) {
    event.preventDefault();
    void run(async () => {
      const { blob, filename } = await exportAccount(password, code.trim());
      saveFile(blob, filename);
      setPassword(""); setCode("");
      setMessage(`Your data was saved as ${filename}.`);
    });
  }

  function remove() {
    void run(async () => {
      await deleteAccount(password, code.trim());
      // What this browser kept for the account goes too.
      try { localStorage.clear(); } catch { /* private mode */ }
      onDeleted();
    });
  }

  return <form className="auth-form security-section" onSubmit={download}>
    <h2>Your data</h2>
    <p>Download a copy of everything Kairo holds about you, or delete your account. See
      the <Link to="/privacy">privacy policy</Link> for what is stored.</p>
    {message && <p role="status" className="auth-confirm">{message}</p>}
    {error && <p role="alert" className="auth-error">{error}</p>}
    <label>Current password<input type="password" autoComplete="current-password" required maxLength={1024}
      value={password} onChange={e => setPassword(e.target.value)} disabled={busy} /></label>
    {mfaEnabled && <label>Authenticator or recovery code
      <input autoComplete="one-time-code" required maxLength={64} value={code}
        onChange={e => setCode(e.target.value)} disabled={busy} />
      <small>Each code works once. Downloading and then deleting takes two codes.</small>
    </label>}
    {deleting ? <>
      <p><strong>This cannot be undone.</strong> Your account, schedule, tasks, habits, journal, notes,
        chat history and what Kairo remembers about you are deleted, and its access to a connected
        calendar is ended. Download your data first if you want a copy.</p>
      <label>Type {email} to confirm<input autoComplete="off" value={typedEmail}
        onChange={e => setTypedEmail(e.target.value)} disabled={busy} /></label>
      <button type="button" className="auth-danger" onClick={remove}
        disabled={busy || !emailMatches || !password || (mfaEnabled && !code)}>
        {busy ? "Deleting…" : "Delete my account permanently"}</button>
      <button type="button" className="auth-quiet" disabled={busy} onClick={() => { setDeleting(false); setTypedEmail(""); setError(""); }}>Cancel</button>
    </> : <>
      <button className="auth-submit" disabled={busy}>{busy ? "Preparing…" : "Download my data"}</button>
      <button type="button" className="auth-danger" disabled={busy}
        onClick={() => { setDeleting(true); setMessage(""); setError(""); }}>Delete my account…</button>
    </>}
  </form>;
}
