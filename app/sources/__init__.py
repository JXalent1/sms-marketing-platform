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

# Discovery sources. P1 built the machinery and shipped none; P2 added the
# first, and it was a class plus a taxonomy exactly as the seam promised —
# nothing in the pipeline changed to take it. The licence registries are P3.
#
# Imported lazily by name rather than at module import: `google_places` reaches
# for `httpx` when it builds a client, and neither this registry nor anything
# that merely lists the sources should pay for that.
PROSPECT_SOURCES = {
    "google_places": "app.sources.google_places:GooglePlacesSource",
}


def get_source(name: str) -> ContactSource:
    if name not in SOURCES:
        raise ValueError(f"Unknown contact source '{name}'. Registered: {', '.join(SOURCES)}")
    return SOURCES[name]()


def get_prospect_source(name: str) -> ProspectSource:
    if name not in PROSPECT_SOURCES:
        raise ValueError(
            f"Unknown prospect source '{name}'. Registered: "
            f"{', '.join(PROSPECT_SOURCES) or 'none'}")
    module_path, class_name = PROSPECT_SOURCES[name].split(":")
    module = __import__(module_path, fromlist=[class_name])
    return getattr(module, class_name)()


__all__ = ["ContactSource", "ContactRecord", "IngestResult", "SOURCES",
           "get_source", "ProspectSource", "ProspectRecord",
           "ProspectIngestResult", "PROSPECT_SOURCES", "get_prospect_source"]
