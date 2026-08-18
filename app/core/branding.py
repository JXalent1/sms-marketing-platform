"""Brand context for templates.

Jinja templates never hardcode a client name. They read `brand.*`, which is
injected globally here, so rebranding the whole dashboard is an .env edit.
"""

from app.core.config import settings


def brand_context() -> dict:
    return {
        "name": settings.BRAND_NAME,
        "short_name": settings.BRAND_SHORT_NAME,
        "app_name": settings.BRAND_APP_NAME,
        "support_phone": settings.BRAND_SUPPORT_PHONE,
        "support_email": settings.BRAND_SUPPORT_EMAIL,
        "color": settings.BRAND_COLOR,
    }


def install(templates):
    """Attach brand + a few feature flags to every template render."""
    templates.env.globals["brand"] = brand_context()
    templates.env.globals["billing_enabled"] = settings.BILLING_ENABLED
