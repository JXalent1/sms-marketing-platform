# Handoff

_Last updated: 2026-08-18_

## What just happened
Planning and design phase. Produced `A4A_BUILD_PLAN.md`, the Pencil UI
(`Auctions4America.pen`, five screens, dark), `CLAUDE.md` and `modules.md`.

## State of the code
Untouched skeleton at `sms-marketing-platform/`. `python -m pytest tests/ -q` → 22 passed.
No git repo yet — module 1 creates it.

## What the next session needs to know
- Read `CLAUDE.md` first, then `sessions/session-1.md`.
- The front-end currently loads Tailwind from the Play CDN at runtime. With no network
  the app renders as raw unstyled HTML. Module 1 fixes this before anything is built on top.
- `bg-{{ brand.color }}-600` in `base.html` only works because of that CDN. Compiling
  Tailwind will purge those classes and silently drop the brand color unless it moves to
  CSS variables first.
- Dark is the default theme, not an option.

## Open decisions
See "Blocked on" in `status.md`.
