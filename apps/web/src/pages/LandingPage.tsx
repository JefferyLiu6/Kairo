import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { EntryBrand } from "./EntryBrand";

export function LandingPage() {
  const { user, loading } = useAuth();
  if (!loading && user && !user.isDemo) return <Navigate to="/app" replace />;
  return <div className="entry-home">
    <header className="entry-nav"><EntryBrand /><nav aria-label="Account">
      <Link to="/login">Sign in</Link><Link className="entry-action" to="/signup">Create account</Link>
    </nav></header>
    <main className="entry-content">
      <section className="entry-hero">
        <div className="entry-intro">
          <h1>Make room for<br /><em>what matters.</em></h1>
          <p>Plan your day with Kairo. Bring your calendar, tasks, and habits into one conversation.</p>
          <div className="entry-choices"><Link to="/signup" className="entry-action">Create your account</Link>
            <Link to="/demo" className="entry-demo">Try the demo <span aria-hidden="true">↗</span></Link></div>
          <small>The demo opens a temporary workspace with sample data.</small>
        </div>
        <figure className="entry-agenda">
          <figcaption>Example day plan</figcaption>
          <h2>Monday morning</h2>
          <dl>{[["09:00", "Team standup"], ["09:30", "Prepare the project brief"], ["11:00", "Design review"]].map(([time, task]) =>
            <div key={time}><dt>{time}</dt><dd>{task}</dd></div>)}</dl>
          <blockquote>“Find time to prepare my brief before the design review.”</blockquote>
        </figure>
      </section>
      <section className="entry-features" aria-label="What you can do with Kairo">
        <article><h2>Plan around your calendar</h2><p>Find a free window between meetings and review the time Kairo suggests.</p></article>
        <article><h2>Keep tasks in reach</h2><p>Add a task as it comes to mind. Ask what’s still open when you’re ready to start.</p></article>
        <article><h2>Look back on your week</h2><p>Log a habit or write a journal entry, then return to it when you plan your next day.</p></article>
      </section>
    </main>
    <footer className="entry-foot"><span>© {new Date().getFullYear()} Kairo</span><Link to="/login">Sign in to your workspace</Link></footer>
  </div>;
}
