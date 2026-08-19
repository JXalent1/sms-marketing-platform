# Session 1b — Module 1 review fixes

## Objective

Module 1's gate passes and its scope was delivered, but a fresh-context review of
`33a7d5d..HEAD` found two confirmed defects and one structural problem that lands on
module 2 if it isn't fixed first. This is a short corrective session. **No new features,
no module 2 work.**

All three findings were verified against the repo before this spec was written — they are
facts, not suspicions.

## Prerequisites

- `CLAUDE.md` read in full.
- Module 1 complete (`bash agent/gate.sh` passes, 34 tests).
- Run the gate first and show it green, so any red later is yours.

## Scope

Four fixes and one correction. Nothing else.

---

### 1. The client is being shown our wholesale cost (HIGH — confirmed)

`app/core/config.py:62`:
```python
PREFLIGHT_COST_PER_SEGMENT: float = 0.009    # your blended carrier rate, for the estimate
```

That is **our** rate. The client is billed **$0.015**. It reaches his screen in three
places, all in `app/templates/campaigns.html`, none of which the white-label sweep
touched because they were only given the mechanical brand-class swap:

- **line 202** — every draft he creates toasts
  `est. carrier cost $${data.campaign.estimated_cost.toFixed(2)}`
- **line 237** — the campaign history list shows `· est. $${c.estimated_cost.toFixed(2)}`
- **line 138** — the UCS-2 warning says *"would cut this campaign's carrier cost roughly in half"*

Served by `app/routers/campaigns.py:226` (`"estimated_cost": c.estimated_cost`), computed
at `app/services/campaign_service.py:116` from `PREFLIGHT_COST_PER_SEGMENT`.

Three separate problems in one field: it names the carrier relationship, it discloses our
margin, and it shows him a number roughly 40% below what he will actually be invoiced —
which is arguably the worst of the three, because he'll plan against it.

**Fix:**
- Remove `estimated_cost` from `_campaign_dict` in `app/routers/campaigns.py`. Grep first
  to confirm nothing else consumes it; if the operator genuinely needs it, it belongs
  behind a separate operator-only route, not the client campaign payload.
- Keep `campaign.estimated_cost` on the model and in logs — pre-flight needs it. This is
  about what crosses the API boundary.
- Rewrite all three UI strings in **segments**, and where a dollar figure is genuinely
  useful to him, compute it from the *client* rate (`BILLING_PRICE_PER_SEGMENT`) via
  `billing_service`, never from `PREFLIGHT_COST_PER_SEGMENT`. Example:
  *"Draft created — 6,771 recipients, ~9,340 segments"*.
- Rename `PREFLIGHT_COST_PER_SEGMENT` to something that cannot be mistaken for the client
  rate, e.g. `WHOLESALE_COST_PER_SEGMENT`, and comment that it must never reach a
  response body or template.

### 2. Currency rounding drifts one cent low on ~24% of invoices (MEDIUM — confirmed)

`billing_service.to_money()` does `Decimal(str(amount))` — the right instinct — but
`cost_for_segments()` hands it a **float** that has already lost the boundary. Every odd
billable-segment count lands on a half-cent, and about a quarter of them sit just below
it:

```
billable=11 → 11*0.015 = 0.16499999999999998 → 0.16   (should be 0.17)
billable=15 → 0.22499999999999998            → 0.22   (should be 0.23)
billable=27 → 0.40499999999999997            → 0.40   (should be 0.41)
```

Measured: **11,782 of the first 50,000 odd counts are wrong, always one cent low.** The
existing tests miss it because `0.125`, `0.135`, `2.005`, `1×`, `3×` and `22940×` all
happen to repr cleanly.

The docstring already argues why this matters. It's the same systematic drift it warns
about, one order of magnitude smaller.

**Fix:** do the arithmetic in `Decimal` end to end — `cost_for_segments()` returns a
`Decimal`, `to_money()` accepts `Decimal | float`. Add tests: `10011 → $0.17`, and a loop
over odd counts asserting float-path and exact-decimal agree.

**This is billing math**, which is normally an escalation. It is pre-authorised here:
you are fixing an arithmetic defect so the code matches the already-agreed terms. The
model, the rate, the allowance and the billable-status set do not change. If you find
yourself wanting to change any of those, stop and escalate.

### 3. Alembic and `create_all()` will collide, and module 2 is where it lands (HIGH)

Three linked problems:

- `app/main.py:32` still runs `Base.metadata.create_all()` at import. Any database the
  app has ever started against has all seven tables and **no `alembic_version` row**, so
  `alembic upgrade head` on it fails with `table app_settings already exists`. Nothing in
  the README, handoff or `deploy.sh` mentions `alembic stamp head`.
- Module 2 adds `categories` and `contact_categories`. Once those models exist, *starting
  the app* creates the tables silently with no version bump, and the migration then fails.
  Whichever order the developer happens to use decides whether it works.
- **No test exercises the migration.** `tests/conftest.py` builds the scratch schema with
  `create_all()`, so a migration that diverges from the models — precisely what Alembic
  exists to prevent — is invisible to a green suite.

**Fix:**
- Change `conftest` to build the scratch schema with `alembic upgrade head` instead of
  `create_all()`. About four lines, and it makes every future migration a tested artifact.
- Make `create_all()` conditional — development only, and only when no `alembic_version`
  table exists — or remove it now while there is exactly one migration to stamp.
- Add `alembic stamp head` guidance for pre-existing databases to `README.md` and
  `deployment/deploy.sh`.

### 4. Add a white-label test that runs the code (HIGH — the gate cannot do this)

The three leaks module 1 found, and the one in §1 it missed, were all **assembled at
runtime** — f-strings, URL construction, `str(e)`, a JS template literal reading an API
field. `agent/gate.sh` greps for literals and structurally cannot see any of them.

Add `tests/test_whitelabel.py` that exercises the app and scans what comes back:

- Hit every authenticated JSON endpoint the smoke test already covers; assert no response
  body matches `/telnyx|twilio/i`.
- Assert `estimated_cost` does not appear in any `/api/campaigns` response.
- Assert a forced pre-flight failure's client-visible message contains no `$` figure and
  no provider name.
- Assert `/api/settings/system` exposes no webhook URL.

Write it so adding a new client-facing route makes it fail by default rather than pass by
omission.

### 5. Correct a false statement in `status.md`

`status.md` under "Found while working" says `scripts/balance_alert.py` *"reads the
provider balance directly rather than through `/api/usage/balance`"*. **That file does not
exist** — `scripts/` contains only `hash_password.py` and `seed_demo_data.py`. The name
appears only in two docs. Delete or correct the claim. A confident, specific, false note
in a handoff is worse than no note, because the next session will trust it.

---

## Explicitly out of scope

- **`app/static` does not exist on a fresh clone.** The review raised this and flagged its
  own uncertainty. It is **wrong** — the four Inter `.woff2` files are tracked
  (`git ls-files app/static` → 4). Do not "fix" this.
- Tailwind opacity modifiers (`bg-brand/50`) not working with bare `var()` colors — real,
  but modules 3 and 8 own the templates that would use them.
- `--on-brand` not being derived from a custom brand hex — real and worth doing, but it
  needs the client's actual brand colors, which we don't have yet. Log it in `status.md`.
- Everything else in the review's Low and Nit sections. Log, don't fix.
- Module 2 work of any kind.

## Acceptance criteria (demonstrate each in the transcript)

- [ ] `bash agent/gate.sh` exits 0 — output shown
- [ ] Suite run **twice in a row**, green both times
- [ ] `grep -rn "estimated_cost" app/routers/ app/templates/` returns nothing
- [ ] `grep -rni "carrier cost" app/templates/` returns nothing
- [ ] A billing test proves `10011` segments → `$0.17`, and a loop over odd counts shows
      the float path agreeing with exact decimal — output shown
- [ ] `tests/test_whitelabel.py` exists and passes; show it failing first by temporarily
      reintroducing one leak, then passing once reverted
- [ ] `alembic upgrade head` builds the test schema (show conftest doing it) and the suite
      still passes
- [ ] `status.md` no longer references `scripts/balance_alert.py`
- [ ] `status.md` and `handoff.md` updated

## Constraints

- `agent/gate.sh` and `agent.config.sh` are human-only; the hook blocks edits.
- Do not change the billing model, rate, allowance, or billable-status set — only the
  arithmetic path.
- Do not touch `count_sms_segments()` or weaken the pre-flight check.
- Stay in: `app/routers/campaigns.py`, `app/templates/campaigns.html`,
  `app/services/billing_service.py`, `app/services/campaign_service.py`,
  `app/core/config.py`, `app/main.py`, `tests/`, `README.md`,
  `deployment/deploy.sh`, `status.md`, `handoff.md`.
