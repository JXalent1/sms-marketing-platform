"""Source registries.

Two of them, because the two kinds of source produce different things and land
in different tables. A `ContactSource` produces people the client already has a
relationship with — a CSV of past bidders — and its records become contacts. A
`ProspectSource` produces businesses nobody has agreed to anything, and its
records land in the review queue behind the line-type gate.

Keeping them apart is the point. The day a discovery source is registered in
SOURCES by mistake, its output goes straight onto the textable list without ever
being screened or reviewed, which is the failure this whole pipeline exists to
prevent.

Register new sources here so routes and scheduled jobs can look them up by name.
"""

from app.sources.base import ContactSource, ContactRecord, IngestResult
from app.sources.csv_source import CSVContactSource
from app.sources.prospect_base import (
    ProspectSource, ProspectRecord, ProspectIngestResult,
)

SOURCES = {
    CSVContactSource.name: CSVContactSource,
    # ExampleAPIContactSource.name: ExampleAPIContactSource,   # needs constructor args
}

# Discovery sources. Empty on purpose: P1 built the machinery and explicitly
# shipped no source implementation. Google Places is P2 and the licence
# registries are P3, and each one is a class plus a taxonomy — nothing else here
# has to change to take one.
PROSPECT_SOURCES = {}


def get_source(name: str) -> ContactSource:
    if name not in SOURCES:
        raise ValueError(f"Unknown contact source '{name}'. Registered: {', '.join(SOURCES)}")
    return SOURCES[name]()


def get_prospect_source(name: str) -> ProspectSource:
    if name not in PROSPECT_SOURCES:
        raise ValueError(
            f"Unknown prospect source '{name}'. Registered: "
            f"{', '.join(PROSPECT_SOURCES) or 'none yet — see P2'}")
    return PROSPECT_SOURCES[name]()


__all__ = ["ContactSource", "ContactRecord", "IngestResult", "SOURCES",
           "get_source", "ProspectSource", "ProspectRecord",
           "ProspectIngestResult", "PROSPECT_SOURCES", "get_prospect_source"]
