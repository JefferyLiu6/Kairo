/** A registered account's credits come back at the start of each UTC day. */
export function nextCreditRefill(now: Date = new Date()): Date {
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
}

/** What to tell someone who has no credits left. A demo account is never refilled. */
export function outOfCreditsMessage(isDemo: boolean, now: Date = new Date()): string {
  if (isDemo) return "You're out of credits. Sign up with a new account or try another demo later.";
  const time = nextCreditRefill(now).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  return `You're out of credits for today. They refill at ${time}.`;
}
