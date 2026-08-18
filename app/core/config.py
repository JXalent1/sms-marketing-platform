"""Application settings.

Everything that differs between clients lives here and is driven by the .env
file — brand strings, the SMS provider and its credentials, the pricing plan,
and the admin login. Nothing client-specific should be hardcoded anywhere else
in the codebase; if you find yourself typing a client's name into a .py or
.html file, add a setting here instead.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Tuple


class Settings(BaseSettings):
    # ─── Brand / white-label ────────────────────────────────────────────────
    # Used in page titles, nav, the login screen, auto-replies and opt-out copy.
    BRAND_NAME: str = "Example Company"          # full legal-ish name, used in SMS copy
    BRAND_SHORT_NAME: str = "Example"            # short name for the nav bar
    BRAND_APP_NAME: str = "Marketing Bot"        # what the dashboard calls itself
    BRAND_SUPPORT_PHONE: str = ""                # shown in the default auto-reply
    BRAND_SUPPORT_EMAIL: str = ""
    BRAND_COLOR: str = "blue"                    # any Tailwind color name (blue/emerald/rose/...)

    # ─── Admin auth ─────────────────────────────────────────────────────────
    # Set ADMIN_PASSWORD_HASH in production (generate with scripts/hash_password.py).
    # ADMIN_PASSWORD is a convenience for local dev only — it is hashed at startup.
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = ""
    ADMIN_PASSWORD_HASH: str = ""
    SECRET_KEY: str = ""                         # signs the session cookie — MUST be set
    SESSION_COOKIE_NAME: str = "session"
    SESSION_DAYS: int = 7
    COOKIE_SECURE: bool = True                   # set False only for local http:// dev

    # ─── SMS provider ───────────────────────────────────────────────────────
    # "console" writes messages to the log instead of sending them. Use it for
    # all local development so a stray test never costs money or hits a real phone.
    SMS_PROVIDER: str = "console"                # console | telnyx | twilio

    TELNYX_API_KEY: str = ""
    TELNYX_PHONE_NUMBER: str = ""
    TELNYX_MESSAGING_PROFILE_ID: str = ""

    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_PHONE_NUMBER: str = ""

    # ─── Sending behaviour ──────────────────────────────────────────────────
    SEND_DELAY_SECONDS: float = 0.15             # pause between messages in a campaign
    # Skip numbers outside the region your 10DLC/messaging profile is approved for.
    # These are guaranteed-undeliverable, so attempting them just burns money.
    SKIP_NON_US_NUMBERS: bool = True
    # Abort a campaign before the first send if the provider balance can't cover it.
    # This is the single most valuable guard in the system — see docs/SMS_LESSONS.md.
    PREFLIGHT_BALANCE_CHECK: bool = True
    PREFLIGHT_COST_PER_SEGMENT: float = 0.009    # your blended carrier rate, for the estimate

    # ─── Pricing plan (what YOU bill the client) ────────────────────────────
    # Kept as config, not constants in code, because these get renegotiated.
    # Tiers are "size:rate" pairs applied in order to segments over the allowance;
    # the last tier should be "inf" to catch everything above.
    BILLING_ENABLED: bool = True
    BILLING_CYCLE_DAY: int = 1                   # day of month the allowance resets
    BILLING_BASE_FEE: float = 400.0
    BILLING_INCLUDED_SEGMENTS: int = 15000
    BILLING_OVERAGE_TIERS: str = "5000:0.025,10000:0.022,20000:0.020,inf:0.016"

    # ─── Alerting ───────────────────────────────────────────────────────────
    ALERT_PHONE: str = ""                        # your number, for balance/scrape alerts
    BALANCE_ALERT_THRESHOLD: float = 50.0

    # ─── Infrastructure ─────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./data/app.db"
    PUBLIC_BASE_URL: str = "http://localhost:8000"   # used to build webhook URLs
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",          # tolerate stale keys left in a client's .env
    )

    # ─── Derived helpers ────────────────────────────────────────────────────

    def overage_tiers(self) -> List[Tuple[float, float]]:
        """Parse BILLING_OVERAGE_TIERS into [(tier_size, rate), ...]."""
        tiers: List[Tuple[float, float]] = []
        for part in self.BILLING_OVERAGE_TIERS.split(","):
            part = part.strip()
            if not part:
                continue
            size_str, rate_str = part.split(":")
            size = float("inf") if size_str.strip().lower() in ("inf", "*") else float(size_str)
            tiers.append((size, float(rate_str)))
        return tiers

    def webhook_url(self, provider: str) -> str:
        return f"{self.PUBLIC_BASE_URL.rstrip('/')}/webhooks/{provider}"


settings = Settings()
