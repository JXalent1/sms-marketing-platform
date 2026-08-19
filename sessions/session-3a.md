# Session 3a — UI shell

## Objective

Module 3a. Rebuild `base.html` as the dark application shell from
`Auctions4America.pen`. Small, focused session — it exists on its own because every other
template extends this file, and sessions 3b and 4 run in parallel against it afterwards.

**One file of substance, plus its route wiring.** Resist doing more.

## Prerequisites

- `CLAUDE.md` read in full.
- Modules 1, 1b, 2 and 5a merged. Run `bash agent/gate.sh` first and show it green —
  **76 tests**. If it says 46, you're on a stale branch.
- `npm run build:css` before serving anything; `app/static/app.css` is a gitignored build
  artifact, so `/static/app.css` 404s until you build it. That is expected, not a bug.

## Design reference

`Auctions4America.pen` on the canvas, and the 2× renders in `pen-exports/`:

| file | screen |
|---|---|
| `b3I3tf.png` | Today — shows the shell in context |
| `BWsLw.png` | Compose |
| `ezUfo.png` | Prospects (**deferred — ignore the content, but the shell is visible**) |
| `j98DI.png` | Contacts |

Match the shell: proportions, spacing, type scale, the pinned footer. You are not
building the page content in those shots — 3b and 4 own that.

## Scope

### 1. The shell

Left sidebar, 216px, `bg-surface`, hairline right border.

- **Brand block** — 30px rounded mark in `bg-brand` with the short name, then the full
  brand name and "Text platform" beneath. All from `brand.*`, never a literal.
- **Grouped nav** with small uppercase section captions in `text-ink-3`.
- **Pinned footer** — "Segments this month" with the live count, and "Your number" with
  the sender number in the mono face. Pushed to the bottom by a flex spacer.

Top bar: page title, a status pill, a slot for the page's primary action, right-aligned.

### 2. The nav — ship only what exists

The Pencil design shows eight items. Four of those screens don't exist yet, and a nav
item that 404s is worse than an absent one. For launch:

```
SEND       Today            /dashboard      (label it "Today", keep the route)
           Compose          /campaigns
AUDIENCE   Contacts         /contacts
           Opt-outs         /blocklist
ACCOUNT    Usage & billing  /usage
           Settings         /settings
```

**Dropped for now, with a comment in the template saying why and what restores them:**
History and Categories (screens deferred to module 8), and **Prospects — the whole
prospecting engine is deferred**, so no badge, no nav entry, no placeholder route.

Active state via `aria-current="page"` plus `bg-brand-soft`, driven by the existing
`active_page` context variable.

### 3. Template blocks

Give child templates a clean contract, and document it in a comment at the top:

- `{% block title %}` — the top-bar heading
- `{% block page_actions %}` — top-bar right slot (buttons)
- `{% block content %}` — the page body
- `{% block head %}` / `{% block scripts %}` — keep the existing names

Sessions 3b and 4 write against this contract in parallel. If you change a block name
later, both break. Get it right now.

### 4. Every existing page must keep rendering

`dashboard.html`, `contacts.html`, `campaigns.html`, `blocklist.html`, `settings.html`
and `usage.html` all extend this file. They still carry the skeleton's light
`bg-white`/`text-gray-*` classes and will look like light cards on a dark page. **That is
expected and not yours to fix** — 3b, 4 and module 8 own those templates.

What you must guarantee: every one of them still returns 200 and is usable. If a block
rename would break one, either keep the old name or update the child's block declaration
only — do not restyle it.

### 5. Responsive and accessible

- Below ~760px the sidebar collapses to a top bar with a toggle. No horizontal scroll.
- The existing hamburger button is icon-only with no `aria-label` — fix that.
- A skip-to-content link, visible on focus.
- Visible focus rings on every interactive element; the token set has the colours.
- `aria-current="page"` on the active nav item.

### 6. `pages.py`

Only what the shell needs: make sure every route passes `active_page`, and supply the
footer's two values (segments this month, sender number) through a small context helper
rather than duplicating the query in six handlers.

The sender number is displayed **partially masked** (`+1 954 ••• 4120`). It is his
number, not the carrier's, so it is fine to show — but no carrier name anywhere near it.

## Out of scope

- `today.html`, the Contacts redesign, `dashboard_service` — session 3b
- `campaigns.html` and the composer — session 4
- Retiring the uncategorised import endpoints — session 3b owns that
- Any screen for Categories, History or Prospects
- Restyling the child templates

## Acceptance criteria (demonstrate each in the transcript)

- [ ] `bash agent/gate.sh` green at start (76 tests) and at end — output shown both times
- [ ] Suite run twice in a row, green both times
- [ ] `curl -s -o /dev/null -w "%{http_code}"` returns **200** for `/dashboard`,
      `/campaigns`, `/contacts`, `/blocklist`, `/usage`, `/settings` — all six shown
- [ ] Screenshot of the shell at 1440px beside `pen-exports/b3I3tf.png`
- [ ] Screenshot at 375px showing the collapsed sidebar and no horizontal scroll
- [ ] `grep -n "aria-current\|aria-label\|skip" app/templates/base.html` shows all three
- [ ] No nav item points at a route that 404s — demonstrate by curling every `href`
- [ ] No file over 500 lines
- [ ] `status.md` and `handoff.md` updated

## Constraints

- Touch only `app/templates/base.html` and `app/routers/pages.py`. A child template's
  `{% block %}` declaration may be edited **only** if a rename forces it.
- `agent/gate.sh` and `agent.config.sh` are human-only.
- No brand hex, price, allowance or carrier name in any template.
- Do not build a class name from a template variable. `bg-{{ brand.color }}-600` is the
  bug that made module 1 necessary — the compiler purges anything it can't see as a
  literal. Use the token utilities.
- `--s1`…`--s4` are the validated category colours. Four hues is the proven ceiling. Do
  not add a fifth or adjust them by eye.
