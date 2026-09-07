"""Google Places — the first real discovery source.

One search at a time. `scrape_runner.run_plan()` walks `taxonomy.searches()` and
runs each one as its own job, so `scrape_jobs.search_term` is the ledger of what
has been searched and `scrape_jobs.api_requests` is what that search cost. A run
whose cost cannot be attributed to its results is a run nobody can decide to
repeat.

## Everything here spends money, so the caps and the dedup are the feature

Text Search is **$35 per 1,000 requests**, first 1,000 of a calendar month free,
up to twenty places a request. The line-type screening that follows is $0.0025 a
number. Two bills, two meters, and neither of them is hygiene:

- `RequestBudget` is checked **before** every request, never after.
- A search already run inside `GOOGLE_PLACES_QUERY_REPEAT_DAYS` is not run
  again. The same query returns the same sixty businesses and we already hold
  every one of them.
- A business already rejected, already a prospect or already a contact is
  stopped in `prospect_ingest.record_prospect()` before it can cost $0.0025 to
  screen.

## Enterprise tier is required

`nationalPhoneNumber` and `internationalPhoneNumber` are Enterprise-SKU fields.
Without that tier the response parses cleanly and carries no phone at all, which
means every record is `invalid` and the run reports finding nothing — the most
expensive way possible to learn the key is wrong. `MISSING_PHONE_FIELDS` names
that case in the log rather than leaving it as a count.

## What this source does NOT do

It does not write a row, does not decide whether a number is textable, and does
not decide who is a buyer. The never-prospect list is matched one layer down in
`record_prospect()`, so P3's sources inherit it. See `prospect_base`'s docstring
for why all three belong there and not here.

It does decide **geography**, because that is what a search *is*: a group's
radius is the "can they collect it" rule, and a result outside it is dropped
before it becomes a prospect rather than scored zero and screened anyway. That
is the difference between a rule and a ranking, and it is $0.0025 a row.

## The API key never reaches a stored string

The key travels in a header, not a query string — `source_url` on every record
is the human-facing Google Maps link, which is what P1 requires and what the CSV
export carries. `describe_places_error()` builds its text from named response
fields and redacts the key if the API ever echoes it, because
`scrape_jobs.error` is stored unscrubbed by design.
"""

from typing import Iterable, Iterator, List, Optional
import logging
import math

from app.core.config import settings
from app.sources.prospect_base import ProspectRecord, ProspectSource
from app.sources.taxonomy import Search

logger = logging.getLogger("prospects")

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

# Only what is used. A field mask is billed by tier, so asking for more than
# this raises the price of every request for data nothing reads.
FIELD_MASK = ",".join((
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.location",
    "places.types",
    "places.primaryType",
    "places.googleMapsUri",
    # Enterprise SKU. Without the tier these come back absent, not empty.
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "nextPageToken",
))

NO_API_KEY = (
    "GOOGLE_PLACES_API_KEY is not set, so there is nothing to search with. "
    "This source refuses rather than running: a search that returns nothing "
    "and a niche that contains nothing look identical on every screen.")

MISSING_PHONE_FIELDS = (
    "The Places response carried no phone field for any of %d results. The "
    "phone number is an Enterprise-tier field — a key without that tier parses "
    "cleanly and returns every business with no way to contact it.")

EARTH_RADIUS_MILES = 3958.7613


def distance_miles(origin, point) -> Optional[float]:
    """Great-circle miles between two (lat, lon) pairs, or None.

    Haversine rather than a flat approximation: this is used at national scale
    and the flat version is several percent out at 2,000 miles, which is the
    range where "national" and "regional" have to be told apart.
    """
    if not origin or not point:
        return None
    lat1, lon1 = (math.radians(v) for v in origin)
    lat2, lon2 = (math.radians(v) for v in point)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return round(2 * EARTH_RADIUS_MILES * math.asin(min(1.0, math.sqrt(a))), 1)


def match_confidence(term: str, place: dict) -> float:
    """How well this result answers the term that found it. 0.5 to 1.0.

    Text Search matches on reviews and descriptions as well as on names and
    categories, so a "food truck" query returns restaurants that a review once
    called a food truck. The overlap between the term's words and the business's
    own name and Google types is a real signal about which kind of result this
    is.

    Deliberately weak, and deliberately floored at 0.5 rather than 0: it is
    worth 15 of 100 points in the score, it is a heuristic about a text match,
    and a source that pretended to more certainty than that would outrank the
    line-type gate, which is the one thing here that is actually measured.
    """
    words = {w for w in term.lower().split() if len(w) > 2}
    if not words:
        return 0.5
    haystack = " ".join([
        (place.get("displayName") or {}).get("text", "") or "",
        " ".join(place.get("types") or []),
        place.get("primaryType") or "",
    ]).lower().replace("_", " ")
    hits = sum(1 for word in words if word in haystack)
    return round(0.5 + 0.5 * (hits / len(words)), 2)


def describe_places_error(status_code: int, body) -> str:
    """One sentence about a failed request, built from named fields.

    Never `str(exc)` and never the raw body. P1b's lesson is that a scrubber
    downstream will cover for a defect upstream and a test after both proves
    neither, so the text is assembled here from fields we chose. The key is
    redacted even though it travels in a header: `scrape_jobs.error` is stored
    unscrubbed on purpose, and a credential in a column outlives the incident.
    """
    message = ""
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("status") or "")
    text = f"The discovery search failed (HTTP {status_code})."
    if message:
        text = f"{text} {message}"
    key = settings.GOOGLE_PLACES_API_KEY
    return text.replace(key, "[redacted]") if key else text


class PlacesClient:
    """The one place an HTTP request is made. Replaced wholesale in tests.

    Constructed lazily by the source so that importing this module, registering
    it, or building it to read its taxonomy costs nothing and needs no key. The
    `httpx.Client` it holds is the thing `cleanup()` exists to close — the
    reference system leaked one driver process per nightly scrape and nobody
    noticed for months.
    """

    def __init__(self, api_key: str = None, timeout: float = None):
        import httpx                       # local: keeps import-time cost off boot

        self.api_key = api_key if api_key is not None else settings.GOOGLE_PLACES_API_KEY
        if not self.api_key:
            raise ValueError(NO_API_KEY)
        self._client = httpx.Client(
            timeout=timeout or settings.GOOGLE_PLACES_TIMEOUT_SECONDS)

    def search_text(self, query: str, *, page_size: int, page_token: str = None,
                    bias=None) -> dict:
        """One Text Search request. Raises on anything but a 200.

        Raising is right here: `scrape_runner` records the job `failed`, keeps
        everything produced before the failure, and a search that half-worked is
        not reported as a search that finished.
        """
        payload = {"textQuery": query, "pageSize": page_size}
        if page_token:
            payload["pageToken"] = page_token
        if bias:
            latitude, longitude, meters = bias
            payload["locationBias"] = {
                "circle": {"center": {"latitude": latitude,
                                      "longitude": longitude},
                           "radius": meters}}

        response = self._client.post(
            SEARCH_URL, json=payload,
            headers={"Content-Type": "application/json",
                     "X-Goog-Api-Key": self.api_key,
                     "X-Goog-FieldMask": FIELD_MASK})
        if response.status_code != 200:
            body = None
            try:
                body = response.json()
            except Exception:                        # noqa: BLE001
                body = None
            raise RuntimeError(describe_places_error(response.status_code, body))
        return response.json()

    def close(self) -> None:
        self._client.close()


class GooglePlacesSource(ProspectSource):
    """Runs one planned search and yields the businesses it found."""

    name = "google_places"
    description = "Google Places Text Search (Enterprise tier)"

    def __init__(self, client: PlacesClient = None):
        super().__init__()
        self.client = client
        self._closed = False
        self.dropped_out_of_radius = 0
        self.pages_read = 0

    # ─── The contract ───────────────────────────────────────────────────────

    def fetch(self, *, search: Search = None, request_budget=None,
              **kwargs) -> Iterator[ProspectRecord]:
        """Page one `Search` and yield a record per usable result.

        `request_budget` is handed in by the runner already read from the
        database, for the same reason `line_type_for()` is handed its budget: a
        source may not query, and the ceiling has to be checked inside this
        loop. See `api_budget`.
        """
        if search is None:
            raise ValueError(
                "GooglePlacesSource.fetch() needs a Search. Run it through "
                "scrape_runner.run_plan(), which builds them from the taxonomy.")

        origin = (settings.PROSPECT_ORIGIN_LAT, settings.PROSPECT_ORIGIN_LON)
        bias = None
        if search.sweep.center:
            bias = (search.sweep.center[0], search.sweep.center[1],
                    search.bias_meters)

        page_token = None
        # `max(1, ...)` rather than the "zero means off" reading the two spend
        # caps have, and the difference is deliberate: a cap of zero is an
        # instruction about money and reads as "do not spend", while a page
        # depth of zero is not an instruction at all. Honouring it would make a
        # mis-set integer produce a run that found nothing — indistinguishable
        # from a niche that contains nothing, which is the exact failure this
        # source refuses a missing key to avoid.
        for page in range(max(1, settings.GOOGLE_PLACES_MAX_PAGES)):
            if self.should_stop():
                logger.info("[%s] stop requested; %s left after %d page(s)",
                            self.name, search.ledger_key, page)
                return
            # **Before the call, never after.** A cap checked afterwards is a
            # ledger, not a ceiling.
            if request_budget is not None and not request_budget.allows():
                request_budget.refuse()
                logger.info("[%s] %s stopped at the request cap after %d page(s)",
                            self.name, search.ledger_key, page)
                return

            result = self._client().search_text(
                search.query, page_size=settings.GOOGLE_PLACES_PAGE_SIZE,
                page_token=page_token, bias=bias)
            if request_budget is not None:
                request_budget.charge()
            self.pages_read += 1

            places = result.get("places") or []
            if places and not any(_phone_of(p) for p in places):
                # Not an error and not a count somebody will chase later: the
                # single most likely cause is a key without Enterprise tier.
                logger.error(MISSING_PHONE_FIELDS, len(places))

            for place in places:
                record = self._record(place, search, origin)
                if record is not None:
                    yield record

            # What this request actually bought, per page. The job row counts
            # what was *persisted*; these two are the difference between it and
            # what the API returned, and without them a request that produced
            # three prospects is indistinguishable from one that returned three
            # places. There is no jobs screen, so this is the only place it is
            # visible — and none of it is the client's business.
            logger.info("[%s] %s page %d: %d place(s) returned, %d without a "
                        "phone, %d outside the %s radius",
                        self.name, search.ledger_key, page + 1, len(places),
                        sum(1 for p in places if not _phone_of(p)),
                        self.dropped_out_of_radius,
                        "national" if search.radius_miles is None
                        else f"{search.radius_miles}-mile")

            page_token = result.get("nextPageToken")
            if not page_token:
                return

    def cleanup(self) -> None:
        """Close the HTTP client. Safe twice, and safe if it never opened.

        Called by the runner in a `finally` on every exit path, including the
        one where the deadline passed while a request was in flight — which is
        why it closes the client rather than asking the loop to. It closes
        **whatever client it holds**, injected or not: a source is run once and
        discarded, and a rule of the form "close it only if you made it" is one
        more branch on the path the reference system leaked seventeen processes
        down.
        """
        client, self.client = self.client, None
        self._closed = True
        if client is not None:
            try:
                client.close()
            except Exception as exc:                 # noqa: BLE001
                logger.error("[%s] closing the search client raised: %s",
                             self.name, exc)

    # ─── Internals ──────────────────────────────────────────────────────────

    def _client(self) -> PlacesClient:
        """The client, built on first use. Never rebuilt after cleanup.

        Rebuilding would open a second connection on a source somebody reused,
        and the first one is already closed and unreachable — which is exactly
        the shape of a leak that stays invisible until a box runs out of memory.
        Raise instead: a source is run once by `scrape_runner` and discarded.
        """
        if self._closed:
            raise RuntimeError(
                "This source's search client was closed. A source is run once "
                "by scrape_runner and then discarded — build a new one.")
        if self.client is None:
            self.client = PlacesClient()
        return self.client

    def _record(self, place: dict, search: Search,
                origin) -> Optional[ProspectRecord]:
        """One API result → one record, or None when geography rules it out."""
        phone = _phone_of(place)
        if not phone:
            return None

        location = place.get("location") or {}
        point = None
        if location.get("latitude") is not None and location.get("longitude") is not None:
            point = (location["latitude"], location["longitude"])
        miles = distance_miles(origin, point)

        # The radius rule, applied to the result rather than to the request.
        # Google's bias circle tops out at 50 km (`MAX_BIAS_RADIUS_METERS`), so
        # a 150-mile group cannot be expressed as one request and the rule has
        # to be enforced here. Dropped rather than kept-and-scored-zero: a
        # business that will never collect a lot costs $0.0025 to screen and a
        # slot in the review queue forever.
        # A business whose location the API did not return is **kept**, not
        # dropped. An unknown distance is not evidence of being far away, and
        # the scorer already ranks it below everything it can measure — so the
        # cheap error is a reviewer glancing at one extra row, and the expensive
        # one is silently discarding a buyer over a missing field.
        if search.radius_miles is not None and miles is not None:
            if miles > search.radius_miles:
                self.dropped_out_of_radius += 1
                return None

        name = (place.get("displayName") or {}).get("text") or None
        return ProspectRecord(
            phone=phone,
            # Human-facing, and never the API endpoint: those carry credentials
            # in the query string and this field goes into the CSV export.
            source_url=place.get("googleMapsUri")
            or f"https://www.google.com/maps/place/?q=place_id:{place.get('id', '')}",
            search_term=search.term,
            buyer_rationale=search.group.buyer_rationale,
            business_name=name,
            address=place.get("formattedAddress") or None,
            category_slug=search.group.category_slug,
            category_confidence=match_confidence(search.term, place),
            distance_miles=miles,
            raw_payload=dict(place),
        )


def _phone_of(place: dict) -> str:
    """The best phone on a place, preferring the E.164-shaped one."""
    return (place.get("internationalPhoneNumber")
            or place.get("nationalPhoneNumber") or "").strip()


def records_from(places: Iterable[dict], search: Search) -> List[ProspectRecord]:
    """Convenience for callers holding a page of results already.

    Used by the acceptance script to show the mapping without a client. Goes
    through the same `_record()` the live path does, because a demonstration
    built on a second copy of the mapping demonstrates the copy.
    """
    source = GooglePlacesSource(client=None)          # never used: no request
    origin = (settings.PROSPECT_ORIGIN_LAT, settings.PROSPECT_ORIGIN_LON)
    made = [source._record(place, search, origin) for place in places]
    return [record for record in made if record is not None]
