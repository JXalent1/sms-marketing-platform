# Production configuration checklist

Every environment variable that has to be set for real on the server, what it
does, and what breaks when it is wrong.

`.env.example` carries **developer** defaults — copying it to a server and
editing it from memory is how a variable gets missed. Work down this file
instead.

**Nothing here contains a real credential, number, hostname or IP, and nothing
here ever should.** This file is committed. The real values live in
`$APP_DIR/.env` on the server, owned by the service user, mode `600`, and
nowhere else.

```bash
sudo -u appuser install -m 600 /dev/null /home/appuser/app/.env
sudo -u appuser nano /home/appuser/app/.env
```

---

## The four that decide whether it works at all

### `SMS_PROVIDER`

| | |
|---|---|
| **Deploy as** | `console` |
| **Does** | Selects the carrier adapter. `console` writes each message to the log and sends nothing. |
| **Wrong** | Set to a live carrier before the walkthrough and the first click of **Send** reaches real handsets. There is no undo on a delivered text. |

Leave it on `console` through bootstrap, through the first deploy, and through
clicking every screen on the live domain. A **human** changes it, once, as step
B5.1 of the launch sequence — never a script and never an agent. The top bar
pill reads **Dry run** until it changes and **Live** after; that pill is the
fastest way to answer "did that campaign actually go out?".

### `PUBLIC_BASE_URL`

| | |
|---|---|
| **Set to** | `https://<the live domain>` — https, no trailing slash |
| **Does** | Every webhook URL handed to the carrier is built from it. |
| **Wrong** | Two failures, and the second is the serious one. No delivery receipts, so every message stays at "sent" and never reaches "delivered". And **no inbound STOP** — opt-out replies go to a URL that does not answer, the blocklist never learns about them, and the next campaign texts people who told you to stop. That is the compliance failure, not a cosmetic one. |

Wrong here is silent. Nothing errors; the receipts simply never arrive. Confirm
it by pasting the webhook URL from **Settings → System** into a browser: a `GET`
on any webhook path returns a health check.

### `SECRET_KEY`

| | |
|---|---|
| **Set to** | `openssl rand -hex 32` — a fresh value for this deployment |
| **Does** | Signs the session cookie. |
| **Wrong** | Reused from another deployment, a session cookie from that one authenticates here. Left as the example value, anyone who has read this repo can mint a valid session. Changed later, everyone is logged out — which is also how you force that deliberately. |

### `DATABASE_URL`

| | |
|---|---|
| **Set to** | `sqlite:///./data/app.db` (the systemd unit's `WorkingDirectory` makes the relative path stable) |
| **Does** | Where the contact list lives. |
| **Wrong** | A path outside the unit's `ReadWritePaths` and the app cannot write — every import and every send fails. A path under `/tmp` and `PrivateTmp=true` gives you a database that vanishes on restart. `scripts/backup.sh` parses this same variable, so a wrong value also means the backups are of the wrong file. |

---

## Login

| Variable | Set to | Wrong |
|---|---|---|
| `ADMIN_USERNAME` | His username | — |
| `ADMIN_PASSWORD_HASH` | Output of `python scripts/hash_password.py` | Blank **and** `ADMIN_PASSWORD` blank: login is disabled and nobody can get in. The app logs exactly that at startup. |
| `ADMIN_PASSWORD` | **Blank in production** | A plaintext password in a file that gets backed up and rsynced. The app warns about this at startup in production; the warning is not the fix. |
| `SESSION_DAYS` | `7` | Longer is a longer-lived stolen cookie. |
| `COOKIE_SECURE` | `true` | `false` sends the session cookie over plain HTTP. Only ever `false` for local `http://` development. |

The predecessor of this codebase shipped with no auth and was found by a
stranger who sent 9,360 unauthorized messages from the client's number in eleven
minutes. Do not skip the hash.

---

## Brand — what he sees

| Variable | Set to | Wrong |
|---|---|---|
| `BRAND_NAME` | His full business name | It is checked against the opening of every campaign message by the "Business identified" pre-flight check. Wrong and that check fails on correct copy. |
| `BRAND_SHORT_NAME` | The sidebar badge, 2–4 characters | Cosmetic. |
| `BRAND_APP_NAME` | What the product is called in the tab title and in alerts | Cosmetic. |
| `BRAND_SUPPORT_PHONE` / `BRAND_SUPPORT_EMAIL` | His, not ours | Support requests routed to the wrong place. |
| `BRAND_COLOR_HEX` / `BRAND_ACCENT_HEX` | Literal `#RRGGBB`, or blank for the validated defaults | A Tailwind color name (`blue-600`) is not a color here; it renders as an invalid CSS variable and the element loses its background silently. |

The category hues `--s1`..`--s4` are deliberately **not** configurable. They were
chosen by running candidates through a colorblind-separation and contrast
validator and they pass all-pairs in both themes.

---

## Commercial terms — what he is billed

His agreed terms: **no monthly fee, 10,000 segments included per cycle, $0.015
per segment after that.** These three variables are the only place those terms
exist; nothing is hardcoded in a template, a service or a fixture.

| Variable | Set to | Wrong |
|---|---|---|
| `BILLING_ENABLED` | `true` | `false` and the Usage screen stops metering. |
| `BILLING_CYCLE_DAY` | `1` | Cycle boundaries move; a campaign lands in the wrong month's allowance. |
| `BILLING_MONTHLY_FEE` | `0` | Bills him a fee he did not agree to. |
| `BILLING_SEGMENTS_INCLUDED` | `10000` | Under-states the allowance and bills him for segments that are free. |
| `BILLING_PRICE_PER_SEGMENT` | `0.015` | The rate on his invoice. |

`WHOLESALE_COST_PER_SEGMENT` is **ours, not his** — what we pay the carrier. It
funds the capacity check and our own logs. It must never reach a response body,
a template, or a log he can read: it discloses our margin and understates his
bill by roughly 40%. Set it to the real wholesale rate; check it never appears
on screen.

---

## Sending behaviour

| Variable | Set to | Wrong |
|---|---|---|
| `PREFLIGHT_BALANCE_CHECK` | `true` | `false` disables the single most valuable safeguard here. It is what turns "we lost 4,623 messages mid-blast" into "the campaign refused to start". Do not turn it off to get a send through. |
| `SEND_DELAY_SECONDS` | `0.15` | Too low and the carrier rate-limits or flags the number. Too high and a large blast takes hours. |
| `SKIP_NON_US_NUMBERS` | `true` unless the messaging profile covers Canada/international | `false` with a US-only profile means paying for messages that cannot be delivered. |
| `RECENT_CONTACT_SUPPRESSION_DAYS` | Per spec | This decides whether a real person gets two texts in three days. Not a tuning knob. |
| `PREFLIGHT_SEGMENT_CEILING` | `3` | Only changes when the length warning fires. |

---

## Alerts — ours, not his

| Variable | Set to | Wrong |
|---|---|---|
| `ALERT_PHONE` | **Your** number, never the client's | The client gets our operational alerts. |
| `BALANCE_ALERT_THRESHOLD` | Well above zero — high enough that one full blast cannot cross it in the gap between hourly checks | Set near zero and the alert fires after the damage. The reference client lost 19,375 messages across eight campaigns to exactly this. |

The scheduled low-credit alert and the daily failure digest both page through
`agent/notify.sh`, which is ours and is the one file allowed to name the
carrier. Configure its credential separately from the sending account — the
whole point is that the warning still goes out when the sending account is the
thing that is empty.

---

## Backups

| Variable | Set to | Wrong |
|---|---|---|
| `BACKUP_REMOTE` | `user@host:/path`, or an S3-compatible target | Unset and every backup sits on the same disk as the database. That survives a bad deploy and not a dead droplet. `backup.sh` warns loudly rather than failing. |

The prior client's 181 MB production database had no backup at all. The cron
entry `bootstrap.sh` installs runs `--verify` nightly, which restores the newest
archive and runs `PRAGMA integrity_check` on it. A backup nobody has restored is
not a backup.

---

## Carrier credentials

Filled in **by a human, at step B5.1**, not before. Until then the values stay
blank and `SMS_PROVIDER=console`.

The variable names are in `.env.example`. What matters here:

- The sending number must be the one the client is told is his.
- The messaging profile has to cover the regions `SKIP_NON_US_NUMBERS` implies.
- Auto-recharge: **$200–300 per top-up on a card, threshold around $100.** The
  reference client had $10 top-ups via PayPal. A 6,000-recipient blast spends
  that in about ninety seconds — faster than the recharge can post — and it
  drained mid-blast eight separate times. Pre-fund before anything large.

---

## Before you hand over the login

```bash
# Every one of these must pass on the live domain.
curl -sf https://<domain>/health                    # 200
curl -s -o /dev/null -w "%{http_code}" https://<domain>/dashboard   # 302 -> /login
curl -s -o /dev/null -w "%{http_code}" https://<domain>/static/app.css  # 200
sudo systemctl is-active <service>                  # active
sudo systemctl is-active certbot.timer              # active
cd $APP_DIR && ./scripts/backup.sh --verify         # takes one and restores it
```

The certificate timer is on that list because the prior client's certificate sat
45 days from expiry with nobody watching.

Then walk the launch sequence in `sessions/session-5b.md` Part B, in order.
Step B5.3 — reply STOP from your own handset and confirm the next send skips it
— comes **before** the first send to real contacts. Opt-out handling is the one
thing that must work on day one, and it is cheaper to prove with your own phone
than with fifty of his buyers.
