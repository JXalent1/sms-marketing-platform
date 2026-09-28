"""Session LT1 — `scripts/prospect_run.py` and metro-overlap dedup.

Acceptance criteria 5 and 7-10. **Nothing here calls a paid API.** The Places
client is a fake that answers by bias centre and counts its calls; the
line-type provider is `_prospect_setup.CountingLookupProvider`, passed in
explicitly; `PROSPECT_LOOKUP_PROVIDER` is only ever changed to a string that is
never resolved into a real provider (the fake is injected, or the cached
disabled provider answers).
"""

import os
import socket

import pytest
from fastapi.testclient import TestClient

import scripts.prospect_run as prospect_run
from app.core.config import settings
from app.main import app
from app.models.prospect import Prospect, ProspectSighting
from app.services import scrape_runner
from app.sources import metros, taxonomy
from app.sources.google_places import GooglePlacesSource
from tests import _places_setup as setup
from app.sms.lookup import LineTypeResult
from tests._prospect_setup import CountingLookupProvider

CARRIERS = ("telnyx", "twilio")
POOL = setup.POOL
SHARED = POOL[199]            # one store both New York and Philadelphia find
CHAIN = POOL[198]             # a chain, returned in Chicago
INDEX = {s.center: i for i, s in enumerate(metros.METRO_SWEEPS)}
NYC, PHL, CHI = (INDEX[metros.METRO_SWEEPS[i].center] for i in (0, 7, 2))


@pytest.fixture
def db():
    yield from setup.purged_db_fixture_body()


def _place(phone, name, center):
    return {"id": f"id-{phone}", "displayName": {"text": name},
            "formattedAddress": "1 Main St", "types": ["lighting_store"],
            "location": {"latitude": center[0], "longitude": center[1]},
            "googleMapsUri": f"https://maps.google.com/?cid={phone[-4:]}",
            "internationalPhoneNumber": phone}


class MetroClient:
    """Answers by the metro the request is centred on; counts every request."""

    def __init__(self, calls):
        self.calls = calls

    def search_text(self, query, *, page_size, page_token=None, bias=None):
        self.calls.append((query, bias))
        m = INDEX[(bias[0], bias[1])]
        places = [_place(POOL[m], f"Metro {m} Lighting", (bias[0], bias[1]))]
        if m in (NYC, PHL):
            places.append(_place(SHARED, "Tri-State Lighting Supply", (bias[0], bias[1])))
        if m == CHI:
            places.append(_place(CHAIN, "The Home Depot", (bias[0], bias[1])))
        return {"places": places}

    def close(self):
        pass


def metro_factory():
    calls = []
    return (lambda: GooglePlacesSource(client=MetroClient(calls))), calls


# Line types by metro index; the shared store is mobile.
ANSWERS = {POOL[i]: ("landline", "voip", "unknown", "mobile")[i % 4] for i in range(50)}


# ─── Criterion 5: overlapping metros → one prospect ─────────────────────────

def _search(term, sweep):
    return next(s for s in taxonomy.searches(group_slugs=["lighting_trade",
                                                          "lighting_retail"])
                if s.term == term and s.sweep == sweep)


def _run(db, search):
    make, _ = metro_factory()
    return scrape_runner.run_job(db, make(), search_term=search.ledger_key,
                                 search=search, screen=False)


def test_same_store_from_two_metros_and_two_terms_is_one_prospect_two_sightings(db):
    ny, phl = metros.METRO_SWEEPS[0], metros.METRO_SWEEPS[7]
    _run(db, _search("lighting wholesaler", ny))
    second = _run(db, _search("lighting distributor", phl))

    rows = db.query(Prospect).filter(Prospect.phone == SHARED).all()
    assert len(rows) == 1
    sightings = db.query(ProspectSighting).filter(
        ProspectSighting.prospect_id == rows[0].id).all()
    assert len(sightings) == 2
    assert second.prospects_corroborated >= 1
    # One source, however many metros: corroboration counts sources, so the
    # overlap cannot inflate the score.
    assert rows[0].source_count == 1


def test_same_store_from_two_metros_on_one_term_is_one_prospect_one_sighting(db):
    """The spec expected two sightings here; the existing path writes one.

    A sighting is unique on (prospect, source, term) — the term, not the
    ledger key — which is what keeps the per-term rejection flag and the
    corroboration score meaningful. Recorded in status.md; no second dedup.
    """
    ny, phl = metros.METRO_SWEEPS[0], metros.METRO_SWEEPS[7]
    _run(db, _search("lighting wholesaler", ny))
    score_before = db.query(Prospect).filter(Prospect.phone == SHARED).one().score
    second = _run(db, _search("lighting wholesaler", phl))

    rows = db.query(Prospect).filter(Prospect.phone == SHARED).all()
    assert len(rows) == 1
    assert db.query(ProspectSighting).filter(
        ProspectSighting.prospect_id == rows[0].id).count() == 1
    assert second.prospects_corroborated == 1 and second.prospects_created == 1
    assert rows[0].score == score_before and rows[0].source_count == 1


# ─── Criterion 7: the dry run prints every figure and makes no call ─────────

class NetworkAttempt(Exception):
    pass


@pytest.fixture
def no_network(monkeypatch):
    attempts = []

    def refuse(*args, **kwargs):
        attempts.append(args)
        raise NetworkAttempt(args)

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(prospect_run, "get_lookup_provider", refuse)
    monkeypatch.setattr("app.sources.google_places.PlacesClient.__init__", refuse)
    return attempts


def test_the_network_guard_catches_a_real_request(no_network):
    """Known answer: a real HTTP request under the guard is caught by it."""
    import httpx
    with pytest.raises(NetworkAttempt):
        httpx.Client().get("http://192.0.2.1/")
    assert no_network


def test_dry_run_prints_every_figure_and_makes_no_network_call(db, no_network, capsys):
    with setup.caps(requests=700, max_pages=3, repeat_days=30, lookup_cap=40.0):
        code = prospect_run.main(["--groups", "lighting_retail,lighting_trade",
                                  "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and no_network == []
    for expected in ("Searches planned: 400", "to run: 400",
                     "Worst-case Places requests: 1,200 (400 searches x 3 pages",
                     "Places requests already used this month: 0",
                     "GOOGLE_PLACES_MONTHLY_REQUEST_CAP: 700",
                     "stops after 700 requests",
                     f"Free Places requests per month: "
                     f"{settings.GOOGLE_PLACES_FREE_REQUESTS_PER_MONTH:,}",
                     "Worst-case Places cost beyond the free allowance: $0.00",
                     "PROSPECT_LOOKUP_MONTHLY_CAP: $40.00", "spent this month: $0.00",
                     "Worst-case lookup spend: $35.00"):
        assert expected in out, (expected, out)


def test_dry_run_figures_move_with_the_meters_not_a_constant(db, no_network, capsys):
    """Requests used this month and searches already run come from the tables."""
    search = next(iter(taxonomy.searches(group_slugs=["lighting_trade"])))
    make, _ = metro_factory()
    scrape_runner.run_job(db, make(), search_term=search.ledger_key,
                          search=search, screen=False)
    with setup.caps(requests=1500, max_pages=2, repeat_days=30):
        prospect_run.main(["--groups", "lighting_trade", "--dry-run"])
    out = capsys.readouterr().out
    assert "already searched in the last 30 days (skipped, free): 1" in out
    assert "Worst-case Places requests: 298 (149 searches x 2 pages" in out
    assert "Places requests already used this month: 1" in out


# ─── Criterion 8: --live without a key refuses in one line ──────────────────

def test_live_without_a_key_refuses_in_one_line(db, monkeypatch, capsys):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_API_KEY", "")
    code = prospect_run.main(["--groups", "lighting_retail", "--live"])
    captured = capsys.readouterr()
    lines = [line for line in (captured.out + captured.err).splitlines() if line.strip()]
    assert code != 0
    assert len(lines) == 1 and "GOOGLE_PLACES_API_KEY" in lines[0], lines


def test_live_with_screening_off_or_fallen_back_refuses(db, monkeypatch, capsys):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(settings, "PROSPECT_LOOKUP_PROVIDER", "none")
    assert prospect_run.main(["--groups", "lighting_retail", "--live"]) == 2
    assert "PROSPECT_LOOKUP_PROVIDER is none" in capsys.readouterr().out
    # Configured, but the provider that answers is the disabled fallback.
    monkeypatch.setattr(settings, "PROSPECT_LOOKUP_PROVIDER", "telnyx")
    assert prospect_run.main(["--groups", "lighting_retail", "--live"]) == 2
    out = capsys.readouterr().out
    assert "screening did not start" in out
    assert not any(c in out.lower() for c in CARRIERS), out
    assert db.query(Prospect).filter(Prospect.phone.in_(POOL)).count() == 0


def test_live_with_no_lookup_room_refuses_before_any_request(db, monkeypatch, capsys):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(settings, "PROSPECT_LOOKUP_PROVIDER", "fake-lookup")
    make, calls = metro_factory()
    provider = CountingLookupProvider()
    with setup.caps(lookup_cap=0.0):
        code = prospect_run.main(["--groups", "lighting_trade", "--live"],
                                 source_factory=make, provider=provider)
    assert code == 2
    assert "PROSPECT_LOOKUP_MONTHLY_CAP has no room" in capsys.readouterr().out
    assert calls == [] and provider.calls == []


def test_dry_run_warns_when_the_lookup_cap_cuts_the_worst_case(db, no_network, capsys):
    with setup.caps(requests=1000, max_pages=3, lookup_cap=1.0):
        prospect_run.main(["--groups", "lighting_trade", "--dry-run"])
    assert "The lookup cap is below the worst case" in capsys.readouterr().out


# ─── Criterion 9: --live end to end on fakes ────────────────────────────────

def _live(monkeypatch, provider, capsys):
    monkeypatch.setattr(settings, "GOOGLE_PLACES_API_KEY", "test-key")
    monkeypatch.setattr(settings, "PROSPECT_LOOKUP_PROVIDER", "fake-lookup")
    make, calls = metro_factory()
    with setup.caps(requests=1000, max_pages=3, repeat_days=30, lookup_cap=50.0):
        code = prospect_run.main(["--groups", "lighting_trade", "--live"],
                                 source_factory=make, provider=provider)
    return code, calls, capsys.readouterr()


def test_live_queues_prospects_and_prints_the_line_type_breakdown(db, monkeypatch, capsys):
    provider = CountingLookupProvider(answers=ANSWERS, default="mobile")
    code, calls, captured = _live(monkeypatch, provider, capsys)
    out = captured.out
    assert code == 0, out
    assert len(calls) == 150                      # one page per fake search

    queued = db.query(Prospect).filter(Prospect.phone.in_(POOL),
                                       Prospect.status == "pending").count()
    assert queued == 51                           # 50 metros + the shared store
    assert db.query(Prospect).filter(Prospect.phone == CHAIN).count() == 0
    assert "Prospects queued: 51 new" in out
    assert "excluded" in out and "3 excluded" in out   # the chain, once per term

    expected = {k: 0 for k in prospect_run.LINE_TYPES_SHOWN}
    for phone in list(POOL[:50]) + [SHARED]:
        expected[ANSWERS.get(phone, "mobile")] += 1
    line = "Line types found: " + "; ".join(f"{k} {v:,}" for k, v in expected.items())
    assert line in out, out
    assert f"Promotable now: {expected['mobile'] + expected['voip']} " in out
    assert expected["mobile"] + expected["voip"] == 26
    assert len(provider.calls) == 51              # each number looked up once


# ─── Criterion 10: white-label on rendered output and on the script ─────────

class CarrierNamedProvider(CountingLookupProvider):
    """A fake whose name and failure text are the carrier's, as a live one is."""

    name = "telnyx"

    def lookup(self, phone):
        if phone == POOL[5]:
            self.calls.append(phone)
            return LineTypeResult(line_type="unknown", ok=False,
                                  error="TelnyxError: telnyx_api lookup failed")
        return super().lookup(phone)


class RaisingCarrierProvider(CountingLookupProvider):
    """Breaks the provider contract by raising, so the script's own exception
    path is what has to keep the name off the screen."""

    name = "telnyx"

    def lookup(self, phone):
        raise RuntimeError("TelnyxError: telnyx_api lookup failed")


def test_script_output_names_no_carrier_even_with_a_carrier_named_provider(
        db, monkeypatch, capsys):
    code, _, captured = _live(monkeypatch, CarrierNamedProvider(answers=ANSWERS),
                              capsys)
    text = (captured.out + captured.err).lower()
    assert "Line types found" in captured.out
    assert not any(c in text for c in CARRIERS), text[:500]


def test_a_raised_provider_error_is_one_scrubbed_line(db, monkeypatch, capsys):
    code, _, captured = _live(monkeypatch, RaisingCarrierProvider(), capsys)
    assert code == 1
    assert "The run stopped early" in captured.out
    text = (captured.out + captured.err).lower()
    assert not any(c in text for c in CARRIERS), text[:500]


class InterruptingProvider(CountingLookupProvider):
    name = "telnyx"

    def lookup(self, phone):
        raise KeyboardInterrupt


def test_an_interrupt_prints_one_scrubbed_line_not_a_traceback(db, monkeypatch, capsys):
    code, _, captured = _live(monkeypatch, InterruptingProvider(), capsys)
    assert code == 130
    assert captured.out.strip().startswith("Interrupted.")
    assert not any(c in (captured.out + captured.err).lower() for c in CARRIERS)


def test_scrubbing_formatter_removes_a_glued_carrier_name():
    import logging
    record = logging.LogRecord("prospects", logging.ERROR, __file__, 1,
                               "TelnyxError from telnyx_api", None, None)
    text = prospect_run.ScrubbingFormatter("%(message)s").format(record).lower()
    assert "telnyx" not in text


def test_prospect_screens_render_no_carrier_name_after_a_lighting_run(
        db, monkeypatch, capsys):
    _live(monkeypatch, CarrierNamedProvider(answers=ANSWERS), capsys)
    client = TestClient(app)
    login = client.post("/login", data={"username": "admin",
                                        "password": os.environ["ADMIN_PASSWORD"]})
    assert login.status_code in (200, 302)
    for path in ("/prospects", "/api/prospects"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert not any(c in response.text.lower() for c in CARRIERS), path
    # Known answer: the API is showing the lighting rows, not an empty page.
    assert "Metro" in client.get("/api/prospects").text
