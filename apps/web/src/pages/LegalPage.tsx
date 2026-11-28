import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { EntryBrand } from "./EntryBrand";
import { CONTACT_EMAIL, POLICIES_UPDATED } from "../lib/site";

function LegalPage({ title, children }: { title: string; children: ReactNode }) {
  return <div className="entry-home">
    <header className="entry-nav"><EntryBrand /><nav aria-label="Account">
      <Link to="/login">Sign in</Link><Link to="/signup">Create account</Link>
    </nav></header>
    <main className="entry-content entry-legal">
      <h1>{title}</h1>
      <p className="entry-legal-date">Last updated {POLICIES_UPDATED}</p>
      {children}
    </main>
    <LegalFooter />
  </div>;
}

/** The footer of the public pages. `children` are links shown before the policy links. */
export function LegalFooter({ children }: { children?: ReactNode }) {
  return <footer className="entry-foot"><span>© {new Date().getFullYear()} Kairo</span>
    <nav aria-label="Footer">{children}<Link to="/privacy">Privacy</Link><Link to="/terms">Terms</Link></nav>
  </footer>;
}

const contact = <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>;

export function PrivacyPage() {
  return <LegalPage title="Privacy policy">
    <p>Kairo is a planning assistant run as an independent project. This page says what it stores
      about you, who else receives it, and how to take it with you or delete it.</p>

    <h2>What Kairo stores</h2>
    <ul>
      <li><strong>Your account.</strong> Your email address, display name and a hash of your password,
        never the password itself. If you turn on an authenticator, its secret is stored encrypted.</li>
      <li><strong>What you put in.</strong> Your schedule, tasks, habits, journal entries and notes, and
        the messages you exchange with Kairo.</li>
      <li><strong>What Kairo keeps to do its job.</strong> Facts and preferences it remembers about you,
        the changes it proposed and whether you approved them, and a record of what it did.</li>
      <li><strong>A connected calendar.</strong> If you connect Google Calendar, Kairo stores encrypted
        access tokens and a copy of your events.</li>
      <li><strong>Sign-in and abuse limits.</strong> Your sign-in sessions, and counters that limit
        how often an account or network address can make requests. The counters hold a hash of the
        address, not the address.</li>
    </ul>
    <p>Kairo sets one cookie, which keeps you signed in. It has no advertising and no analytics tracking.</p>

    <h2>Who else receives your data</h2>
    <p>Kairo sends your data to other services only to do what you ask of it.</p>
    <ul>
      <li><strong>An AI model provider (OpenAI or Anthropic).</strong> Your message, and the parts of
        your schedule, tasks and notes needed to answer it, are sent to produce a reply.</li>
      <li><strong>Google,</strong> if you connect Google Calendar: events are read from your calendar,
        and changes you make or approve are written to it.</li>
      <li><strong>A web search provider,</strong> when answering needs a search: the search words are
        sent. Kairo asks you first when a search looks like it contains private details.</li>
      <li><strong>Brevo,</strong> which delivers account email: your address and the message, such as a
        verification link or a security notice.</li>
      <li><strong>Vercel and Oracle Cloud,</strong> which host the web app and the server.</li>
      <li><strong>Your browser’s speech service,</strong> if you use voice input. Your browser, not
        Kairo, turns speech into text and may send the audio to its maker.</li>
    </ul>
    <p>Your data is not sold, and Kairo does not use it to train models.</p>

    <h2>How it is protected</h2>
    <p>Your data is kept on one server. Passwords are hashed, sign-in sessions are stored as hashes,
      and authenticator secrets and calendar tokens are encrypted. Your other records are stored on
      the server’s disk without separate encryption. Connections to Kairo use HTTPS.</p>

    <h2>How long it is kept</h2>
    <p>Your data is kept until you delete it or your account. A temporary demo account expires after
      24 hours and is deleted afterwards. A backup of the server made before a deletion can still
      hold a copy for a limited time; backups are used only to recover from a failure.</p>

    <h2>Your choices</h2>
    <ul>
      <li><strong>Take a copy.</strong> In the app, open Account security and choose Download my data.</li>
      <li><strong>Delete everything.</strong> In Account security, choose Delete my account. Your
        account and everything stored in it are deleted, and Kairo’s access to a connected calendar
        is ended.</li>
      <li><strong>Disconnect your calendar</strong> at any time from the schedule panel. Kairo asks
        Google to end its access and deletes its copy of your events.</li>
      <li><strong>Ask.</strong> For anything else about your data, write to {contact}.</li>
    </ul>

    <h2>Children</h2>
    <p>Kairo is not meant for children under 13, and does not knowingly keep their data.</p>

    <h2>Changes to this page</h2>
    <p>This page is updated when Kairo changes how it handles your data, and the date at the top
      changes with it.</p>
  </LegalPage>;
}

export function TermsPage() {
  return <LegalPage title="Terms of use">
    <p>These are the terms for using Kairo. By creating an account or starting a demo, you agree to them.</p>

    <h2>What Kairo is</h2>
    <p>Kairo is a planning assistant run as an independent project. It is offered as it is, may
      change, and may be unavailable or discontinued.</p>

    <h2>Your account</h2>
    <p>You need to be at least 13 to use Kairo. Give an email address you can read, keep your password
      to yourself, and use an account only for yourself. You are responsible for what is done from
      your account.</p>

    <h2>Your content</h2>
    <p>What you put into Kairo stays yours. You let Kairo store and process it only to provide the
      service to you, which includes sending it to the services named in
      the <Link to="/privacy">privacy policy</Link>.</p>

    <h2>Fair use</h2>
    <p>Do not use Kairo to break the law, to try to get at other people’s data, or to overload or
      disrupt the service. Accounts have a daily limit on messages.</p>

    <h2>The assistant can be wrong</h2>
    <p>Kairo uses AI models. It can misread a request, leave part of one out, or get a date or a
      detail wrong. Read what it proposes before you approve it, and check what it tells you. Do not
      rely on it for medical, legal or financial decisions, or as your only record of something
      that must not be missed.</p>

    <h2>No guarantee</h2>
    <p>Kairo comes without warranties of any kind. It may lose data or be unavailable. Download a
      copy of anything you cannot afford to lose. As far as the law allows, the operator of Kairo is
      not liable for losses that come from using it or from being unable to use it.</p>

    <h2>Ending</h2>
    <p>You can delete your account at any time from Account security. An account that breaks these
      terms may be suspended or deleted.</p>

    <h2>Changes and contact</h2>
    <p>When these terms change, this page and the date at the top change with them. Questions go
      to {contact}.</p>
  </LegalPage>;
}
