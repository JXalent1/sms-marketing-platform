# Session 5k — close the attribute-escaping class

**Module:** 5k · **Depends on:** 5j · **Parallel-safe with:** P2 (file sets are disjoint)

Small and narrow on purpose. One class of bug, closed everywhere rather than where it was
noticed.

## Prerequisites

- Read `CLAUDE.md` (the `esc()` / `attr()` entry), `RULES.md`, `status.md`.
- 5j is merged. **613 tests, gate green twice.**
- Run inside the project venv. `npm run build:css` before serving anything.

## Why

`esc()` in `base.html` escapes through `textContent`/`innerHTML`, which covers `<`, `>`
and `&` and **not the double quote that ends an attribute**. So `value="${esc(name)}"`
with a value containing `"` closes the attribute and everything after it parses as markup.

5j made this reachable — the client can now type a list name — and fixed it *in the
composer*: `attr()` in `_composer-script.html`, and `tests/test_composer_markup.py`
sweeping the composer templates for the shape. The other templates were outside 5j's file
list, so three sites survive:

| Site | Interpolates | Where that comes from |
|---|---|---|
| `blocklist.html:116` | `n.notes` | **`blocked_numbers.notes` — carrier free text, written by the delivery webhook at thousands of rows a campaign** |
| `contact-history.html:86` | `m.message` | the rendered message, which carries a contact name from a client CSV |
| `settings.html:145` | `d.send_mode` | an internal enum |

The first is why this session exists ahead of its size. That column is populated from
outside the system entirely — we do not author it, the carrier does — and
`scrub_provider_text()` removes carrier names from it, not markup.

This is `CLAUDE.md`'s "a guard with one call site is a guard on one path", arriving for the
third time in this project.

## Scope

1. **Move `attr()` to `base.html`, beside `esc()`**, and delete the local copy in
   `_composer-script.html`. One definition. Give it the comment that says what `esc()`
   does not do and why this exists — a helper whose whole purpose is one character needs
   its reason next to it or it gets "simplified" back.
2. **Use it at the three sites above.**
3. **Widen `tests/test_composer_markup.py`'s sweep from the composer list to every
   template under `app/templates/`,** and rename the module to match what it now covers.
   Keep `test_the_sweep_actually_fires` — the positive control that runs the matcher
   against a known-bad string before the sweep is quoted as evidence.

## Out of scope

- Any other escaping question: server-side Jinja autoescaping, the CSV export, the short-link
  renderer. This session closes one shape in one place.
- Changing `esc()` itself. Making it escape quotes would be a wider blast radius than this
  session can verify, and the two helpers reading differently at the call site is the point.
- Anything in `app/services/`, `app/routers/` or a migration. **No schema change at all.**

## File list

```
app/templates/base.html
app/templates/_composer-script.html
app/templates/blocklist.html
app/templates/contact-history.html
app/templates/settings.html
tests/test_composer_markup.py   (renamed to tests/test_attribute_escaping.py)
agent/accept-5k.sh
```

## Acceptance criteria

`agent/accept-5k.sh` is the stop condition. Each criterion runs its tests in isolation.

1. **The sweep covers every template**, discovered from the directory rather than a
   hard-coded list, and finds no `="${esc(...)}"` anywhere.
2. **The sweep fires.** Point the matcher at a known-bad string, and — separately —
   reintroduce the shape in a scratch copy of one template and confirm the sweep goes red.
   A green check whose matcher is broken is worse than no check.
3. **`attr()` has exactly one definition**, in `base.html`, asserted by sweeping the
   templates for a second one. Two copies of a one-character escaper is how they come to
   disagree.
4. **A quote in a list name survives a round trip.** Create a list named `Bob" onmouseover=x`
   through the API, render `/campaigns`, and assert the rendered page carries `&quot;` in
   the option's value and no unescaped `onmouseover` attribute. This is the behavioural
   half — the sweep is a shape test and a shape test cannot see a site written a different
   way.
5. **`bash agent/gate.sh` green, twice.**

No mutation harness this time. The session changes no behaviour a mutation could revert
meaningfully — criterion 2's reintroduce-and-confirm is the same proof at this size, and
`CLAUDE.md`'s rule is that a mutation must be on a path something executes.

## `/goal`

> Session 5k is complete when `bash agent/accept-5k.sh` exits 0 with every criterion
> printed and passing and `bash agent/gate.sh` is green on two consecutive runs. Turn cap
> 25. Show the output.

## Review

One synchronous fresh-context review. Two lenses only, matching the size:

1. **Every path the condition arrives on.** The sweep looks for `="${esc(...)}"`. What
   about a single-quoted attribute, a backtick-nested template literal, or `setAttribute`
   with an unescaped value? Say what the sweep cannot see, in the module docstring, rather
   than implying it covers more than it does.
2. **The scan's own first version.** Criterion 2, run before the result is quoted.
