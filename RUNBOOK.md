# RUNBOOK.md

Operational reference for running Domi unattended. Written August 2026, before Lincks left for a
15-month masters in Arizona. If you're reading this because something looks wrong, start with
**"When something looks wrong"** below, not the deploy sections.

---

## What alerts you, and how

As of August 2026, Domi has real alerting wired up. If something breaks, you should hear about it
through one of these — you should not need to be actively watching the app for problems to surface.

| Signal | Channel | What it means |
|---|---|---|
| Unhandled exception in the web app | Email to `MAINTAINER_EMAIL` | A route crashed. Check `/platform/errors` for the traceback. |
| A scheduled job throws an exception | Email to `MAINTAINER_EMAIL` | Immediate — doesn't wait for the heartbeat grace period. |
| A scheduled job stops running entirely (crash, dropped on restart) | Email from healthchecks.io | The "dead-man's-switch" — no ping arrived when one was expected. |
| Weekly system health digest | Email to `MAINTAINER_EMAIL`, Mondays | Payment counts, unassigned transactions, parse errors, admin activity gap. |
| App is down / DB unreachable | Whatever you point an uptime monitor at `/health` with (see 1.5 in the audit — not yet configured as of this writing) | `/health` checks real DB connectivity, not just that the process answers HTTP. |

All of the above land in `lincksmorara@gmail.com`. If that inbox goes unchecked for a while, you
will not hear about problems — there's no second channel (SMS/Telegram) as of this writing.

**healthchecks.io project:** the account is `lincksmorara@gmail.com` on healthchecks.io — 8 checks,
one per scheduled job (`daily_snapshot_job`, `anomaly_check_job`, `morning_briefings_job`,
`maintainer_digest_job`, `weekly_digest_job`, `disbursement_job`, `monthly_checkins_job`,
`process_payment_queue`, `backup_job`). Each check's expected schedule is set to match the actual
UTC cron time (see the CAUTION note in `.agent/jobs.yaml` — the hour= values in `app.py` run as
UTC, not EAT, despite older comments in this codebase claiming otherwise).

---

## When something looks wrong

1. **Check `/health` first**: `curl https://rent-reconciliation.fly.dev/health` — confirms the app
   is up, the DB is reachable, the scheduler is running, and which optional services
   (SMTP, SMS, Daraja, Anthropic) have credentials configured.
2. **If it's down, wait ~15 seconds and check again.** Verified restart times (see below) are ~3-10
   seconds for both a process crash and a full machine reboot — Fly's supervisor handles this
   automatically, no intervention needed for a simple crash/restart.
3. **If it's still down after a minute**, SSH in and check logs:
   ```
   export PATH="$HOME/.fly/bin:$PATH"
   fly logs --no-tail
   ```
4. **Check your email** for anything from healthchecks.io ("is DOWN") or from Domi itself
   (`[Domi] ERROR` or `[Domi] System Health`) — these usually explain what broke before you have
   to go digging.
5. **Check `/platform/errors`** (platform admin login) for recent unhandled exceptions with full
   tracebacks.

---

## Verified recovery behavior (tested August 2026, not assumed)

- **Process crash** (SIGKILL to the gunicorn master): new process spawned, full app re-init
  including all DB migrations completed within **~10 seconds**. Fully automatic.
- **Full machine reboot** (`fly machine restart <id>`): VM relaunch → volume remount → gunicorn
  listening in **~3 seconds**; fully ready (migrations done) by **~10 seconds** total. Fully
  automatic.
- Both were real tests against production, not documentation copied from Fly's marketing — see
  git history around 2026-08-04 for the actual timestamps if you want to re-verify later.

---

## Safe hotfix deploy procedure

**Always back up before deploying.** The app runs on a single Fly machine with no redundancy
(`min_machines_running = 1`) — a bad deploy is recoverable, but you want a fresh DB snapshot in
hand before you find that out the hard way.

```bash
export PATH="$HOME/.fly/bin:$PATH"
cd rent-reconciliation

# 1. Pull latest and make your fix
git pull
# ...edit...

# 2. Verify it imports cleanly before deploying
./venv/bin/python -c "from app import app; print('OK')"

# 3. ALWAYS back up before deploying
./scripts/download_prod_db.sh
# Confirm it actually wrote a file to backups/ before proceeding

# 4. Commit and push (repo lives at github.com/Domi-solutions/rent-reconciliation)
git add <files>
git commit -m "..."
git push origin main

# 5. Deploy
fly deploy

# 6. Verify
curl https://rent-reconciliation.fly.dev/health
fly logs --no-tail | tail -30
```

If `fly deploy` fails partway or the deployed app looks broken, the DB is untouched (deploys only
replace the app code/image, not the persistent volume) — the backup from step 3 is your safety net
if you also need to roll back a bad migration, not a routine requirement for every deploy.

**Also automated separately:** a daily off-server backup runs at 02:00 UTC regardless of whether
anyone deploys anything — see the `backup_job` heartbeat check on healthchecks.io. The manual
backup above is on top of that, specifically because you're about to change something.

---

## Monthly ~20-minute checklist

Run through this once a month. Everything here is checkable without SSH.

1. **Uptime / health** — `curl https://rent-reconciliation.fly.dev/health`, confirm `status: ok`
   and `db: true`.
2. **Scheduled jobs** — open the healthchecks.io dashboard (`lincksmorara@gmail.com` project),
   confirm all 9 checks show green/"up". Any grey ("never pinged") or red ("down") needs
   investigation.
3. **Backup freshness** — confirm a backup from within the last 24-48 hours exists in the
   `domi-backups` R2 bucket (Cloudflare dashboard → R2 → domi-backups → sort by date).
4. **Disk/volume headroom** — `fly volumes list` (or check the Fly dashboard) — the volume is 1GB;
   at the pace observed in August 2026 (~2-3MB DB, ~40MB statements) this has a lot of headroom,
   but re-check the trend isn't accelerating.
5. **Inbox check** — search `lincksmorara@gmail.com` for `[Domi]` and `healthchecks.io` from the
   past month. Anything you haven't already dealt with?
6. **Admin activity gap** — the weekly maintainer digest already flags this if nobody's logged in
   for 14+ days, but worth a manual glance if the digest itself seems to have stopped arriving.

**Not yet checkable via this list** (flagged honestly rather than pretending otherwise — see the
dormancy-readiness audit for full status): reconciliation mismatch detection (payment with no
ledger entry, or vice versa) doesn't exist yet as an automated check; receipt resend and a
landlord-facing "something looks wrong" guide don't exist yet either. These are tracked as open
items, not silently assumed done.

---

## Where the secrets live

All production secrets are Fly secrets (`fly secrets list` for names, values are never printed to
logs or this file). As of August 2026: `ADMIN_PASSWORD`, `SECRET_KEY`, `VIEWER_PASSWORD`,
`CARETAKER_PASSWORD`, `AT_API_KEY`, `AT_USERNAME`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
`TWILIO_FROM_NUMBER` (unused in code as of this writing — candidate for removal),
`APP_BASE_URL`, `DARAJA_CALLBACK_URL`, `DARAJA_CONSUMER_KEY`, `DARAJA_CONSUMER_SECRET`,
`DARAJA_ENV`, `DARAJA_PASSKEY`, `DARAJA_SHORTCODE`, `PLATFORM_ADMIN_PASSWORD`,
`MAINTAINER_EMAIL`, `SMTP_FROM/HOST/PASSWORD/PORT/USER`, `R2_ACCESS_KEY_ID/BUCKET_NAME/ENDPOINT_URL/SECRET_ACCESS_KEY`,
`ANTHROPIC_API_KEY`. Full descriptions in `.agent/env.yaml`.

**Not yet done as of this writing:** these secrets exist only in Fly's secret store and the local
laptop's `.env` — there is no copy in a password manager accessible from the US. If the laptop is
unavailable, secrets would need to be regenerated (new SMTP app password, new R2 keys, etc.)
rather than looked up. Flagged as an open item.

**External accounts involved:** Fly.io (hosting), Cloudflare (R2 backup storage), healthchecks.io
(job monitoring), Gmail (SMTP relay + MAINTAINER_EMAIL), GitHub (`Domi-solutions/rent-reconciliation`
— note this is an org, not the personal `LincksMorara` account; confirm who else has access),
Safaricom Daraja (M-Pesa — account ownership/renewal not yet documented, open item).
