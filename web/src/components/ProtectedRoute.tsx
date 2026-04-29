import { useAuth } from "../contexts/AuthContext";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, loading, tryDemo } = useAuth();
  const [demoStarting, setDemoStarting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (loading || user || demoStarting || error) return;
    setDemoStarting(true);
    tryDemo()
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Could not start demo");
      })
      .finally(() => setDemoStarting(false));
  }, [loading, user, demoStarting, error, tryDemo]);

  if (loading) return <div className="auth-loading">Loading…</div>;
  if (!user) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <div className="auth-logo">
            <img src="/kairo-logo.svg" alt="" width={32} height={32} />
            <span className="auth-logo-name">Kairo</span>
          </div>
          <h1 className="auth-title">{error ? "Demo unavailable" : "Setting up demo"}</h1>
          <p className={error ? "auth-error" : "auth-confirm"}>
            {error || "Creating a temporary account with sample data…"}
          </p>
          {error && (
            <button
              className="auth-submit"
              type="button"
              onClick={() => {
                setError("");
                setDemoStarting(false);
              }}
            >
              Try again
            </button>
          )}
        </div>
      </div>
    );
  }
  return <>{children}</>;
}
