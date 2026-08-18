# Session 1 — Foundation, pricing & white-label

## Objective

Module 1 of 8. Put the project on ground the next seven sessions can stand on: version
control, real migrations, a compiled stylesheet with a dark-first token system, A4A's
commercial terms, and no trace of the SMS carrier in anything a user can see.

No features are built in this session. Everything here is plumbing — but it's plumbing
that every later module depends on, and retrofitting any of it later is worse than doing
it now.

## Prerequisites

- `CLAUDE.md` has been read in full, including "How to verify work in this project".
- Previous sessions completed: none. This is the first.
- Baseline confirmed before you change anything: `python -m pytest tests/ -q` → 22 passed.
  Run it first and show the output. If it isn't 22 passed, stop and report.

## Scope for this session

Build:
1. Git repository with an initial commit
2. Alembic, with an initial migration capturing the current schema
3. Compiled Tailwind stylesheet + self-hosted Inter + static file mount
4. CSS custom-property token system, dark by default
5. Brand color from `.env` as a hex value
6. A4A billing terms
7. Carrier-name sweep

Do NOT build in this session:
- Categories, or any schema change beyond the Alembic baseline
- Any new screen or route
- The redesigned Usage screen layout (module 8) — only its numbers change here
- Any prospecting code

---

## Detailed specification

### 1. Git

`git init` in `sms-marketing-platform/`. A `.gitignore` already exists — extend it with
`node_modules/`, `app/static/app.css`, `*.db`, `.env`, `browser_data/`.

Commit the untouched skeleton **first**, as its own commit, before making any changes.
That gives a clean diff for the review pass. Then commit this session's work in logical
chunks.

Rationale worth a comment in the repo: the prior client's production server had no git
repo and its local checkout silently drifted six files behind production, which meant a
deploy would have reverted three months of fixes.

### 2. Alembic

- `alembic init alembic`
- Point `alembic/env.py` at `app.core.database.Base.metadata` and read the URL from
  `app.core.config.settings.DATABASE_URL` rather than hardcoding it in `alembic.ini`
- Generate the initial migration from the existing models (`contacts`, `contact_lists`,
  `contact_list_members`, `campaigns`, `sms_messages`, `blocked_numbers`, `app_settings`)
- It must apply cleanly to an empty database

`Base.metadata.create_all()` may stay in place for local convenience, but from this point
on every schema change goes through Alembic. Note that in `CLAUDE.md` if it isn't already
clear.

### 3. Front-end build pipeline

The current `base.html` loads Tailwind from `https://cdn.tailwindcss.com` at runtime.
That is the Play CDN, which is documented as development-only: it ships the compiler to
the browser, regenerates CSS on every page load, and flashes unstyled content on every
navigation. With the CDN unreachable the entire app renders as raw unstyled HTML — this
has been reproduced, it is not theoretical.

Replace it:

- `package.json` with `tailwindcss@3.4.17` as a devDependency and a `build:css` script
- `tailwind.config.js` with `content: ["./app/templates/**/*.html"]`
- An input stylesheet (`app/assets/tailwind.css`) with the three `@tailwind` directives
  plus the `@font-face` and `:root` blocks below
- Output compiled to `app/static/app.css` (gitignored; built as part of deploy)
- Self-host Inter via the `@fontsource/inter` npm package — copy the woff2 files into
  `app/static/fonts/` and declare `@font-face` locally. Remove the `fonts.googleapis.com`
  preconnects and stylesheet link.
- Mount static files in `app/main.py`:
  `app.mount("/static", StaticFiles(directory="app/static"), name="static")`
- Document the build step in `README.md` and wire it into `deployment/deploy.sh`

### 4. Token system

`base.html` currently builds a class name dynamically: `bg-{{ brand.color }}-600`. That
only works because the Play CDN JITs at runtime. Once Tailwind is compiled, that class
gets purged and the brand color silently disappears. It also limits the brand to
Tailwind's named colors, so a client's actual hex is impossible.

Replace it with CSS custom properties. **Dark is the default** — light lives under
`[data-theme="light"]`, not the other way round.

Put these in the input stylesheet. They are taken from `Auctions4America.pen`, so the
design file and the code share one vocabulary — keep the names identical.

```
:root {                         /* dark — the default */
  --page:#0D0D0D;  --surface:#1A1A19;  --surface-2:#232322;
  --ink:#FFFFFF;   --ink-2:#C3C2B7;    --ink-3:#898781;
  --line:#2C2C2A;  --line-strong:#383835;
  --brand:#3987E5; --brand-soft:#16233A; --on-brand:#0D0D0D;
  --accent:#C98500; --accent-soft:#2A2110;
  --s1:#3987E5; --s2:#D95926; --s3:#199E70; --s4:#B457B4;
  --good:#0CA30C; --good-ink:#0CA30C; --good-soft:#0F2411;
  --warn-ink:#FAB219; --warn-soft:#2B2308;
  --crit:#E66767; --crit-soft:#2E1412;
}
[data-theme="light"] {
  --page:#F7F7F5;  --surface:#FCFCFB;  --surface-2:#F1F1EE;
  --ink:#0B0B0B;   --ink-2:#52514E;    --ink-3:#8A8880;
  --line:#E4E3DC;  --line-strong:#C9C8C0;
  --brand:#123A6B; --brand-soft:#E7EEF8; --on-brand:#FFFFFF;
  --accent:#B5851F; --accent-soft:#FBF3DF;
  --s1:#2A78D6; --s2:#EB6834; --s3:#1BAF7A; --s4:#A3419F;
  --good:#0CA30C; --good-ink:#006300; --good-soft:#E3F5E3;
  --warn-ink:#7A5200; --warn-soft:#FCF0D4;
  --crit:#C3372F; --crit-soft:#FBE7E5;
}
```

Expose these to Tailwind in `tailwind.config.js` via
`theme.extend.colors` entries like `brand: "var(--brand)"`, so templates can write
`bg-brand text-ink border-line` and stay readable.

**Brand override from `.env`:** `app/core/branding.py` currently exposes `color` as a
Tailwind color *name*. Change it to `color_hex` and `accent_hex` (validate they're
`#RRGGBB`; fall back to the defaults above if unset or malformed). `base.html` emits a
small inline `<style>` that overrides `--brand` and `--accent` with those values. One
place, no other template touches brand.

The `--s1`..`--s4` category colors are **not** brand-configurable. They were selected by
running candidate palettes through a colorblind-separation and contrast validator, and
they pass all-pairs in both modes. Changing them by eye will break that. Leave a comment
in the stylesheet saying so.

Update `base.html`: `<html data-theme="dark">`, local stylesheet link, no CDN script, no
Google Fonts, brand classes swapped to the token-based utilities. Other templates keep
working — this session isn't redesigning them, only ensuring they still render.

### 5. Billing

Current model is a monthly base fee plus a tiered overage against a 15,000-segment
allowance. A4A's terms are different and simpler:

- **No monthly fee**
- **10,000 segments included per month**
- **$0.015 per segment beyond that**

Add to `app/core/config.py` and `.env.example`:

```
BILLING_MONTHLY_FEE=0
BILLING_SEGMENTS_INCLUDED=10000
BILLING_PRICE_PER_SEGMENT=0.015
```

`billing_service` computes:

```
billable_segments = max(0, segments_this_cycle - BILLING_SEGMENTS_INCLUDED)
cost = BILLING_MONTHLY_FEE + (billable_segments * BILLING_PRICE_PER_SEGMENT)
```

Round currency half-up to 2dp at the point of display, not during accumulation.

Only `('sent', 'delivered')` messages count toward segments — that rule already exists and
must not change. `blocked`, `skipped`, `failed` and `undelivered` are not billable.

Remove the tier table and the allowance constant. Update `app/routers/usage.py` and
`app/templates/usage.html` so the numbers and labels reflect the new model — "10,000
included · N billable", no "base fee", no "overage" language. The screen's layout is
module 8's job; only its content changes here.

### 6. White-label sweep

This is a client-facing product under the Auctions4America brand. The SMS carrier is our
implementation detail and must never be visible.

- Grep the whole app for the carrier's name. Anything in a template, a user-facing string,
  an error message, a log line the client could see, or an export gets replaced with
  neutral language.
- Provider *module* names and internal identifiers stay as they are — this is about what
  a user can see, not about renaming the code.
- Any UI that shows a carrier-denominated account balance becomes segment-denominated.
  In the sidebar the design shows "Segments this month" and "Your number".
- Confirm `scrub_provider_text()` is applied on every path where a provider error reaches
  a user, not just the one it currently covers.

---

## Acceptance criteria (demonstrate each in the transcript)

The evaluator reads the conversation only — it cannot run commands or open files. So each
criterion below means: run the command, show the output.

- [ ] Baseline shown before changes: `python -m pytest tests/ -q` → 22 passed
- [ ] After changes: `python -m pytest tests/ -q` exits 0, with the new billing tests included
- [ ] Billing test output proves: 32,940 segments → `$344.10`, and 8,000 segments → `$0.00`
- [ ] `alembic upgrade head` against a fresh empty DB succeeds — output shown
- [ ] `npm run build:css` succeeds and `app/static/app.css` exists — `ls -la` shown
- [ ] App starts; `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/static/app.css` → `200`
- [ ] `curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/login` → `200`
- [ ] `grep -rn "cdn.tailwindcss.com\|fonts.googleapis.com" app/templates/` → no output
- [ ] `grep -rni "telnyx" app/templates/ app/routers/` → no output
- [ ] A screenshot or rendered-HTML excerpt showing the app styled correctly in dark mode
      with the local stylesheet
- [ ] `git log --oneline` shows the untouched-skeleton commit first, then this session's work
- [ ] No file exceeds 500 lines — show the check
- [ ] `status.md` updated and `handoff.md` rewritten

## Constraints

- No file exceeds 500 lines.
- Only touch the files listed for module 1 in `modules.md`. If you need something else,
  stop and flag it.
- Do not change `count_sms_segments()`. It matches the carrier's own `parts` value, it is
  correct, and it was expensive to get right.
- Do not weaken or remove the pre-flight balance check.
- Do not import `app.models` or `app.services` from anything in `app/sms/`.
- Do not add features. If something looks needed but isn't in scope, flag it and stop.

## Open questions

- **A4A's real brand hex is not yet available.** Use the placeholder `#123A6B` (light) /
  `#3987E5` (dark) from the design file. Because it comes from `.env`, swapping it later
  is a one-line change — make sure that's actually true when you're done, i.e. no brand
  color is hardcoded in a template.
- If `@fontsource/inter` pulls in more than the weights we use (400/500/600/700), ship only
  those four woff2 files rather than the whole package.
