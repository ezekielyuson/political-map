"""US state capital coordinates.

Used to geocode politicians at the state level: senators land at their
state capital, House reps land there too as a coarse approximation
(district centroids are a v2 enhancement).

Data source: public domain (USGS / Census). Hand-keyed for accuracy.
"""

from __future__ import annotations

# (state_code, capital_name, latitude, longitude)
STATE_CAPITALS: dict[str, tuple[str, float, float]] = {
    "AL": ("Montgomery", 32.3792, -86.3077),
    "AK": ("Juneau", 58.3019, -134.4197),
    "AZ": ("Phoenix", 33.4484, -112.0740),
    "AR": ("Little Rock", 34.7465, -92.2896),
    "CA": ("Sacramento", 38.5816, -121.4944),
    "CO": ("Denver", 39.7392, -104.9903),
    "CT": ("Hartford", 41.7658, -72.6734),
    "DE": ("Dover", 39.1582, -75.5244),
    "FL": ("Tallahassee", 30.4383, -84.2807),
    "GA": ("Atlanta", 33.7490, -84.3880),
    "HI": ("Honolulu", 21.3099, -157.8581),
    "ID": ("Boise", 43.6150, -116.2023),
    "IL": ("Springfield", 39.7817, -89.6501),
    "IN": ("Indianapolis", 39.7684, -86.1581),
    "IA": ("Des Moines", 41.5868, -93.6250),
    "KS": ("Topeka", 39.0473, -95.6752),
    "KY": ("Frankfort", 38.2009, -84.8733),
    "LA": ("Baton Rouge", 30.4515, -91.1871),
    "ME": ("Augusta", 44.3106, -69.7795),
    "MD": ("Annapolis", 38.9784, -76.4922),
    "MA": ("Boston", 42.3601, -71.0589),
    "MI": ("Lansing", 42.7325, -84.5555),
    "MN": ("Saint Paul", 44.9537, -93.0900),
    "MS": ("Jackson", 32.2988, -90.1848),
    "MO": ("Jefferson City", 38.5767, -92.1735),
    "MT": ("Helena", 46.5891, -112.0391),
    "NE": ("Lincoln", 40.8136, -96.7026),
    "NV": ("Carson City", 39.1638, -119.7674),
    "NH": ("Concord", 43.2081, -71.5376),
    "NJ": ("Trenton", 40.2206, -74.7597),
    "NM": ("Santa Fe", 35.6870, -105.9378),
    "NY": ("Albany", 42.6526, -73.7562),
    "NC": ("Raleigh", 35.7796, -78.6382),
    "ND": ("Bismarck", 46.8083, -100.7837),
    "OH": ("Columbus", 39.9612, -82.9988),
    "OK": ("Oklahoma City", 35.4676, -97.5164),
    "OR": ("Salem", 44.9429, -123.0351),
    "PA": ("Harrisburg", 40.2732, -76.8867),
    "RI": ("Providence", 41.8240, -71.4128),
    "SC": ("Columbia", 34.0007, -81.0348),
    "SD": ("Pierre", 44.3683, -100.3510),
    "TN": ("Nashville", 36.1627, -86.7816),
    "TX": ("Austin", 30.2672, -97.7431),
    "UT": ("Salt Lake City", 40.7608, -111.8910),
    "VT": ("Montpelier", 44.2601, -72.5754),
    "VA": ("Richmond", 37.5407, -77.4360),
    "WA": ("Olympia", 47.0379, -122.9007),
    "WV": ("Charleston", 38.3498, -81.6326),
    "WI": ("Madison", 43.0731, -89.4012),
    "WY": ("Cheyenne", 41.1400, -104.8202),
    # DC + territories (for non-voting delegates)
    "DC": ("Washington", 38.9072, -77.0369),
    "PR": ("San Juan", 18.4655, -66.1057),
    "VI": ("Charlotte Amalie", 18.3358, -64.9307),
    "GU": ("Hagatna", 13.4745, 144.7504),
    "AS": ("Pago Pago", -14.2756, -170.7020),
    "MP": ("Saipan", 15.1850, 145.7467),
}


def lookup(state_code: str | None) -> tuple[float, float] | None:
    """Return (lat, lng) for a USPS state code, or None if unknown."""
    if not state_code:
        return None
    entry = STATE_CAPITALS.get(state_code.upper())
    return (entry[1], entry[2]) if entry else None
