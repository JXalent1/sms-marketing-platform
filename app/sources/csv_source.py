"""CSV import — the reference ContactSource implementation.

Read this one before writing your own; it shows the full shape of a source.

Header matching is deliberately forgiving. Clients send "Cell", "Mobile Number",
"Contact #", and a file that imports zero rows because the column was called
"Phone Number" instead of "phone" is a support call every single time.
"""

import csv
import io
from typing import Iterable, List
from app.sources.base import ContactSource, ContactRecord

# Accepted spellings per canonical field. Add to these freely — a false match is
# far less costly than a silently empty import.
HEADER_ALIASES = {
    "name": [
        "name", "full name", "fullname", "first name", "firstname",
        "contact", "contact name", "customer", "customer name",
        "client", "client name", "lead", "lead name", "buyer", "bidder",
    ],
    "phone": [
        "phone", "phone number", "phonenumber", "cell", "cell phone", "cellphone",
        "mobile", "mobile number", "mobile phone", "telephone", "tel",
        "number", "contact number", "contact #", "phone #",
    ],
    "email": ["email", "e-mail", "email address", "mail"],
    "notes": ["notes", "note", "comment", "comments", "remarks"],
}


def _normalize_header(header: str) -> str:
    if header is None:
        return ""
    return header.strip().lower().replace("_", " ").replace("-", " ")


def map_headers(raw_headers) -> dict:
    """Return {raw_header: canonical_field} for headers we recognize."""
    mapping = {}
    for raw in raw_headers or []:
        normalized = _normalize_header(raw)
        for field_name, aliases in HEADER_ALIASES.items():
            if normalized in aliases:
                mapping[raw] = field_name
                break
    return mapping


def _value(row: dict, header_map: dict, field_name: str):
    for raw, canonical in header_map.items():
        if canonical == field_name:
            value = row.get(raw)
            if value is not None and str(value).strip():
                return str(value).strip().strip(",")
    return None


class CSVContactSource(ContactSource):
    name = "csv"
    description = "Import contacts from an uploaded CSV file"

    def fetch(self, content: bytes = None, **kwargs) -> Iterable[ContactRecord]:
        if content is None:
            raise ValueError("CSVContactSource.fetch requires content=<bytes>")

        # utf-8-sig strips the BOM Excel prepends, which otherwise turns the
        # first header into "﻿Name" and breaks the match.
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        header_map = map_headers(reader.fieldnames)

        canonical = set(header_map.values())
        if "phone" not in canonical:
            raise ValueError(
                f"CSV has no recognizable phone column. Found headers: {reader.fieldnames}. "
                f"Accepted: {', '.join(HEADER_ALIASES['phone'])}"
            )

        for row in reader:
            phone = _value(row, header_map, "phone")
            if not phone:
                continue

            attributes = {}
            notes = _value(row, header_map, "notes")
            if notes:
                attributes["notes"] = notes
            # Keep unmapped columns rather than discarding them — the client
            # usually asks for one of them as a merge tag two weeks later.
            for raw, value in row.items():
                if raw and raw not in header_map and value and str(value).strip():
                    attributes[_normalize_header(raw).replace(" ", "_")] = str(value).strip()

            yield ContactRecord(
                phone=phone,
                full_name=_value(row, header_map, "name"),
                email=_value(row, header_map, "email"),
                attributes=attributes,
            )

    @staticmethod
    def count_data_rows(content: bytes) -> int:
        """Data rows in the file, header excluded.

        fetch() skips a row with no phone cell at all, so counting what it
        yields undercounts the file. The import preview's `rows` figure has to
        match what the client sees when he opens the CSV in Excel, or every
        other number in the report reads as wrong.

        Entirely blank lines do not count — Excel exports trail them, and he
        does not count them either.
        """
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        total = 0
        for row in reader:
            values = []
            for value in row.values():
                # DictReader parks surplus columns under restkey as a list.
                values.extend(value if isinstance(value, list) else [value])
            if any((v or "").strip() for v in values):
                total += 1
        return total

    @staticmethod
    def preview(content: bytes, limit: int = 5) -> dict:
        """Parse a few rows so the operator can confirm the mapping before committing."""
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        header_map = map_headers(reader.fieldnames)
        rows: List[dict] = []
        for i, row in enumerate(reader):
            if i >= limit:
                break
            rows.append({
                "name": _value(row, header_map, "name"),
                "phone": _value(row, header_map, "phone"),
                "email": _value(row, header_map, "email"),
            })
        return {
            "headers": reader.fieldnames,
            "mapped": header_map,
            "unmapped": [h for h in (reader.fieldnames or []) if h not in header_map],
            "sample": rows,
        }
