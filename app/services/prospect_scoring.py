"""How a prospect earns its place at the top of the review queue.

A review queue nobody works through is the same as no review queue. Ten thousand
scraped businesses is a week of somebody's life; the top two hundred is an
afternoon, and the point of a score is that the afternoon is worth more than the
week.

## Line type is the dominant term, on purpose

It is 60 of the 100 points, which is more than every other signal combined. That
is not a hedge — it is the finding. 39% of one live send was landlines, and no
amount of category confidence makes a landline worth texting. A perfectly
categorised, half-mile-away, twice-corroborated landline should rank below an
unscreened mobile, and with these weights it does.

`unknown` scores 10 rather than 0. An unscreened number is not evidence of a bad
number — it is the absence of evidence — and scoring it as though it were a
landline would bury every prospect on a box where screening is not switched on,
which is every box until P2. It still cannot be promoted; it just does not get
ranked below the numbers we know are dead.

## Distance, and the cliff at the radius

Inside the category's radius the score falls off linearly from 15 to 7.5, so
nearer is better without nearness ever outweighing line type. Outside it, zero —
a cliff rather than a slope, because "can they collect it" is a yes/no question
and a 400-mile food-truck operator is not 20% of a prospect.

A national category (radius `None`) scores the full 15 for everyone: distance
does not constrain a card dealer, and taxing them for being far away would rank
the whole category below the local ones for a reason that does not apply to it.

Unknown distance scores 0. It is not evidence of proximity, and a source that
cannot say where a business is has not done the work. Note that this is a
uniform penalty across everything one source produces, so it costs that source's
prospects nothing *against each other* — it only ranks them below a source that
did the work.

## Corroboration is capped

Two extra sources is the cap, worth 10. Beyond that it stops being evidence and
starts being a measure of how many searches were run, and the queue would drift
toward whatever the last nightly job happened to cover.
"""

from typing import Optional

from app.core.config import settings
import logging

logger = logging.getLogger("prospects")

# Line type. The dominant term — see the module docstring.
LINE_TYPE_POINTS = {
    "mobile": 60,
    "voip": 30,
    "unknown": 10,
    "toll_free": 5,
    "landline": 0,
}

CONFIDENCE_POINTS = 15
DISTANCE_POINTS = 15
CORROBORATION_POINTS_PER_EXTRA_SOURCE = 5
MAX_CORROBORATION_POINTS = 10

MAX_SCORE = (max(LINE_TYPE_POINTS.values()) + CONFIDENCE_POINTS
             + DISTANCE_POINTS + MAX_CORROBORATION_POINTS)


def radius_for(category_slug: Optional[str]) -> Optional[int]:
    """Miles a buyer in this category will travel, or None for national.

    A slug that is not in the map falls back to the default rather than to
    national. Treating an unconfigured category as unbounded is the permissive
    direction, and the failure is silent: a food-service search would start
    ranking Seattle alongside Fort Lauderdale and nothing on screen would say
    why.
    """
    configured = settings.PROSPECT_CATEGORY_RADIUS_MILES or {}
    if category_slug in configured:
        return configured[category_slug]
    return settings.PROSPECT_DEFAULT_RADIUS_MILES


def distance_points(distance_miles: Optional[float],
                    radius_miles: Optional[int]) -> float:
    if radius_miles is None:                       # national — distance is moot
        return DISTANCE_POINTS
    if distance_miles is None or radius_miles <= 0:
        return 0.0
    if distance_miles > radius_miles:
        return 0.0
    # 15 at the door, 7.5 at the edge of the radius. Nearer is better; nothing
    # inside the radius is worth less than half, because it is collectable.
    return DISTANCE_POINTS * (1 - 0.5 * (distance_miles / radius_miles))


def score(line_type: Optional[str], category_confidence: Optional[float],
          distance_miles: Optional[float], category_slug: Optional[str],
          source_count: int = 1) -> int:
    """0..100. Called on every event that changes one of its inputs.

    Rounded to an int because it is a sort key shown to a human, and a queue
    ordered on 63.7142857 invites somebody to read a precision into it that the
    inputs do not carry.
    """
    points = LINE_TYPE_POINTS.get(line_type or "unknown",
                                  LINE_TYPE_POINTS["unknown"])

    # NULL confidence is not zero confidence: the source did not score it. It
    # earns nothing here, which ranks it below a source that did, and that is
    # the intended pressure on whoever writes the next source.
    if category_confidence is not None:
        points += CONFIDENCE_POINTS * max(0.0, min(1.0, float(category_confidence)))

    points += distance_points(distance_miles, radius_for(category_slug))

    extra_sources = max(0, int(source_count or 1) - 1)
    points += min(MAX_CORROBORATION_POINTS,
                  extra_sources * CORROBORATION_POINTS_PER_EXTRA_SOURCE)

    return int(round(min(points, MAX_SCORE)))
