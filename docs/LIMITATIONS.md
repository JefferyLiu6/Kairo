# Known Limitations

- CSRF tokens work across workers and restarts using a per-session signature.
  Sessions and rate limits still use SQLite: workers must share the same durable
  `DATA_DIR`; independent instances need a shared database.
- Account verification, password recovery and authenticator MFA are implemented.
  Configure account email (Brevo over HTTPS, or SMTP) and the persistent MFA encryption key before real-user rollout;
  see [account security setup](AUTHENTICATION.md).
- A self-service device/session list is not implemented; password recovery revokes
  all sessions and MFA changes revoke other sessions.
- The credit system is simple cost control: a registered account's credits
  refill each UTC day, and there is no billing, top-up, or admin grant flow yet.
- Deleting an account removes it from the live data, not from backups of the
  server made before the deletion.
- SQLite files are not encrypted at rest by this project.
- Demo-account cleanup is opportunistic, not a dedicated scheduled job.
- Google Calendar sync is limited; full two-way conflict resolution is not implemented.
- Replies return whole; the agent graph does not stream token by token.
- A message with several requests is split by the model, and a request that
  was left out is looked for afterwards, but neither is certain: in testing a
  second request was still sometimes dropped without a word.
- Several changes are approved or rejected together; there is no choosing
  among the items of a batch.
- A suggested event the user answers with another time ("make it 4pm instead")
  leaves the original suggestion pending.
- The wording checks read English date, time and repeat words, and the check
  on a task or event picked by id uses a fixed list of English words. In other
  languages a proposal is not cross-checked, and a non-English event title has
  lost a name in testing.
- Not built yet: forgetting a remembered fact, changing a task's due date, and
  completing a task from the task list in the web app.
- The blind eval uses simulated personas. Real users' messages are the test
  that has not been run.
- The typed workflow that came before the graph is still in the repo. It
  serves `/personal-manager/chat` and `/personal-manager/stream`, which the web
  app does not call; the offline 252-case suite covers it, not the graph.
- The schedule tools reuse the existing Google Calendar references, but the
  graph has not been run against a connected Google account.
- The learning loop is narrow: mostly time-window preferences on the previous
  path, not broad behavioral modeling.
