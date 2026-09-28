"""Where a national search is centred, when "national" has to mean coverage.

## Why a national group needs fifty searches per term

Text Search returns at most three pages of twenty. "lighting store in the United
States" is therefore sixty businesses out of several thousand, ranked by
whatever Google thinks is relevant to a query with no location — which in
practice is a handful of big-city showrooms. National *coverage* means asking
the same question centred on many places, so a Text Search per metro is sixty
per metro rather than sixty per country.

A metro sweep is geography, not a radius. The groups that use these still state
`radius_miles=None`: a lighting store in Tulsa is exactly as much a buyer as one
in Broward, and the sweep only decides where Google looks.

## Overlap is expected and is not a second dedup's problem

Neighbouring centres (New York and Philadelphia, San Francisco and San Jose,
Baltimore and Washington) return some of the same stores. Those land on the
existing phone-unique path in `record_prospect()`: one prospect, corroborated.
Nothing here tries to de-overlap the circles, because the bias radius is 50 km
and pruning centres to avoid it would leave gaps between them instead.

## The list

The 50 most populous US Metropolitan Statistical Areas, from the U.S. Census
Bureau's Vintage 2023 population estimates (table CBSA-EST2023-POP), in that
order. Each centre is the principal city's downtown — the first city in the MSA
title — because that is where the bias circle catches the densest retail. The
tail of the ranking moves by a place or two between vintages (Buffalo, Hartford
and Grand Rapids trade 49th-51st); what matters here is coverage, not rank.

`Sweep` lives here rather than in `taxonomy.py` so the taxonomy can import this
list without a cycle; `taxonomy.Sweep` is re-exported and remains the name the
rest of the code uses.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Sweep:
    """Where one search is centred. `center=None` is a national search."""

    name: str
    label: str
    center: Optional[Tuple[float, float]] = None


def _metro(name: str, label: str, lat: float, lon: float) -> Sweep:
    # Prefixed so a metro can never share a ledger key with "home", "national"
    # or "gulf_coast" — the sweep name is part of `Search.ledger_key`, and two
    # sweeps with one name would make the second look already-run.
    return Sweep(f"metro_{name}", label, (lat, lon))


METRO_SWEEPS: Tuple[Sweep, ...] = (
    _metro("new_york", "New York metro", 40.7128, -74.0060),
    _metro("los_angeles", "Los Angeles metro", 34.0522, -118.2437),
    _metro("chicago", "Chicago metro", 41.8781, -87.6298),
    _metro("dallas", "Dallas-Fort Worth metro", 32.7767, -96.7970),
    _metro("houston", "Houston metro", 29.7604, -95.3698),
    _metro("atlanta", "Atlanta metro", 33.7490, -84.3880),
    _metro("washington", "Washington DC metro", 38.9072, -77.0369),
    _metro("philadelphia", "Philadelphia metro", 39.9526, -75.1652),
    _metro("miami", "Miami-Fort Lauderdale metro", 25.7617, -80.1918),
    _metro("phoenix", "Phoenix metro", 33.4484, -112.0740),
    _metro("boston", "Boston metro", 42.3601, -71.0589),
    _metro("riverside", "Riverside-San Bernardino metro", 33.9533, -117.3962),
    _metro("san_francisco", "San Francisco Bay metro", 37.7749, -122.4194),
    _metro("detroit", "Detroit metro", 42.3314, -83.0458),
    _metro("seattle", "Seattle metro", 47.6062, -122.3321),
    _metro("minneapolis", "Minneapolis-St. Paul metro", 44.9778, -93.2650),
    _metro("tampa", "Tampa Bay metro", 27.9506, -82.4572),
    _metro("san_diego", "San Diego metro", 32.7157, -117.1611),
    _metro("denver", "Denver metro", 39.7392, -104.9903),
    _metro("baltimore", "Baltimore metro", 39.2904, -76.6122),
    _metro("st_louis", "St. Louis metro", 38.6270, -90.1994),
    _metro("orlando", "Orlando metro", 28.5383, -81.3792),
    _metro("charlotte", "Charlotte metro", 35.2271, -80.8431),
    _metro("san_antonio", "San Antonio metro", 29.4241, -98.4936),
    _metro("portland", "Portland metro", 45.5152, -122.6784),
    _metro("austin", "Austin metro", 30.2672, -97.7431),
    _metro("sacramento", "Sacramento metro", 38.5816, -121.4944),
    _metro("pittsburgh", "Pittsburgh metro", 40.4406, -79.9959),
    _metro("las_vegas", "Las Vegas metro", 36.1699, -115.1398),
    _metro("cincinnati", "Cincinnati metro", 39.1031, -84.5120),
    _metro("kansas_city", "Kansas City metro", 39.0997, -94.5786),
    _metro("columbus", "Columbus metro", 39.9612, -82.9988),
    _metro("indianapolis", "Indianapolis metro", 39.7684, -86.1581),
    _metro("cleveland", "Cleveland metro", 41.4993, -81.6944),
    _metro("nashville", "Nashville metro", 36.1627, -86.7816),
    _metro("san_jose", "San Jose metro", 37.3382, -121.8863),
    _metro("virginia_beach", "Virginia Beach-Norfolk metro", 36.8529, -75.9780),
    _metro("jacksonville", "Jacksonville metro", 30.3322, -81.6557),
    _metro("providence", "Providence metro", 41.8240, -71.4128),
    _metro("raleigh", "Raleigh metro", 35.7796, -78.6382),
    _metro("milwaukee", "Milwaukee metro", 43.0389, -87.9065),
    _metro("oklahoma_city", "Oklahoma City metro", 35.4676, -97.5164),
    _metro("louisville", "Louisville metro", 38.2527, -85.7585),
    _metro("memphis", "Memphis metro", 35.1495, -90.0490),
    _metro("richmond", "Richmond metro", 37.5407, -77.4360),
    _metro("salt_lake_city", "Salt Lake City metro", 40.7608, -111.8910),
    _metro("birmingham", "Birmingham metro", 33.5186, -86.8104),
    _metro("fresno", "Fresno metro", 36.7378, -119.7871),
    _metro("buffalo", "Buffalo metro", 42.8864, -78.8784),
    _metro("hartford", "Hartford metro", 41.7658, -72.6734),
)
