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

    # ─── Short links ────────────────────────────────────────────────────────
    # The dedicated short domain, e.g. "a4a.bz". Decided 2026-08-24: a short
    # domain of its own rather than a subdomain of the main site, because
    # "a4a.bz/a7k9x2pq" is 16 characters against "go.auctions4america.com/..."
    # at 32, and on a tight message that difference is a whole second segment.
    #
    # Blank is a supported state, not a broken one: the domain may not be
    # registered when this ships. Blank means the composer *refuses* the merge
    # tag with a message naming what is missing — it never mints a link nobody
    # can follow. See app/services/link_service.py.
    SHORT_LINK_DOMAIN: str = ""

    # Whether the rendered link carries "https://". Off by default because the
    # 2026-08-24 costing compared bare domains, and handsets linkify a bare
    # domain with a known TLD. One setting rather than an edit if a carrier or a
    # handset in this client's audience turns out not to.
    SHORT_LINK_INCLUDE_SCHEME: bool = False

    # A click arriving sooner than this after the carrier accepted the message
    # is treated as a scanner rather than a person. Nobody reads a text, unlocks
    # a handset and taps a link in three seconds; the carrier's own URL scanner
    # does it in under one. Set to 0 to switch the timing rule off entirely.
    CLICK_MIN_HUMAN_SECONDS: int = 8

    # ─── Prospecting ────────────────────────────────────────────────────────
    # Which line-type provider screens scraped numbers. "none" is the default
    # and makes no calls at all — see app/sms/lookup.py for why the carrier
    # implementation is not shipped here. An unscreened number reads as
    # `unknown`, and `unknown` cannot be promoted, so a box with this unset
    # holds prospects in the queue rather than promoting landlines.
    PROSPECT_LOOKUP_PROVIDER: str = "none"

    # What ONE line-type lookup costs us, for the job cost record. This is our
    # spend, on the same footing as WHOLESALE_COST_PER_SEGMENT: the client is
    # billed per segment and for nothing else, and screening appears on no
    # invoice of his. It must never reach a response body, a template or an
    # export — agent/accept-P1.sh asserts that structurally.
    PROSPECT_LOOKUP_COST_PER_NUMBER: float = 0.0025

    # "Can they collect it." Per category, because a walk-in cooler is a
    # 150-mile decision and a signed rookie card is a national one.
    #
    # A slug mapped to null is national — distance does not constrain it, and a
    # dealer in Oregon scores the same as one in Broward. A slug absent from
    # this map falls back to PROSPECT_DEFAULT_RADIUS_MILES, which is the more
    # conservative of the two, because silently treating an unconfigured
    # category as national is how a food-truck search starts returning Seattle.
    #
    # Config, not code, and the plan of record says to move them once there is
    # response data to move them with — not before.
    PROSPECT_CATEGORY_RADIUS_MILES: dict[str, int | None] = {
        "food_service": 150,
        "equipment": 150,
        "general": 150,
        "estates": 100,
        "memorabilia": None,
    }
    PROSPECT_DEFAULT_RADIUS_MILES: int = 150

    # A scrape job that has not finished in this long is abandoned, its cleanup
    # is run and it is recorded `timed_out`. Minutes, not hours: the box has
    # 2 GB, and the failure mode this exists to prevent is a run that holds a
    # browser process open until something else on the box dies.
    PROSPECT_JOB_TIMEOUT_SECONDS: int = 300

    # When a search term's rejections skew to seller_or_consignor/competitor,
    # the term is finding the wrong side of the room and should be retired.
    # Two numbers, because a share is meaningless on a small sample: one term
    # with a single seller rejection is not evidence of anything. The minimum
    # counts rejections, not prospects, because that is the denominator of the
    # share it guards.
    PROSPECT_TERM_FLAG_MIN_REJECTIONS: int = 5
    PROSPECT_TERM_FLAG_SHARE: float = 0.5

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
