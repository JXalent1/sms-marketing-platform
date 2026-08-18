# Standing this up for a new client

Work top to bottom. Nothing sends until step 6, on purpose.

---

## 1. Brand it (5 minutes)

```bash
cp .env.example .env
```

Fill in the brand block. This is the only place a client's name appears:

```
BRAND_NAME=Acme Auto Group          # used in SMS copy and opt-out text
BRAND_SHORT_NAME=Acme               # nav bar
BRAND_APP_NAME=Acme Marketing       # page titles
BRAND_SUPPORT_PHONE=(555) 123-4567  # appears in the auto-reply
BRAND_SUPPORT_EMAIL=info@acme.com
BRAND_COLOR=emerald                 # any Tailwind color name
```

**Verify:** `grep -ri "acme\|williamson" app/` returns nothing. If a client name
is in a `.py` or `.html` file, it belongs in `.env` instead.

---

## 2. Secure it (5 minutes)

```bash
openssl rand -hex 32                 # → SECRET_KEY
python scripts/hash_password.py      # → ADMIN_PASSWORD_HASH
```

Leave `ADMIN_PASSWORD` blank in production. Read
[SMS_LESSONS.md](SMS_LESSONS.md#7-no-auth-is-not-a-risk-its-an-incident-waiting-for-a-date)
if you are tempted to skip this.

---

## 3. Run it dry (10 minutes)

```bash
./run.sh                             # http://localhost:8000
python scripts/seed_demo_data.py     # optional sample contacts
python -m pytest tests/ -v
```

`SMS_PROVIDER=console` means messages are logged, never sent. Stay here while you
build. Click through every page and send a fake campaign — the log shows exactly
what would have gone out, with segment counts.

---

## 4. Wire up the contact source (the real work)

Decide where contacts come from:

- **CSV upload only** → nothing to do. `/contacts` already imports them.
- **Client's CRM / booking system / API** → copy
  `app/sources/example_api_source.py`, implement `fetch()`, register it in
  `app/sources/__init__.py`.
- **Scheduled sync** → add a job to the `lifespan` block in `app/main.py`.

Only `fetch()` is yours to write. Normalization, dedup and persistence are
handled by `ContactSource.ingest()`.

**Verify:** import real client data with `SMS_PROVIDER=console` still set, and
check `/contacts`. Look for names in the phone column and vice versa.

---

## 5. Adapt the campaign features

Common per-client changes, in the order they usually come up:

| Ask | Where |
|---|---|
| Different merge tags | Put the data in `Contact.attributes`; `{key}` works automatically |
| Different audience rules | `contact_service.resolve_audience()` |
| Send-time throttle | `SEND_DELAY_SECONDS` in `.env` |
| Quiet hours / scheduling | Add a check at the top of `CampaignService.send_campaign()` |
| Different opt-out copy | `app/sms/compliance.py` |
| Extra campaign fields | `app/models/campaign.py` + the composer template |

Keep the four safety rails: blocklist check, region filter, pre-flight balance
check, and delivery-webhook recording. Each one exists because of a specific
incident documented in [SMS_LESSONS.md](SMS_LESSONS.md).

---

## 6. Go live on a carrier

**Before any of this, the client needs 10DLC registration** (brand + campaign
through TCR). It takes days to weeks and unregistered A2P traffic gets filtered
hard. Start it early.

Then:

1. Buy a number on a messaging profile approved for the destination region.
2. Set `SMS_PROVIDER`, the API key, the number, and the profile ID in `.env`.
3. **Set the webhook URL in the carrier portal.** Copy it from Settings → System
   in the dashboard. If this is blank, STOP handling and delivery tracking both
   silently do nothing.
4. Fund the account properly and set auto-recharge to cover a *full campaign* —
   see [lesson 5](SMS_LESSONS.md#5-a-campaign-that-outruns-the-balance-destroys-itself).
5. Send a test SMS to a real handset from the composer. Confirm it arrives, not
   just that the API returned success.
6. Text STOP to the number from that handset. Confirm it lands in `/blocklist`.
7. Text START. Confirm it clears.

---

## 7. Set the pricing plan

```
BILLING_BASE_FEE=400
BILLING_INCLUDED_SEGMENTS=15000
BILLING_OVERAGE_TIERS=5000:0.025,10000:0.022,20000:0.020,inf:0.016
BILLING_CYCLE_DAY=1
```

Sanity-check against your real cost. A blended carrier rate of ~$0.009/segment
means a $400 base with 15,000 included segments only breaks even around 44,000
segments of usage. Model the client's expected volume before agreeing terms.

Set `BILLING_ENABLED=false` if you're charging flat-rate and don't want the
client seeing a usage meter.

---

## 8. Deploy

```bash
# On the server, as a non-root user:
sudo adduser --disabled-password appuser
sudo -u appuser git clone <repo> /home/appuser/app
cd /home/appuser/app && python3 -m venv venv && venv/bin/pip install -r requirements.txt

sudo cp deployment/app.service.template /etc/systemd/system/acme-bot.service
sudo cp deployment/nginx.conf.template /etc/nginx/sites-available/acme-bot
# edit paths + domain in both, then:
sudo systemctl daemon-reload && sudo systemctl enable --now acme-bot
sudo ln -s /etc/nginx/sites-available/acme-bot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d acme-bot.example.com
```

Then the hourly balance alert:

```bash
sudo -u appuser crontab -e
0 * * * * cd /home/appuser/app && venv/bin/python scripts/balance_alert.py >> logs/balance_alert.log 2>&1
```

Set `ALERT_PHONE` to **your** number, not the client's.

Subsequent deploys: `SERVER=... SERVICE=... ./deployment/deploy.sh`

---

## 9. Pre-launch checklist

- [ ] `grep -ri "williamson\|example company" app/` is empty
- [ ] `ADMIN_PASSWORD_HASH` set, `ADMIN_PASSWORD` blank
- [ ] `SECRET_KEY` is a fresh random value, not the example
- [ ] `COOKIE_SECURE=true` and HTTPS works
- [ ] `python -m pytest tests/` passes
- [ ] Logged out, confirmed `/dashboard` redirects and `/api/contacts` 401s
- [ ] Webhook URL configured in the carrier portal and returns 200 in a browser
- [ ] Test SMS received on a real handset
- [ ] STOP → appears in `/blocklist`; START → clears it
- [ ] Carrier account funded; auto-recharge covers a full campaign
- [ ] `ALERT_PHONE` set and `balance_alert.py` in cron
- [ ] Pricing in `.env` matches what the client actually agreed
- [ ] First real campaign sent with a small `batch_size` before the full blast

---

## 10. First campaign

Do it staged, every time:

1. Send a test to your own phone. Read it on the handset.
2. Check the composer's segment count. If it says UCS-2, ask whether that emoji
   is worth doubling the carrier bill.
3. Check the composer's link warning. Shortener links get spam-blocked.
4. Run it with `batch_size: 50`.
5. Wait ten minutes, then check the campaign detail. `delivered_count` should be
   climbing. If everything sits at `sent` with zero delivered, the webhook isn't
   configured. If messages are turning `undelivered`, look at the failure
   breakdown before sending to the rest of the list.
6. Only then send the full audience.
