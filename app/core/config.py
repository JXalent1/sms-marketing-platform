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

    # The most screening may spend in a calendar month, in dollars.
    #
    # This is not a tidiness measure. **Lookups and sends draw on the same
    # carrier balance**, so a 10,000-number screening run takes $25 out of the
    # pot `capacity_assessment()` measures, and the failure mode is an overnight
    # scrape making the next morning's campaign refuse to start with nothing on
    # any screen connecting the two events. The default is deliberately low: the
    # cheap error is a queue that stops filling, and the expensive one is a
    # silent transfer from the sending budget to the lookup budget.
    #
    # Zero — or a negative value — switches screening off entirely rather than
    # meaning "unlimited". A guard that is off refuses; it does not wave things
    # through, which is the same rule that keeps `unknown` unpromotable.
    PROSPECT_LOOKUP_MONTHLY_CAP: float = 50.0

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
    # `marine` and `seashells` are national and are listed for that reason. The
    # plan of record has called both national since it was written, and neither
    # had a row here — so both fell through to the 150-mile default, and the two
    # categories that most need to run nationally would have run as regional
    # searches with nothing on any screen saying so. See `decisions/009`.
    PROSPECT_CATEGORY_RADIUS_MILES: dict[str, int | None] = {
        "food_service": 150,
        "equipment": 150,
        "general": 150,
        "estates": 100,
        "memorabilia": None,
        "marine": None,
        "seashells": None,
    }
    PROSPECT_DEFAULT_RADIUS_MILES: int = 150

    # Where "can they collect it" is measured from: the auction house itself.
    # A radius is meaningless without a centre, and a source that guessed one
    # would be deciding the client's market in code.
    PROSPECT_ORIGIN_LAT: float = 26.1224          # Fort Lauderdale
    PROSPECT_ORIGIN_LON: float = -80.1373

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

    # ─── Google Places (the first discovery source) ─────────────────────────
    # Empty means the source refuses to run rather than running degraded. There
    # is no useful half-configured state: without a key there is nothing to
    # search, and a source that answered "found nothing" would be indistinguish-
    # able from a niche that really is empty. The line-type gate can degrade
    # safely because `unknown` promotes nobody; this cannot.
    GOOGLE_PLACES_API_KEY: str = ""

    # **A hard monthly ceiling on requests, checked before every call.** The
    # second of the two independent meters — this one counts Google requests and
    # the other counts carrier lookups, and they are separate because they are
    # separate bills. Text Search is $35 per 1,000 requests with the first 1,000
    # of a calendar month free, so the default is exactly the free allowance:
    # the first month of a niche costs nothing, and going past it is a number
    # somebody raised on purpose.
    #
    # Zero or less switches the source off rather than meaning unlimited, the
    # same reading `PROSPECT_LOOKUP_MONTHLY_CAP` has. A guard that is off
    # refuses.
    GOOGLE_PLACES_MONTHLY_REQUEST_CAP: int = 1000
    GOOGLE_PLACES_FREE_REQUESTS_PER_MONTH: int = 1000
    GOOGLE_PLACES_COST_PER_1000_REQUESTS: float = 35.0

    # Twenty places per request is the API's own maximum, and three pages is
    # sixty businesses per search term — past that a text search is returning
    # progressively less relevant matches for three cents a page.
    GOOGLE_PLACES_PAGE_SIZE: int = 20
    GOOGLE_PLACES_MAX_PAGES: int = 3
    GOOGLE_PLACES_TIMEOUT_SECONDS: int = 20

    # How long a search term's results are treated as still true. A re-run
    # inside this window makes no request at all: the same query returns the
    # same sixty businesses, we already hold every one of them, and paying for
    # them again buys nothing. This is what makes "a second identical run costs
    # nothing" true of the Google meter as well as the carrier one.
    GOOGLE_PLACES_QUERY_REPEAT_DAYS: int = 30

    # ─── Stripe (the payment processor, not the SMS carrier) ────────────────
    # The white-label rule covers the carrier. Stripe is deliberately NOT
    # scrubbed: it appears on the client's card statement, it renders the
    # checkout page he types his card into, and hiding it would break the one
    # flow this configuration exists for. See sessions/session-B1.md A8.
    #
    # Every value below is blank by default and blank is a *supported* state,
    # not a broken one — `/subscribe` says the plan is not connected yet and
    # the checkout endpoint answers 503. The page is safe to deploy before the
    # Stripe account exists, which is the order these two things happen in.
    STRIPE_SECRET_KEY: str = ""

    # The graduated tiered usage price: tier 1 is `BILLING_SEGMENTS_INCLUDED`
    # units at $0, tier 2 is `BILLING_PRICE_PER_SEGMENT` per unit thereafter.
    #
    # **The allowance lives in the tier, and therefore in two systems.** This
    # repo holds one copy and Stripe's dashboard holds the other, under nobody's
    # version control. `stripe_tiers.check_tier_drift()` exists to make them
    # prove they still agree, because a mispriced invoice is the one artefact
    # the client audits. Two definitions of one number is the defect this
    # codebase has hit four times.
    STRIPE_PRICE_METERED: str = ""

    # A one-time price for the outstanding balance, charged on the first
    # invoice as a second line item on the same Checkout Session.
    #
    # A line item and not `subscription_data.add_invoice_items`: that parameter
    # does not exist on `checkout.Session.create` in the pinned SDK — it is a
    # Subscription and SubscriptionSchedule parameter and always has been. See
    # `tests/test_stripe_contract.py`, which asserts the shape rather than
    # trusting this comment.
    #
    # Blank is supported: without it checkout still opens, still takes a card
    # and still starts the meter — it simply settles nothing. That is the right
    # degradation, because the balance is a one-off and the metering is not.
    STRIPE_PRICE_BALANCE: str = ""

    # The meter's event name, from Billing -> Meters. Sum aggregation, customer
    # mapping `stripe_customer_id`, value key `value`.
    STRIPE_METER_EVENT_NAME: str = "sms_segments"

    # How long after a send a `sent` row is treated as settled — its status no
    # longer able to move out of the billable set — and therefore metered.
    # A `delivered` row settles at once (`SETTLED_STATUSES` in the model); this
    # window is for the receipt that never arrives, so it cannot hold billing
    # open forever. Hours, and 24 of them: on this account's traffic the
    # carrier's verdict comes back in seconds to minutes (the failure corpus is
    # "not routable" rejections), so a day covers every receipt that is going
    # to arrive, while keeping the last day of a cycle within reach of the
    # invoice Stripe finalises shortly after the cycle closes. The event is
    # stamped with the *send* time, so metering a day late does not move usage
    # into the next cycle — see `stripe_meter`.
    #
    # Which way to err: a row metered before a late receipt flips it is half a
    # cent over-billed, in our favour, and `decisions/011` calls that the
    # serious direction. Lengthen this before shortening it.
    BILLING_SETTLE_HOURS: int = 24

    # `whsec_…` from Developers -> Webhooks. **Unset means every webhook payload
    # is ignored**, not trusted: an unsigned event is an event anybody on the
    # internet can post, and what it would write is the customer id we meter
    # this client's segments against. A guard that is switched off refuses.
    STRIPE_WEBHOOK_SECRET: str = ""

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
