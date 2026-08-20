# Runbook

Operational. **For us, not the client.** Never hand it over; `CLIENT_GUIDE.md`
is his.

It still does not name the carrier. Not because this file is client-facing, but
because the habit is the control: the leaks that have actually happened here
were assembled from a variable somebody copied out of a doc into an f-string.
The credential variable names live in `.env.example`, which is where they
belong.

Every command assumes:

```bash
APP_DIR=/home/appuser/app       # WorkingDirectory in the systemd unit
SERVICE=a4a-sms                 # whatever --name bootstrap.sh was given
```

The single most useful command in this file is the first one under **Stop a
campaign mid-send**. Read that section before you need it.

---

## Deploy

From your laptop, not the server. The stylesheet and fonts are built locally and
shipped as files; the box has no node toolchain at runtime.

```bash
./deployment/deploy.sh --dry-run                          # read it first
SERVER=appuser@<host> SERVICE=$SERVICE ./deployment/deploy.sh
```

It refuses a dirty working tree, builds `app.css` **and** syncs the fonts, backs
the database up, migrates, restarts, then health-checks `/health` and rolls the
code back if that fails. It exits non-zero on every one of those failures.

If it aborts at the migration, nothing was restarted: the old code is still
running against the old schema. Fix the migration and re-run.

---

## Roll back a bad deploy

**The script already tried.** A failed health check restores the previous code
and restarts before it exits, leaving the broken build at `$APP_DIR/app.broken`.
If it printed "Rolled back", the app is serving the previous code and you have
time to think.

To roll back a deploy that *passed* its health check and is wrong anyway:

```bash
git checkout <last-good-sha>
SERVER=appuser@<host> SERVICE=$SERVICE ./deployment/deploy.sh
git checkout -                                            # don't forget this
```

**Migrations are the exception.** A code rollback does not undo one. Check
whether the migration you are rolling past has a working `downgrade`:

```bash
ssh appuser@<host> "cd $APP_DIR && ./venv/bin/alembic history -r-3:current"
ssh appuser@<host> "cd $APP_DIR && ./venv/bin/alembic downgrade -1"   # only if it does
```

If it does not, restore the pre-migrate backup instead — `deploy.sh` takes one
immediately before every migration, so it is minutes old.

---

## Restore a backup

```bash
ssh appuser@<host>
cd $APP_DIR
ls -lt backups/ | head

sudo systemctl stop $SERVICE
cp data/app.db data/app.db.before-restore                 # keep the bad one
gunzip -c backups/app-YYYYmmdd-HHMMSS.db.gz > data/app.db
./scripts/backup.sh --verify-only                         # confirm before restarting
sudo systemctl start $SERVICE
curl -sf http://127.0.0.1:8000/health
```

Keep the database you are replacing. Restoring the wrong archive is recoverable;
restoring over the only copy is not.

Backups are taken with SQLite's online backup API rather than `cp`, so an
archive taken mid-campaign is consistent. Cron runs `--verify` nightly, which
restores the newest archive and runs `PRAGMA integrity_check` plus a table
check — `integrity_check` alone passes on an empty file.

---

## Stop a campaign mid-send

```bash
sudo systemctl stop $SERVICE
```

That is the whole procedure. Sending runs in-process as a background task, so
stopping the service stops the send immediately. There is no queue to drain and
nothing resumes on its own.

Then work out what happened before restarting:

```bash
sudo journalctl -u $SERVICE -n 200 --no-pager
sqlite3 $APP_DIR/data/app.db \
  "SELECT status, COUNT(*) FROM sms_messages WHERE campaign_id=<id> GROUP BY status;"
```

A stopped campaign does **not** resume when the service starts. Messages left
`pending` stay pending. If you want the rest to go out, that is a decision —
check what was already delivered first, because the alternative to a partial
send is a double send.

Restart when you are ready:

```bash
sudo systemctl start $SERVICE
```

---

## Rotate the carrier credential

Do this on a quiet hour, not before a blast.

```bash
ssh appuser@<host>
cd $APP_DIR
cp .env .env.bak-$(date +%Y%m%d)          # mode 600, delete it when you are done
nano .env                                  # replace the carrier API key
                                           # (variable name is in .env.example)
sudo systemctl restart $SERVICE

# Confirm the new key actually works BEFORE the next campaign.
curl -sf http://127.0.0.1:8000/health
# then, signed in: Settings → System should read Live with the right number,
# and the composer's "Send test" should reach your own handset.
```

If the key is wrong the app falls back to the console provider and logs it —
which means the UI looks fine and nothing sends. Check the pill in the top bar:
it reads **Dry run** whenever the provider is console, for exactly this reason.

Rotating `agent/notify.sh`'s credential is separate and deliberately so — it is
what pages you when the sending account is the thing that is broken.

---

## Read the logs

```bash
sudo journalctl -u $SERVICE -f                    # live
sudo journalctl -u $SERVICE --since "1 hour ago" --no-pager
sudo journalctl -u $SERVICE -p err --since today --no-pager

tail -f $APP_DIR/logs/app.log                     # the app's own file handler
tail -50 $APP_DIR/logs/backup.log                 # nightly backup + verify
sudo tail -f /var/log/nginx/error.log
```

Worth grepping for, in order of how bad they are:

| Pattern | Means |
|---|---|
| `pre-flight FAILED` | A campaign refused to start because the balance could not fund it. Working as designed. Top up. |
| `Account inactive` | The balance hit zero **during** a send. Every message after it failed. |
| `Database has tables but no Alembic version` | Pre-Alembic database. See README; stamp once, by hand. |
| `Could not initialize SMS provider` | Bad credential — the app fell back to console and is sending nothing. |
| `SMS provider is 'console'` | Dry run. Expected before go-live, an emergency after. |
| `low-balance check` | The hourly monitoring job. `alerted=True` means someone was paged. |

---

## Check the certificate

The prior client's certificate sat 45 days from expiry with nobody watching.

```bash
sudo certbot certificates                         # expiry dates
sudo systemctl is-active certbot.timer            # must print: active
sudo systemctl list-timers certbot.timer          # when it next runs
sudo certbot renew --dry-run                      # proves renewal actually works
```

`is-active` is the one that matters. A certificate with 60 days left and a dead
timer is a site that goes down in 60 days.

To renew by hand:

```bash
sudo certbot renew && sudo systemctl reload nginx
```

---

## The disk is full

SQLite fails writes on a full disk, which means imports and sends fail while
every page still loads. Symptom: `database or disk is full` in the log.

```bash
df -h /
du -sh $APP_DIR/* | sort -h | tail
```

Usual suspects, in the order they are usually guilty:

```bash
# 1. Journal logs, which grow without limit by default.
sudo journalctl --disk-usage
sudo journalctl --vacuum-time=14d

# 2. Backups. 30-day local retention, but a large database adds up.
ls -lt $APP_DIR/backups/ | head
find $APP_DIR/backups -name '*.db.gz' -mtime +30 -delete

# 3. A failed deploy's leftovers.
ls -d $APP_DIR/app.broken $APP_DIR/app.prev 2>/dev/null

# 4. Exports.
find $APP_DIR/exports -type f -mtime +7 -delete
```

Do **not** delete `$APP_DIR/data/app.db*` to make room. The `-wal` and `-shm`
files next to the database are part of it.

Once there is room, confirm the database is intact before trusting it:

```bash
sqlite3 $APP_DIR/data/app.db "PRAGMA integrity_check;"
```

---

## Quick health sweep

```bash
sudo systemctl is-active $SERVICE
curl -sf http://127.0.0.1:8000/health
sudo systemctl is-active certbot.timer
tail -3 $APP_DIR/logs/backup.log
df -h / | tail -1
```

Five lines. If all five look right, the box is fine.
