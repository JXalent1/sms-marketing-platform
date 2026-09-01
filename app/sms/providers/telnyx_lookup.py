"""Line-type lookup against the carrier's number-lookup API.

About $0.0025 a number, and the entire economic premise of scraping: 2,526 of
one campaign's failures on this client's own list were not-routable numbers —
39% of a 6,857-message send, every one paid for, every one still on the list the
next morning. Screening 10,000 scraped businesses costs $25 **once**, and the
cache in `app/services/lookup_service.py` is what keeps it once.

## What this class is not responsible for

It makes one call and answers one question. It does **not** decide whether it is
allowed to spend: lookups and sends draw on the same carrier balance, so an
overnight screening run can fail the next morning's campaign pre-flight, and the
cap that stops that has to read what has already been spent this month. That is
a database question and this file is under `app/sms/`, which imports nothing
from the DB layer. The cap lives in `lookup_service.py`, in front of every call
site, and this class never sees it.

## An answer we cannot act on is still an answer

The carrier's vocabulary is wider than ours and most of it maps to `unknown`.
That is not a failure, and the difference matters more than it looks: an
**answer** is cached forever and never paid for again, while a **failure** stays
retryable. So a number the carrier classifies as `voicemail` or as
`fixed line or mobile` comes back `ok=True, line_type="unknown"` — we asked,
we were told, the answer does not get us a text through, and asking again next
month cannot change it. `unknown` is not promote-eligible, so the number waits
in the review queue rather than being promoted on a guess.

`fixed line or mobile` is the one worth naming. It is genuinely ambiguous, and
the errors are asymmetric in the way this codebase keeps rediscovering: a mobile
wrongly held back waits in a queue, and a landline wrongly promoted is paid for
on every send from now on. Make the cheap error.

The one case that is **not** an answer is a response carrying no line type at
all. That is a shape we do not understand — schema drift, or our own bug — and
freezing it into the cache as `unknown` forever is exactly the permanent hole
`lookup_service`'s module docstring exists to prevent. It records the spend,
reports `ok=False`, and stays retryable.
"""

from app.core.config import settings
from app.sms.lookup import LineTypeProvider, LineTypeResult, cost_per_lookup
# Same SDK, same error-body shape, and deliberately the same reader: one
# definition of "read the fields by name, never let the payload through". Bound
# to a neutral name because nothing here is about sending.
from app.sms.providers.telnyx import describe_send_error as describe_api_error

try:
    import telnyx
    TELNYX_AVAILABLE = True
except ImportError:                     # keep the app importable without the SDK
    TELNYX_AVAILABLE = False


# The carrier's own vocabulary → the five values `LINE_TYPES` allows. Every
# member of the API's documented set is listed, including the ones that map to
# `unknown`, so that a value falling through to the default below means "the
# carrier said something new", not "somebody forgot a row".
#
# `fixed line or mobile` is ambiguous and is deliberately not treated as mobile.
# `pager`, `voicemail`, `premium rate`, `shared cost`, `personal number` and
# `uan` are all real classifications and none of them is a buyer answering their
# own phone.
CARRIER_LINE_TYPES = {
    "mobile": "mobile",
    "voip": "voip",
    "fixed line": "landline",
    "toll free": "toll_free",
    "fixed line or mobile": "unknown",
    "premium rate": "unknown",
    "shared cost": "unknown",
    "personal number": "unknown",
    "pager": "unknown",
    "uan": "unknown",
    "voicemail": "unknown",
    "unknown": "unknown",
}

# Stored (scrubbed) on the lookup row and read by nobody but us. It names no
# carrier and quotes no money, on the rule that applies to every string this
# codebase writes down, whether or not there is a screen for it today.
NO_CLASSIFICATION = "The lookup returned no line type for this number."


def _field(node, name):
    """`node.name` or `node["name"]`, whichever this object supports.

    Duck-typed for `_money()`'s reason in the send provider: the SDK has changed
    its class hierarchy twice inside this project's lifetime, and a recorded
    fixture replayed as a plain dict must exercise the same code path as the
    model object the live client returns. An AttributeError here would cost a
    lookup we had already paid for.
    """
    if node is None:
        return None
    if isinstance(node, dict):
        return node.get(name)
    return getattr(node, name, None)


def normalize_carrier_type(raw) -> str:
    """The carrier's word for a line → ours, or `unknown`.

    Separators are normalised because the API spells its types with spaces
    (`fixed line`) while its own documentation and several client libraries use
    underscores and hyphens. A spelling difference must not silently become a
    line type we refuse to promote.
    """
    if not raw:
        return "unknown"
    key = str(raw).strip().lower().replace("_", " ").replace("-", " ")
    key = " ".join(key.split())
    return CARRIER_LINE_TYPES.get(key, "unknown")


class TelnyxLineTypeProvider(LineTypeProvider):
    """One number in, one line type out, one charge on the account.

    Constructed exactly like the send provider and for the same reason: a stub
    that agrees with the code under test is how a suite goes green over a
    provider nobody can build. `requirements.txt` once pinned a major version
    this app's provider was not written against and nothing failed until the
    morning of a sale — `tests/test_lookup_provider.py` asserts the SDK shape
    this class drives, so a bad pin fails at `pip install` instead.
    """

    name = "telnyx"

    def __init__(self):
        if not TELNYX_AVAILABLE:
            raise ImportError("Telnyx SDK not installed. Run: pip install telnyx")
        if not settings.TELNYX_API_KEY:
            raise ValueError("TELNYX_API_KEY is not set")

        self.client = telnyx.Telnyx(api_key=settings.TELNYX_API_KEY)

    def lookup(self, phone: str) -> LineTypeResult:
        """Never raises. A provider that raises takes the screening pass with it.

        `type="carrier"` is the cheap MCC/MNC query rather than the CNAM one:
        we are asking what kind of line this is, not whose name is on it.
        """
        charged = str(cost_per_lookup())
        try:
            response = self.client.number_lookup.retrieve(phone, type="carrier")
        except Exception as exc:
            # No completed lookup, so nothing was charged and the row stays
            # retryable — a carrier outage must not file every number screened
            # during it as permanently unscreenable.
            return LineTypeResult(line_type="unknown", ok=False,
                                  error=describe_api_error(exc))

        raw = _field(_field(_field(response, "data"), "carrier"), "type")
        if not raw:
            # The call happened and was billed; we just cannot use what came
            # back. Record the spend, report it as unanswered.
            return LineTypeResult(line_type="unknown", ok=False,
                                  error=NO_CLASSIFICATION, cost=charged)

        return LineTypeResult(line_type=normalize_carrier_type(raw),
                              ok=True, cost=charged)
