import { useAuth } from "../contexts/AuthContext";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { EntryBrand } from "../pages/EntryBrand";
import { Navigate } from "react-router-dom";
import { WalkthroughLink } from "./WalkthroughLink";

export function ProtectedRoute({ children, demo = false }: { children: ReactNode; demo?: boolean }) {
  const { user, loading, tryDemo } = useAuth();
  const [demoStarting, setDemoStarting] = useState(false);
  const [slowStart, setSlowStart] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!demo || loading || user || demoStarting || error) return;
    setDemoStarting(true);
    tryDemo()
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Could not start demo");
      })
      .finally(() => setDemoStarting(false));
  }, [demo, loading, user, demoStarting, error, tryDemo]);

  useEffect(() => {
    if (!demoStarting) {
      setSlowStart(false);
      return;
    }
    const timer = setTimeout(() => setSlowStart(true), 4000);
    return () => clearTimeout(timer);
  }, [demoStarting]);

  if (!demo && loading) return <div className="auth-loading" role="status">Loading…</div>;
  if (!demo && !user) return <Navigate to="/login" replace />;
  if (demo && !loading && user) return <Navigate to="/app" replace />;
  if (demo) {
    return (
      <div className="auth-page entry-auth">
        <div className="auth-card">
          <EntryBrand />
          <h1 className="auth-title">
            {error ? "Demo unavailable" : slowStart ? "Waking up the server" : "Setting up demo"}
          </h1>
          <p className={error ? "auth-error" : "auth-confirm"} role={error ? "alert" : "status"}>
            {error ||
              (slowStart
                ? "The server sleeps when idle and can take up to a minute to wake. Hang tight — your demo will start automatically."
                : loading ? "Connecting to your workspace…" : "Creating a temporary account with sample data…")}
          </p>
          <WalkthroughLink />
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
