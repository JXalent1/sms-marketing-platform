# Session 5c — Live-send blockers found during launch

## Objective

The platform is deployed and serving at https://app.onlineauctions.co. The first live
send is blocked by a dependency pin that made the app silently unable to send while the
dashboard reported itself healthy. Fix that, make the failure mode visible, and close two
hygiene items that came out of B3.

This session exists because launch surfaced defects, not because a module was planned.
It is the last agent work before the first real SMS goes out.

## Prerequisites

- Read `CLAUDE.md`, `RULES.md`, `status.md` and `modules.md` before touching anything.
- Session 5b Part A and step B3 are complete and verified. Server bring-up is done.
- Production is live: systemd unit `a4a-sms` from `/home/appuser/app`, nginx + Let's
  Encrypt, SQLite at `data/app.db`, Alembic at head (`8c1d4a2f70b3`).
- Database is empty — 0 contacts, 0 messages, 0 campaigns. Nothing has been sent.
- Server access: `ssh -i ~/.ssh/a4a_deploy appuser@67.205.180.62`.
  `appuser` has a sudoers entry for `systemctl restart a4a-sms` **only** — no general
  sudo, and no journal read access (not in the `adm` group).
- Deploy with `SERVER=appuser@67.205.180.62 SERVICE=a4a-sms ./deployment/deploy.sh`.

---

# Part A — agent work

## A1. The telnyx SDK pin (the blocker)

`SMS_PROVIDER` was flipped from `console` to `telnyx` in production. The dashboard kept
showing the amber **"Dry run"** pill anyway, with a valid API key configured and the real
sender number rendering in the sidebar.

Cause: `requirements.txt:32` pins `telnyx==2.1.2`, but `app/sms/providers/telnyx.py:35`
calls `telnyx.Telnyx(api_key=...)` — the 4.x client class, which does not exist in 2.x.
`TelnyxProvider.__init__` raised, and `get_provider()` in `app/sms/factory.py:44-47`
caught it and fell back to the console provider. Reproduced on the box:

```
version: 2.1.2
has Telnyx class: False
AttributeError: module 'telnyx' has no attribute 'Telnyx'
```

The server has been hot-patched to `telnyx==4.175.0` and the provider now constructs.
**The repo is still wrong** — the next `deploy.sh` runs `pip install -r requirements.txt`,
which reinstalls 2.1.2 and silently breaks sending again while the UI still looks healthy.

Bump the pin to `telnyx==4.175.0`.

Verified against the real 4.175.0 package — confirm each of these yourself rather than
taking the spec's word for it:

- `client.messages.send()` accepts `to`, `from_`, `text`, `messaging_profile_id`
- the return is `MessageSendResponse` with `.data.id` and `.data.parts`
- its deps are `anyio<5`, `distro<2`, `httpx<1`, `pydantic<3`, `sniffio`,
  `typing-extensions>=4.14`

Check that nothing else in `requirements.txt` resolves to a different version as a result.

## A2. Make a degraded provider distinguishable from a chosen one

The fallback in `factory.py` is correct behaviour — a dashboard serving a client must not
crash because a carrier credential is wrong. The defect is that it is **invisible**. It
logs at ERROR into a journal `appuser` cannot read, and the UI renders a normal,
reassuring "Dry run" pill identical to a deliberate dry run. A live client box sat in a
broken state and the product reported everything was fine.

- Record on the provider instance (or in factory state) that a fallback occurred, with the
  exception type and message.
- Expose it in `/api/settings/system`, and render it on the Settings page and in the
  top-bar pill. A *failed* provider must not look like a *chosen* one.
- Keep it white-label. No carrier name reaches the client — something like
  "Sending unavailable — contact support" in the UI, with the real exception only in logs.
  `CLAUDE.md` records that white-label leaks are assembled at runtime, not written as
  literals, so check rendered HTML and JSON responses, not just source.

## A3. Tests that would have caught this

- `get_provider()` returns the Telnyx provider — not console — when `SMS_PROVIDER=telnyx`
  and an API key is set.
- The fallback path sets the degraded flag and surfaces it in the settings payload.
- White-label assertions cover the new degraded-state strings.

Run these authenticated. `CLAUDE.md` records that the login rate limiter (10/min) has
previously starved tests into 429s, so white-label assertions passed against
unauthenticated response bodies and proved nothing.

## A4. nginx security headers

`deployment/nginx.conf.template` sets no security headers on a public admin panel. Add:

- HSTS at `max-age=300` to start — **not** a year. We need to be able to walk it back.
- `X-Frame-Options DENY`
- `X-Content-Type-Options nosniff`
- `Referrer-Policy same-origin`
- a CSP matching the app's actual same-origin-only asset profile

Put them in the template so the next client inherits them. Do not hand-edit the box. The
likely breakage is a CSP that blocks the self-hosted Inter woff2 — verify fonts still load.

## A5. status.md

There is an uncommitted entry under "Found while working (session 5b)" about the missing
`SECRET_KEY` guard: `config.py:36` defaults to `""`, `auth.py:59` signs with it regardless,
and the dangerous case is a box where `ENVIRONMENT` was never set — so the fix should be
unconditional, not production-only. Commit it, and add an entry for the telnyx SDK bug
alongside.

---

## Part A acceptance

Demonstrate each in the transcript. Self-declared completion does not count.

1. `agent/gate.sh` passes all six checks.
2. `pip install -r requirements.txt` into a clean venv installs telnyx 4.175.0, and
   `TelnyxProvider()` constructs against it.
3. With `SMS_PROVIDER=telnyx` and a key set, `get_provider()` returns the Telnyx provider.
4. With a deliberately broken credential, the Settings page and the pill both show a
   degraded state distinct from "Dry run", and neither leaks a carrier name.
5. New tests pass, and they fail against the pre-fix code — show both.
6. After deploy: all seven screens return 200 over HTTPS, fonts load, and no carrier name
   appears in any rendered page or API response.

Wire this into a `/goal` stop condition with a turn cap, then run a fresh-context review
pass before declaring done.

---

## Constraints

- Do not touch `.env`, `.env.production`, `agent/gate.sh` or `agent.config.sh`. The
  PreToolUse hook blocks these by design. If an env change is needed, escalate — Jordan
  does it by hand.
- No source file exceeds 500 lines.
- `app/sms/` must not import `app.models` or `app.services`.
- No runtime CDN. Every asset same-origin.
- No hardcoded commercials — monthly fee, included segments and per-segment price all come
  from `.env` and render from one place.

## Explicitly out of scope

- **Do not send any SMS.** Jordan runs the first live send by hand.
- **Do not import any contacts.** The five category CSVs are staged at
  `data/contacts/` and Jordan imports them through the UI so he can check preview counts.
- The `SECRET_KEY` guard itself — logged in `status.md` this session, fixed post-launch.
- History and Categories screens, quiet hours, prospecting. All post-launch.

---

# Part B — human work (Jordan, not the agent)

Runs after Part A acceptance clears.

### B4. Load the real contacts

Import through the UI, checking each preview against the expected count before committing.
Files are in `data/contacts/`.

| # | File | Category | Expect |
|---|---|---|---:|
| 1 | `estates.csv` | Estates | 356 |
| 2 | `equipment.csv` | Equipment & Machinery | 406 |
| 3 | `general.csv` | General Merchandise | 85 |
| 4 | `memorabilia.csv` | Memorabilia | 3 |
| 5 | `food_service.csv` | Food Service | 593 |

Estates first — cleanest file, so a broken import flow shows up on 356 rows rather than 593.

Final total should be roughly **1,223 unique contacts**, not 1,443. The gap is deliberate
cross-listing: a restaurant-supply dealer is in both Food Service and Equipment, and the
importer tags the existing contact into the second category instead of creating a second
row. **If the total comes out at 1,443, dedup is not firing — stop.**

`unclassified.csv` (455) is held back pending source-file labels. Do not import it.

### B5. The send sequence — in this order

1. Add `+16199933683` as a contact in Memorabilia.
2. Send one plain-ASCII message under 160 characters — no emoji, no curly quotes. Preflight
   should read 1 recipient, 1 segment, $0.015.
3. Confirm the text arrives from 954-738-2462.
4. Confirm status flips `sent` → `delivered`. That is the delivery webhook proving itself.
5. Reply **STOP**. Confirm the number lands on the Opt-outs page, and that a second send to
   Memorabilia reports 1 skipped. **If STOP does not blocklist, do not send to the full
   list.**
6. 50 contacts to one category.
7. Hand over the login.

### B6. Before the first real blast

- Telnyx messaging profile webhook must point at `https://app.onlineauctions.co/webhooks/telnyx`.
- `agent/notify.sh` still reads the carrier credential it is meant to be warning about —
  give it a separately-funded credential.
- Revoke the `a4a-deploy-agent` key now that contact data is landing.
- Add `appuser` to the `adm` group so logs are readable without root.
