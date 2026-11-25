# Deploy the backend on Oracle Cloud (free, with durable storage)

Render's free web service wipes its disk whenever it sleeps, restarts or
redeploys, so signed-in users lose their accounts and data. An Oracle Cloud
Always Free VM keeps its disk, never sleeps and costs nothing. The frontend stays
on Vercel; only the backend moves.

What runs on the VM (all from this repo):

- `backend`: the same FastAPI image as before (`Dockerfile.agent-python`), with
  user data in the Docker volumes `kairo_pm_data` and `kairo_pm_vault`.
- `caddy`: HTTPS in front of the backend. It gets a free certificate for
  `<your-ip-with-dashes>.sslip.io`, a hostname that points at your VM's IP, so
  you don't need to buy a domain.

Files: [`deploy/oracle/`](../deploy/oracle/) (`compose.yml`, `Caddyfile`,
`setup.sh`, `deploy.sh`, and `dc.sh`, which runs `docker compose` with the
right files).

## 1. Create the VM

1. Sign up at <https://www.oracle.com/cloud/free/>. Pick your home region
   carefully; Always Free resources only live there. Oracle asks for a card to
   verify you; Always Free resources are not charged.
2. **Compute → Instances → Create instance**.
   - **Image**: the default Oracle Linux 9 or Canonical Ubuntu 24.04; `setup.sh`
     handles both. You log in as `opc` on Oracle Linux and as `ubuntu` on Ubuntu.
   - **Shape**: Ampere `VM.Standard.A1.Flex`, 2 OCPUs and 12 GB memory (the free
     allowance is 4 OCPUs and 24 GB in total).
   - **Networking**: keep the default new VCN and public subnet, with
     **Assign a public IPv4 address** on.
   - **SSH keys**: upload your public key (for example `~/.ssh/id_ed25519.pub`),
     or let Oracle generate a pair and download the private key.
   - If you get **Out of capacity**, try another availability domain, or try
     again later; free Ampere capacity comes and goes.
3. Note the instance's **public IP**. It stays the same unless you delete the VM.

## 2. Open ports 80 and 443

**Networking → Virtual cloud networks →** your VCN **→ Security Lists → Default
Security List → Add Ingress Rules**:

| Source CIDR | IP protocol | Destination port |
| ----------- | ----------- | ---------------- |
| `0.0.0.0/0` | TCP | `80,443` |

(The VM also has its own firewall, firewalld on Oracle Linux and iptables on
Ubuntu; `setup.sh` opens the same ports there.)

## 3. Install and configure

If Oracle generated the SSH key, keep it private first:
`chmod 600 <the downloaded .key file>`, and add `-i <that file>` to every `ssh`
below. Use `opc@` on Oracle Linux and `ubuntu@` on Ubuntu.

From your own machine, in a clone of this repo:

```bash
ssh opc@<PUBLIC_IP> 'bash -s <PUBLIC_IP>' < deploy/oracle/setup.sh
```

`setup.sh` installs Docker and git, opens the firewall, clones the repo to
`~/kairo`, and creates
`~/kairo/.env.production` with `API_DOMAIN` and `PUBLIC_URL` already set to
`https://<ip-with-dashes>.sslip.io`.

Fill in the rest of `~/kairo/.env.production` (`nano ~/kairo/.env.production`)
by copying each value from the Render backend's **Environment** page
(`ssh opc@<PUBLIC_IP>` first). In
particular:

| Variable | Value |
| -------- | ----- |
| `AUTH_ENCRYPTION_KEY`, `SESSION_SECRET` | Copy Render's values exactly |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, `MODEL`, `PM_MODEL` | Copy from Render |
| `AUTH_EMAIL_PROVIDER`, `AUTH_EMAIL_FROM`, `BREVO_API_KEY` | Copy from Render |
| `CORS_ORIGINS`, `AUTH_PUBLIC_URL` | `https://kairo-mu-two.vercel.app` |
| `COOKIE_SECURE` / `COOKIE_SAMESITE` / `TRUST_PROXY_HEADERS` | `true` / `lax` / `true` |
| `PUBLIC_URL`, `API_DOMAIN` | Leave as `setup.sh` wrote them |
| `GOOGLE_CALENDAR_REDIRECT_URI` | Only if you use Google Calendar: leave it empty (it becomes `PUBLIC_URL` + `/personal-manager/google-calendar/callback`) and add that URL to the OAuth client in Google Cloud Console |

## 4. Start it

```bash
ssh opc@<PUBLIC_IP>
cd ~/kairo && bash deploy/oracle/deploy.sh
```

The first build takes a few minutes. Then open
`https://<ip-with-dashes>.sslip.io/health`; it should show `{"status":"ok"}`.

## 5. Point Vercel at it

In [`apps/web/vercel.json`](../apps/web/vercel.json), change the `/api` rewrite's
destination from the Render URL to the new one:

```json
"destination": "https://<ip-with-dashes>.sslip.io/:path*"
```

Commit and push; Vercel redeploys on its own. Then check durability: sign up,
add an event, run `bash ~/kairo/deploy/oracle/dc.sh restart backend` on the
VM (or `deploy.sh` again), and sign back in. Your account and event should still be
there.

Once that works, suspend the Render service (Render dashboard → the service →
**Settings → Suspend**).

## Day to day

- **Deploy new code**: `cd ~/kairo && bash deploy/oracle/deploy.sh`. Data is kept.
- **Logs**: `bash ~/kairo/deploy/oracle/dc.sh logs -f backend`
- **Back up**: in the Oracle console, open the instance's **Boot volume** and
  create a backup (Always Free includes 5 volume backups). For a file copy of
  just the data:

  ```bash
  cd ~/kairo && bash deploy/oracle/dc.sh stop backend
  docker run --rm -v kairo_pm_data:/data -v "$PWD":/out alpine \
    tar czf /out/kairo-data-$(date +%F).tgz -C /data .
  bash deploy/oracle/dc.sh start backend
  ```

- **Never** run `dc.sh down -v` or `docker volume rm` on the VM; that
  deletes every user's data.

## Things to know

- **Idle reclaim**: Oracle may reclaim an Always Free VM that stays almost
  completely idle for 7 days (very low CPU, network and memory use). Upgrading
  the account to Pay As You Go keeps Always Free resources free and stops
  reclaiming; set a budget alert at $1 if you do.
- **One server**: the backend runs as a single instance with SQLite, which is
  the design; there is a few seconds of downtime while `deploy.sh` restarts it.
- **The IP is the address**: if you delete and recreate the VM, its IP and so
  its `sslip.io` hostname change; update `API_DOMAIN`, `PUBLIC_URL` and
  `vercel.json` to match.
