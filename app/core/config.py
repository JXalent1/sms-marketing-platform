"""Application settings.

Everything that differs between clients lives here and is driven by the .env
file — brand strings, the SMS provider and its credentials, the pricing plan,
and the admin login. Nothing client-specific should be hardcoded anywhere else
in the codebase; if you find yourself typing a client's name into a .py or
.html file, add a setting here instead.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ─── Brand / white-label ────────────────────────────────────────────────
    # Used in page titles, nav, the login screen, auto-replies and opt-out copy.
    BRAND_NAME: str = "Example Company"          # full legal-ish name, used in SMS copy
    BRAND_SHORT_NAME: str = "Example"            # short name for the nav bar
    BRAND_APP_NAME: str = "Marketing Bot"        # what the dashboard calls itself
    BRAND_SUPPORT_PHONE: str = ""                # shown in the default auto-reply
    BRAND_SUPPORT_EMAIL: str = ""
    # Brand palette as literal hex, not a Tailwind color name. The old
    # BRAND_COLOR fed `bg-{{ brand.color }}-600`, which only ever worked because
    # the Play CDN compiled classes in the browser: once Tailwind is compiled at
    # build time that class is purged and the brand color silently disappears.
    # Hex also means a client's actual brand works, instead of the nearest
    # Tailwind name. Blank = use the validated defaults from the design file.
    BRAND_COLOR_HEX: str = ""                    # "#RRGGBB"
    BRAND_ACCENT_HEX: str = ""                   # "#RRGGBB"

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
    # OUR blended wholesale rate — what we pay the carrier, not what the client
    # pays us. It exists only to convert a carrier balance into a capacity
    # estimate for the pre-flight check.
    #
    # It must never reach a response body, a template, or anything the client
    # can read, directly or as a figure derived from it. It was named
    # PREFLIGHT_COST_PER_SEGMENT, and under that name it was quietly used as
    # "the cost of a campaign" in three places on the client's screen: it
    # disclosed our margin and under-stated his bill by ~40%, because he is
    # billed at BILLING_PRICE_PER_SEGMENT below. Any client-facing money figure
    # comes from billing_service, which reads that setting and only that one.
    WHOLESALE_COST_PER_SEGMENT: float = 0.009

    # ─── Pricing plan (what YOU bill the client) ────────────────────────────
    # Kept as config, not constants in code, because these get renegotiated.
    # The plan is: a monthly fee, an allowance of included segments, and a flat
    # rate for every segment beyond it. No tiers — the tier table this replaced
    # was three numbers that had to agree across a template, a config string and
    # a Python function, and they did not.
    BILLING_ENABLED: bool = True
    BILLING_CYCLE_DAY: int = 1                   # day of month the allowance resets
    BILLING_MONTHLY_FEE: float = 0.0
    BILLING_SEGMENTS_INCLUDED: int = 10000
    BILLING_PRICE_PER_SEGMENT: float = 0.015

    # ─── Dashboard ──────────────────────────────────────────────────────────
    # A category card turns red past this many days without a send. Config, not
    # a constant, because "stale" is a judgement about his auction calendar: a
    # house that runs food service weekly wants a tighter number than one that
    # runs estates twice a quarter.
    DASHBOARD_STALE_DAYS: int = 14

    # ─── Composer guardrails ────────────────────────────────────────────────
    # A contact who was texted inside this window is skipped by the next
    # campaign, whatever category it is for. Across categories on purpose: a
    # buyer tagged Food Service, Equipment and Estates would otherwise collect
    # three texts in a week from three perfectly correct campaigns, and it is
    # the person who unsubscribes, not the category.
    RECENT_CONTACT_SUPPRESSION_DAYS: int = 3

    # Segments per message above which pre-flight warns. Not a refusal — a long
    # message is a legitimate choice, it just costs a multiple of a short one
    # and that should be a decision rather than a surprise.
    PREFLIGHT_SEGMENT_CEILING: int = 3

    # Words that belong to a niche, keyed by category slug. Pre-flight warns
    # when the body carries another category's vocabulary — the copy-paste
    # mistake ("last night's fryer text, sent to the memorabilia list") is the
    # single most likely way this platform sends the wrong thing to the wrong
    # people, and a keyword table catches it for nothing.
    #
    # Config, not code, because the niches are the client's and he will add to
    # them. Override in .env with a JSON object under the same key.
    CATEGORY_KEYWORDS: dict[str, list[str]] = {
        "food_service": ["fryer", "walk-in", "hood", "griddle", "range",
                         "dishwasher", "prep table", "reach-in", "steam table"],
        "equipment": ["lathe", "welder", "drill press", "forklift", "compressor",
                      "skid steer", "excavator", "generator", "mill"],
        "estates": ["estate", "antique", "china cabinet", "sterling", "armoire",
                    "heirloom", "silverware"],
        "memorabilia": ["memorabilia", "autograph", "autographed", "signed",
                        "trading card", "rookie", "collectible", "vintage poster"],
    }

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

    def webhook_url(self, provider: str) -> str:
        return f"{self.PUBLIC_BASE_URL.rstrip('/')}/webhooks/{provider}"


settings = Settings()
