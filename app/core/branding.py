"""Brand context for templates.

Jinja templates never hardcode a client name or a client color. They read
`brand.*`, which is injected globally here, so rebranding the whole dashboard is
an .env edit.
"""

import re

from app.core.config import settings

# Matches --brand / --accent for the dark (default) theme in
# app/assets/tailwind.css. Kept in sync by hand — there is no build step that
# reads the stylesheet, so if a token changes there, change it here.
DEFAULT_BRAND_HEX = "#3987E5"
DEFAULT_ACCENT_HEX = "#C98500"

_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _valid_hex(value: str) -> bool:
    return bool(_HEX.match((value or "").strip()))


def _hex_or_default(value: str, default: str) -> str:
    """A malformed hex falls back rather than raising.

    A typo in .env should cost the client a default-colored button, not a
    dashboard that will not boot at 6am before a sale.
    """
    value = (value or "").strip()
    return value if _valid_hex(value) else default


def brand_context() -> dict:
    color_hex = _hex_or_default(settings.BRAND_COLOR_HEX, DEFAULT_BRAND_HEX)
    accent_hex = _hex_or_default(settings.BRAND_ACCENT_HEX, DEFAULT_ACCENT_HEX)

    return {
        "name": settings.BRAND_NAME,
        "short_name": settings.BRAND_SHORT_NAME,
        "app_name": settings.BRAND_APP_NAME,
        "support_phone": settings.BRAND_SUPPORT_PHONE,
        "support_email": settings.BRAND_SUPPORT_EMAIL,
        "color_hex": color_hex,
        "accent_hex": accent_hex,
        # Whether to emit the inline override at all. With nothing configured we
        # want the stylesheet's own per-theme values, which are contrast-checked
        # against both the dark and the light surfaces; one .env hex cannot be
        # correct for both, so it should only take effect when someone actually
        # chose it.
        "has_custom_palette": (
            _valid_hex(settings.BRAND_COLOR_HEX) or _valid_hex(settings.BRAND_ACCENT_HEX)
        ),
    }


def install(templates):
    """Attach brand + a few feature flags to every template render."""
    templates.env.globals["brand"] = brand_context()
    templates.env.globals["billing_enabled"] = settings.BILLING_ENABLED
