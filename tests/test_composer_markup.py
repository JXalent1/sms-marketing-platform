"""The composer builds markup in JavaScript, and a list name is client-typed.

Session 5j's rename box means the client chooses the strings the composer's
dropdown and its list panel interpolate. `esc()` in `base.html` escapes `<`, `>`
and `&` — correct between tags, and **not enough inside a quoted attribute**,
where a double quote closes the attribute and everything after it is parsed as
markup. A list named `Bob"s buyers onmouseover=...` would have done it.

So attribute position uses `attr()`, which adds `&quot;`. This is a shape test
over the template source because the markup is assembled in the browser: there
is nothing rendered server-side to assert on, and the same reasoning already
governs the "class names are literals in a lookup table" rule in the same file.

`test_the_sweep_actually_fires` is here for CLAUDE.md's reason: a green check
whose matcher is broken is worse than no check, because it gets quoted.
"""

import pathlib
import re

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "app" / "templates"
COMPOSER = ("_composer-script.html", "_composer-upload.html",
            "_composer-link.html", "_composer-lists.html", "campaigns.html")

# `attribute="${esc(...)}"` — the text escaper used where an attribute quote can
# be closed. Deliberately narrow: it matches the interpolation *inside* a
# double-quoted attribute value and nothing else.
ESC_IN_ATTRIBUTE = re.compile(r'=\s*"[^"\n]*\$\{\s*esc\s*\(')


def test_no_composer_template_escapes_an_attribute_with_the_text_escaper():
    offenders = []
    for name in COMPOSER:
        text = (TEMPLATES / name).read_text()
        for number, line in enumerate(text.splitlines(), 1):
            if ESC_IN_ATTRIBUTE.search(line):
                offenders.append(f"{name}:{number}: {line.strip()}")
    assert not offenders, (
        "esc() escapes < > & and not the quote that ends an attribute; use "
        "attr() there:\n  " + "\n  ".join(offenders))


def test_the_sweep_actually_fires():
    """Against a case whose answer is known, before it is quoted as evidence."""
    assert ESC_IN_ATTRIBUTE.search('<option value="${esc(a.selector)}">')
    assert ESC_IN_ATTRIBUTE.search('<input value="${ esc(row.label) }" >')
    # Text position is fine and must not be flagged.
    assert not ESC_IN_ATTRIBUTE.search('<td>${esc(row.label)}</td>')
    assert not ESC_IN_ATTRIBUTE.search('<option value="${attr(a.selector)}">')


def test_the_attribute_escaper_exists_and_covers_the_quote():
    """It is defined once, in the partial every other one is included after."""
    source = (TEMPLATES / "_composer-script.html").read_text()
    assert "const attr = (value) =>" in source
    assert "&quot;" in source, "attr() does not escape the character it exists for"
