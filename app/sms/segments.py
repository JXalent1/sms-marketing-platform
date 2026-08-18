"""SMS segment counting — the billing basis.

Carriers bill per *segment*, not per message, and the segment size depends on
the character set:

    GSM-7  (plain letters, digits, basic punctuation)   160 alone / 153 concatenated
    UCS-2  (anything else — one emoji is enough)         70 alone /  67 concatenated

A single emoji re-encodes the entire message, so a 300-character reminder goes
from 2 segments to 5. In the reference deployment this one detail was the
difference between a 20% and a 60% gross margin: the client was billed on a flat
len/160 basis while the carrier billed real UCS-2 parts, a 2.36x multiplier
absorbed entirely by the operator.

Prefer the carrier's reported part count when you have it (SendResult.parts);
this module is the fallback and the pre-send estimate. It is tested to agree
with Telnyx's own `parts` value.
"""

# GSM 03.38 basic alphabet. One 7-bit position each.
_GSM7_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ ÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
# Still GSM-7, but each occupies two positions.
_GSM7_EXTENDED = set("^{}\\[~]|€")

GSM7_SINGLE, GSM7_MULTI = 160, 153
UCS2_SINGLE, UCS2_MULTI = 70, 67


def is_gsm7(message: str) -> bool:
    """True if the whole message fits the GSM-7 alphabet."""
    return all(ch in _GSM7_BASIC or ch in _GSM7_EXTENDED for ch in message)


def count_segments(message: str) -> int:
    """Encoding-aware segment count, matching how carriers actually bill."""
    if not message:
        return 1
    if is_gsm7(message):
        length = len(message) + sum(1 for ch in message if ch in _GSM7_EXTENDED)
        single, multi = GSM7_SINGLE, GSM7_MULTI
    else:
        length = len(message)
        single, multi = UCS2_SINGLE, UCS2_MULTI
    if length <= single:
        return 1
    return -(-length // multi)          # ceil division


def describe(message: str) -> dict:
    """Segment breakdown for the composer UI, so an operator sees the cost of
    an emoji *before* sending to 6,000 people."""
    gsm7 = is_gsm7(message or "")
    segments = count_segments(message or "")
    non_gsm = sorted({ch for ch in (message or "") if ch not in _GSM7_BASIC and ch not in _GSM7_EXTENDED})
    return {
        "encoding": "GSM-7" if gsm7 else "UCS-2",
        "characters": len(message or ""),
        "segments": segments,
        "per_segment": (GSM7_MULTI if segments > 1 else GSM7_SINGLE) if gsm7
                       else (UCS2_MULTI if segments > 1 else UCS2_SINGLE),
        # Which characters forced UCS-2 — usually a single emoji the client pasted in.
        "forced_unicode_by": non_gsm[:10],
        "gsm7_segments_if_stripped": count_segments(
            "".join(ch for ch in (message or "") if ch in _GSM7_BASIC or ch in _GSM7_EXTENDED)
        ) if not gsm7 else segments,
    }
