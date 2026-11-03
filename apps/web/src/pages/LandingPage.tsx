import React, { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

/* ─── Data ────────────────────────────────────────────────────────────────── */

const FEATURES = [
  {
    icon: "calendar",
    color: "#14b8a6",
    title: "Smart Scheduling",
    desc: "Auto-schedules tasks around your existing commitments based on energy and priority.",
  },
  {
    icon: "shield",
    color: "#8b5cf6",
    title: "Focus Protection",
    desc: "Automatically blocks deep-work windows and guards them against meeting creep.",
  },
  {
    icon: "sparkle",
    color: "#f59e0b",
    title: "AI Insights",
    desc: "Spots overload patterns and proactively rebalances before your week goes sideways.",
  },
  {
    icon: "chat",
    color: "#14b8a6",
    title: "Natural Language",
    desc: "Just tell Kairo what you need. No forms, no drag-and-drop, no friction.",
  },
  {
    icon: "sync",
    color: "#3b82f6",
    title: "Calendar Sync",
    desc: "Two-way sync with Google Calendar keeps your real calendar always up to date.",
  },
  {
    icon: "eye",
    color: "#ec4899",
    title: "Decision Trace",
    desc: "Every AI action is logged with reasoning so you stay in control at all times.",
  },
];

const STEPS = [
  {
    n: "1",
    variant: "teal" as const,
    title: "Connect your calendar",
    desc: "Link Google Calendar in one click. Kairo reads your existing schedule and commitments.",
  },
  {
    n: "2",
    variant: "violet" as const,
    title: "Share your priorities",
    desc: "Tell Kairo what matters this week — projects, deadlines, personal commitments.",
  },
  {
    n: "3",
    variant: "amber" as const,
    title: "Kairo handles the rest",
    desc: "Your week is planned, protected, and optimized. You just show up and execute.",
  },
];

const REVIEWS = [
  { quote: "I haven't manually scheduled a meeting in three weeks. Kairo just handles it.", name: "Alex M.", role: "Product Lead", color: "#14b8a6", initial: "A" },
  { quote: "The focus block protection is worth it alone. I finally have uninterrupted deep work.", name: "Sarah K.", role: "Senior Engineer", color: "#8b5cf6", initial: "S" },
  { quote: "It found 12 extra hours in my week that I didn't know I had.", name: "James O.", role: "Founder & CEO", color: "#f97316", initial: "J" },
  { quote: "Feels like having a great EA who never sleeps and knows your calendar perfectly.", name: "Mia R.", role: "VP of Operations", color: "#3b82f6", initial: "M" },
  { quote: "Kairo moved a sync when I was running behind — I didn't have to do a thing.", name: "Daniel L.", role: "Engineering Manager", color: "#ef4444", initial: "D" },
  { quote: "The decision trace makes me trust it. I can see exactly why it made every change.", name: "Priya N.", role: "Staff PM", color: "#14b8a6", initial: "P" },
];

const AVATARS = [
  { l: "A", c: "#14b8a6" }, { l: "M", c: "#f97316" }, { l: "J", c: "#3b82f6" },
  { l: "S", c: "#8b5cf6" }, { l: "R", c: "#ef4444" },
];

/* ─── Tiny SVG icons ─────────────────────────────────────────────────────── */

function Icon({ name }: { name: string }) {
  const icons: Record<string, React.ReactElement> = {
    calendar: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>
      </svg>
    ),
    shield: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
      </svg>
    ),
    sparkle: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2l2.4 7.4H22l-6.2 4.6 2.4 7.4L12 17l-6.2 4.4 2.4-7.4L2 9.4h7.6L12 2z"/>
      </svg>
    ),
    chat: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
      </svg>
    ),
    sync: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="1 4 1 10 7 10"/><polyline points="23 20 23 14 17 14"/>
        <path d="M20.49 9A9 9 0 005.64 5.64L1 10M23 14l-4.64 4.36A9 9 0 013.51 15"/>
      </svg>
    ),
    eye: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>
      </svg>
    ),
  };
  return icons[name] ?? null;
}

/* ─── Calendar mock data ─────────────────────────────────────────────────── */

const CAL_ROWS = [
  {
    time: "9 AM",
    cells: [
      { label: "Team standup", cls: "lp-ev-teal" },
      null,
      { label: "Design review", cls: "lp-ev-purple" },
      null,
      { label: "Deep work", cls: "lp-ev-outline" },
    ],
  },
  {
    time: "11 AM",
    cells: [
      { label: "Product review", cls: "lp-ev-amber" },
      { label: "1:1 Alex", cls: "lp-ev-teal" },
      null,
      { label: "Investor call", cls: "lp-ev-red" },
      null,
    ],
  },
  {
    time: "1 PM",
    cells: [null, null, { label: "Team lunch", cls: "lp-ev-teal" }, null, null],
  },
];

/* ─── Component ──────────────────────────────────────────────────────────── */

export function LandingPage() {
  const navigate = useNavigate();
  const { user, loading } = useAuth();
  const [email, setEmail] = useState("");
  const emailRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!loading && user) navigate("/app", { replace: true });
  }, [user, loading, navigate]);

  const goApp = () => navigate("/app");

  const handleEmailSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    navigate("/app");
  };

  return (
    <div className="lp">
      {/* ── Nav ── */}
      <nav className="lp-nav">
        <div className="lp-logo">
          <div className="lp-logo-mark">
            <img src="/kairo-logo.svg" alt="" width={20} height={20} />
          </div>
          <span className="lp-logo-word">Kairo</span>
        </div>
        <div className="lp-nav-links">
          <a href="#features">Features</a>
          <a href="#how-it-works">How it works</a>
          <a href="#reviews">Reviews</a>
        </div>
        <button className="lp-nav-cta" onClick={goApp}>
          Get early access
        </button>
      </nav>

      {/* ── Hero ── */}
      <section className="lp-hero">
        {/* Background orbs */}
        <div className="lp-orb lp-orb-teal" />
        <div className="lp-orb lp-orb-amber" />
        <div className="lp-orb lp-orb-violet" />

        <div className="lp-hero-inner">
          <div className="lp-badge lp-anim">
            <span className="lp-badge-dot" />
            Now in early access
          </div>

          <h1 className="lp-h1 lp-anim lp-anim-1">
            Your calendar,
            <br />
            <span className="lp-h1-gradient">finally thinking</span>
            <br />
            for itself
          </h1>

          <p className="lp-sub lp-anim lp-anim-2">
            Kairo is a personal AI manager that schedules your week,
            <br />
            protects your focus time, and keeps your life in balance —
            <br />
            without you lifting a finger.
          </p>

          <div className="lp-cta-row lp-anim lp-anim-3">
            <button className="lp-btn-primary" onClick={goApp}>
              Get early access — it's free
            </button>
            <a href="#how-it-works" className="lp-btn-ghost">
              See how it works ↓
            </a>
          </div>

          <div className="lp-trust lp-anim lp-anim-4">
            <div className="lp-avatars">
              {AVATARS.map((a) => (
                <div key={a.l} className="lp-avatar" style={{ background: a.c }}>
                  {a.l}
                </div>
              ))}
            </div>
            <span>
              Joined by <strong>2,400+</strong> professionals this week
            </span>
          </div>
        </div>

        {/* ── App preview window ── */}
        <div className="lp-preview lp-anim lp-anim-5">
          {/* Browser chrome */}
          <div className="lp-chrome">
            <span className="lp-dot" style={{ background: "#ff5f57" }} />
            <span className="lp-dot" style={{ background: "#ffbd2e" }} />
            <span className="lp-dot" style={{ background: "#28c940" }} />
            <div className="lp-url-bar" />
          </div>

          {/* Two-column body */}
          <div className="lp-preview-body">
            {/* Sidebar */}
            <div className="lp-sidebar">
              <div className="lp-tasks-label">TASKS</div>
              {[
                { text: "Review Q2 roadmap", accent: "#14b8a6" },
                { text: "Prepare investor deck", accent: "#f59e0b" },
                { text: "Update documentation", accent: "#3b82f6" },
              ].map((t) => (
                <div key={t.text} className="lp-task" style={{ borderLeftColor: t.accent }}>
                  {t.text}
                </div>
              ))}
            </div>

            {/* Calendar grid */}
            <div className="lp-cal">
              <div className="lp-cal-head">
                <div className="lp-cal-gutter" />
                {["MON 19", "TUE 20", "WED 21", "THU 22", "FRI 23"].map((d) => (
                  <div key={d} className="lp-cal-day">{d}</div>
                ))}
              </div>
              {CAL_ROWS.map((row) => (
                <div key={row.time} className="lp-cal-row">
                  <div className="lp-cal-time">{row.time}</div>
                  {row.cells.map((cell, i) =>
                    cell ? (
                      <div key={i} className={`lp-cal-event ${cell.cls}`}>
                        {cell.label}
                      </div>
                    ) : (
                      <div key={i} className="lp-cal-empty" />
                    )
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* AI insight bar */}
          <div className="lp-ai-bar">
            <div className="lp-ai-icon">⚡</div>
            <em>Thursday is your heaviest day — 5h of meetings. I've moved Design sync to Friday 10am.</em>
          </div>

          {/* Floating chat bubble */}
          <div className="lp-bubble">
            <p>Block 3:30–5pm for investor call prep?</p>
            <div className="lp-bubble-actions">
              <button className="lp-bubble-yes" onClick={goApp}>Yes, do it</button>
              <button className="lp-bubble-no">Not now</button>
            </div>
          </div>
        </div>
      </section>

      {/* ── Social proof strip ── */}
      <div className="lp-strip">
        {[
          { val: "2.4k+", label: "professionals using Kairo" },
          { val: "12h", label: "avg saved per week" },
          { val: "4.9★", label: "early access rating" },
        ].map((s) => (
          <div key={s.val} className="lp-strip-item">
            <strong>{s.val}</strong>
            <span>{s.label}</span>
          </div>
        ))}
        <div className="lp-strip-item lp-strip-gcal">
          <div className="lp-gcal-dots">
            <span style={{ background: "#4285F4" }} />
            <span style={{ background: "#EA4335" }} />
            <span style={{ background: "#FBBC04" }} />
            <span style={{ background: "#34A853" }} />
          </div>
          <span>Works with Google Calendar</span>
        </div>
      </div>

      {/* ── Features ── */}
      <section className="lp-features" id="features">
        <div className="lp-orb lp-orb-features-teal" />
        <div className="lp-orb lp-orb-features-indigo" />
        <div className="lp-section-label">Features</div>
        <h2 className="lp-section-h2">
          Everything you need to reclaim<br />your week
        </h2>
        <div className="lp-features-grid">
          {FEATURES.map((f) => (
            <div key={f.title} className="lp-feature-card">
              <div className="lp-feature-icon" style={{ background: `${f.color}22`, color: f.color }}>
                <Icon name={f.icon} />
              </div>
              <div className="lp-feature-title">{f.title}</div>
              <div className="lp-feature-desc">{f.desc}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── How it works ── */}
      <section className="lp-hiw" id="how-it-works">
        <div className="lp-section-label lp-section-label-dark">How it works</div>
        <h2 className="lp-section-h2 lp-section-h2-dark">
          Up and running in minutes
        </h2>
        <div className="lp-steps">
          <div className="lp-steps-line" />
          {STEPS.map((s) => (
            <div key={s.n} className="lp-step">
              <div className={`lp-step-circle lp-step-${s.variant}`}>{s.n}</div>
              <div className="lp-step-title">{s.title}</div>
              <div className="lp-step-desc">{s.desc}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── Testimonials ── */}
      <section className="lp-reviews" id="reviews">
        <div className="lp-section-label">Reviews</div>
        <h2 className="lp-section-h2 lp-section-h2-dark">
          Loved by professionals
        </h2>
        <div className="lp-reviews-grid">
          {REVIEWS.map((r) => (
            <div key={r.name} className="lp-review-card">
              <div className="lp-stars">{"★".repeat(5)}</div>
              <p className="lp-review-quote">"{r.quote}"</p>
              <div className="lp-review-author">
                <div className="lp-review-avatar" style={{ background: r.color }}>
                  {r.initial}
                </div>
                <div>
                  <div className="lp-review-name">{r.name}</div>
                  <div className="lp-review-role">{r.role}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── CTA section ── */}
      <section className="lp-cta-section">
        <div className="lp-cta-orb" />
        <h2 className="lp-cta-h2">
          Your best week starts<br />
          <span className="lp-h1-gradient">today.</span>
        </h2>
        <p className="lp-cta-sub">
          Join 2,400+ professionals already using Kairo. No credit card required.
        </p>
        <form className="lp-email-row" onSubmit={handleEmailSubmit}>
          <input
            ref={emailRef}
            type="email"
            className="lp-email-input"
            placeholder="Enter your email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <button type="submit" className="lp-email-submit">
            Get early access
          </button>
        </form>
      </section>

      {/* ── Footer ── */}
      <footer className="lp-footer">
        <div className="lp-footer-logo">
          <div className="lp-logo-mark lp-logo-mark-sm">
            <img src="/kairo-logo.svg" alt="" width={16} height={16} />
          </div>
          <span className="lp-logo-word" style={{ fontSize: 15 }}>Kairo</span>
        </div>
        <div className="lp-footer-links">
          <a href="#features">Features</a>
          <a href="#how-it-works">How it works</a>
          <a href="#reviews">Reviews</a>
        </div>
        <div className="lp-footer-copy">© 2025 Kairo. All rights reserved.</div>
      </footer>
    </div>
  );
}
