# Design reference

The UI design is authored in **Pencil**, in `Auctions4America.pen` one level above this
repo. These files are the durable exports of it, kept in-repo so a coding session can
actually look at the design rather than working from prose.

Sessions 3a, 3b and 4 all cited `pen-exports/*.png` and found nothing, because the
renders were outside the repo. That is why this folder exists.

| file | what it is |
|---|---|
| `b3I3tf.png` | Today — 2× render |
| `BWsLw.png` | Compose — 2× render |
| `j98DI.png` | Contacts — 2× render |
| `lCcJx.png` | Today, earlier light-mode variant — historical |
| `ezUfo.png` | Prospects — **deferred feature**, useful only for the shell |
| `screens.html` | Today, Compose and Contacts exported as HTML + Tailwind |

`screens.html` is the more useful one when you're implementing: it carries the real
spacing, type scale and structure, not just pixels. It is an export, not the app — do not
copy it in wholesale. Read it, then build against the token utilities.

## Keeping these honest

If the build and the design diverge, update the Pencil document too and re-export. A
design file that contradicts the code is worse than no design file.

The `.pen` file on disk is **not** currently the design — Pencil holds the live document
and has not written it back, so the file is still an empty stub. These exports are the
only durable copy. Save from within Pencil before relying on the `.pen`.
