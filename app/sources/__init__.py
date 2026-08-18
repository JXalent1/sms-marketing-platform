"""Contact source registry.

Register new sources here so routes and scheduled jobs can look them up by name.
"""

from app.sources.base import ContactSource, ContactRecord, IngestResult
from app.sources.csv_source import CSVContactSource

SOURCES = {
    CSVContactSource.name: CSVContactSource,
    # ExampleAPIContactSource.name: ExampleAPIContactSource,   # needs constructor args
}


def get_source(name: str) -> ContactSource:
    if name not in SOURCES:
        raise ValueError(f"Unknown contact source '{name}'. Registered: {', '.join(SOURCES)}")
    return SOURCES[name]()


__all__ = ["ContactSource", "ContactRecord", "IngestResult", "SOURCES", "get_source"]
