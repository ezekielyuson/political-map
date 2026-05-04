"""Hand-curated list of major corporate PAC sponsors.

Why hand-curated?
-----------------
The FEC publishes a committee master file listing every registered PAC
with its ``connected_organization`` (the corporation behind the PAC). We
*could* take everything labeled "corporate" and create thousands of
Company nodes automatically. We don't, because:

1. Most are obscure. The user-facing "spider web on a map" view wants
   *recognizable* logos -- AT&T, JPMorgan, Boeing -- not "Acme Cement
   Workers PAC."
2. HQ coordinates and domain names aren't in FEC data. We'd have to
   geocode + look up each one. Hand-curating ~30 majors is faster than
   geocoding 3000 minors.

This file is the seed. Adding a company is one line + a build redeploy.

Field guide
-----------
* ``name``        -- canonical display name, used as the Company node's name.
* ``domain``      -- primary website. Drives Clearbit logo lookup
                     (``https://logo.clearbit.com/<domain>``). No API key
                     required for low volume.
* ``hq_city``,
  ``hq_state``    -- where the headquarters is, for human display.
* ``latitude``,
  ``longitude``   -- HQ coordinates for the map.
* ``aliases``     -- substrings to match against FEC's
                     ``connected_organization_name``. Case-insensitive
                     containment. Multiple per company because FEC names
                     vary ("JP MORGAN CHASE", "JPMORGAN CHASE & CO", etc.).

The ``aliases`` field is what binds a Company in this file to a PAC in
the FEC bulk data. Get them wrong and we miss connections; get them too
loose and we cluster unrelated PACs. Tested empirically against the
2024-cycle FEC committee master.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CuratedCompany:
    name: str
    domain: str
    hq_city: str
    hq_state: str
    latitude: float
    longitude: float
    aliases: tuple[str, ...]

    @property
    def slug(self) -> str:
        """Stable id slug for the Company node id (``co:seed:<slug>``)."""
        return self.domain.replace(".", "-")


CURATED_COMPANIES: list[CuratedCompany] = [
    # ----- Tech -----
    CuratedCompany(
        name="Alphabet (Google)",
        domain="google.com",
        hq_city="Mountain View",
        hq_state="CA",
        latitude=37.4220,
        longitude=-122.0841,
        aliases=("GOOGLE", "ALPHABET"),
    ),
    CuratedCompany(
        name="Amazon",
        domain="amazon.com",
        hq_city="Seattle",
        hq_state="WA",
        latitude=47.6228,
        longitude=-122.3361,
        aliases=("AMAZON",),
    ),
    CuratedCompany(
        name="Microsoft",
        domain="microsoft.com",
        hq_city="Redmond",
        hq_state="WA",
        latitude=47.6396,
        longitude=-122.1283,
        aliases=("MICROSOFT",),
    ),
    CuratedCompany(
        name="Meta (Facebook)",
        domain="meta.com",
        hq_city="Menlo Park",
        hq_state="CA",
        latitude=37.4845,
        longitude=-122.1481,
        aliases=("META PLATFORMS", "FACEBOOK"),
    ),
    CuratedCompany(
        name="Apple",
        domain="apple.com",
        hq_city="Cupertino",
        hq_state="CA",
        latitude=37.3349,
        longitude=-122.0090,
        aliases=("APPLE INC",),
    ),
    CuratedCompany(
        name="Oracle",
        domain="oracle.com",
        hq_city="Austin",
        hq_state="TX",
        latitude=30.2240,
        longitude=-97.7350,
        aliases=("ORACLE",),
    ),
    CuratedCompany(
        name="IBM",
        domain="ibm.com",
        hq_city="Armonk",
        hq_state="NY",
        latitude=41.1063,
        longitude=-73.7204,
        aliases=("INTERNATIONAL BUSINESS MACHINES", "IBM CORP"),
    ),

    # ----- Finance -----
    CuratedCompany(
        name="JPMorgan Chase",
        domain="jpmorganchase.com",
        hq_city="New York",
        hq_state="NY",
        latitude=40.7553,
        longitude=-73.9784,
        aliases=("JPMORGAN", "J P MORGAN", "JP MORGAN"),
    ),
    CuratedCompany(
        name="Bank of America",
        domain="bankofamerica.com",
        hq_city="Charlotte",
        hq_state="NC",
        latitude=35.2271,
        longitude=-80.8431,
        aliases=("BANK OF AMERICA",),
    ),
    CuratedCompany(
        name="Goldman Sachs",
        domain="goldmansachs.com",
        hq_city="New York",
        hq_state="NY",
        latitude=40.7141,
        longitude=-74.0142,
        aliases=("GOLDMAN SACHS",),
    ),
    CuratedCompany(
        name="Citigroup",
        domain="citigroup.com",
        hq_city="New York",
        hq_state="NY",
        latitude=40.7197,
        longitude=-74.0123,
        aliases=("CITIGROUP", "CITIBANK"),
    ),
    CuratedCompany(
        name="Wells Fargo",
        domain="wellsfargo.com",
        hq_city="San Francisco",
        hq_state="CA",
        latitude=37.7929,
        longitude=-122.4022,
        aliases=("WELLS FARGO",),
    ),
    CuratedCompany(
        name="BlackRock",
        domain="blackrock.com",
        hq_city="New York",
        hq_state="NY",
        latitude=40.7569,
        longitude=-73.9772,
        aliases=("BLACKROCK",),
    ),

    # ----- Telecom / Media -----
    CuratedCompany(
        name="AT&T",
        domain="att.com",
        hq_city="Dallas",
        hq_state="TX",
        latitude=32.7833,
        longitude=-96.8000,
        aliases=("AT&T", "AT & T"),
    ),
    CuratedCompany(
        name="Verizon",
        domain="verizon.com",
        hq_city="New York",
        hq_state="NY",
        latitude=40.7549,
        longitude=-73.9840,
        aliases=("VERIZON",),
    ),
    CuratedCompany(
        name="Comcast",
        domain="comcast.com",
        hq_city="Philadelphia",
        hq_state="PA",
        latitude=39.9542,
        longitude=-75.1660,
        aliases=("COMCAST",),
    ),
    CuratedCompany(
        name="T-Mobile",
        domain="t-mobile.com",
        hq_city="Bellevue",
        hq_state="WA",
        latitude=47.6101,
        longitude=-122.2015,
        aliases=("T-MOBILE", "T MOBILE"),
    ),
    CuratedCompany(
        name="Walt Disney Co.",
        domain="disney.com",
        hq_city="Burbank",
        hq_state="CA",
        latitude=34.1559,
        longitude=-118.3252,
        aliases=("WALT DISNEY", "DISNEY"),
    ),

    # ----- Defense / Aerospace -----
    CuratedCompany(
        name="Boeing",
        domain="boeing.com",
        hq_city="Arlington",
        hq_state="VA",
        latitude=38.8910,
        longitude=-77.0837,
        aliases=("BOEING",),
    ),
    CuratedCompany(
        name="Lockheed Martin",
        domain="lockheedmartin.com",
        hq_city="Bethesda",
        hq_state="MD",
        latitude=38.9907,
        longitude=-77.0986,
        aliases=("LOCKHEED MARTIN", "LOCKHEED"),
    ),
    CuratedCompany(
        name="RTX (Raytheon)",
        domain="rtx.com",
        hq_city="Arlington",
        hq_state="VA",
        latitude=38.8810,
        longitude=-77.0911,
        aliases=("RAYTHEON", "RTX CORP"),
    ),
    CuratedCompany(
        name="Northrop Grumman",
        domain="northropgrumman.com",
        hq_city="Falls Church",
        hq_state="VA",
        latitude=38.8848,
        longitude=-77.1711,
        aliases=("NORTHROP GRUMMAN",),
    ),
    CuratedCompany(
        name="General Dynamics",
        domain="gd.com",
        hq_city="Reston",
        hq_state="VA",
        latitude=38.9586,
        longitude=-77.3570,
        aliases=("GENERAL DYNAMICS",),
    ),

    # ----- Energy -----
    CuratedCompany(
        name="ExxonMobil",
        domain="exxonmobil.com",
        hq_city="Spring",
        hq_state="TX",
        latitude=30.0850,
        longitude=-95.4500,
        aliases=("EXXON", "EXXONMOBIL"),
    ),
    CuratedCompany(
        name="Chevron",
        domain="chevron.com",
        hq_city="Houston",
        hq_state="TX",
        latitude=29.7355,
        longitude=-95.4280,
        aliases=("CHEVRON",),
    ),
    CuratedCompany(
        name="ConocoPhillips",
        domain="conocophillips.com",
        hq_city="Houston",
        hq_state="TX",
        latitude=29.7460,
        longitude=-95.4621,
        aliases=("CONOCOPHILLIPS", "CONOCO PHILLIPS"),
    ),

    # ----- Healthcare / Pharma -----
    CuratedCompany(
        name="Pfizer",
        domain="pfizer.com",
        hq_city="New York",
        hq_state="NY",
        latitude=40.7530,
        longitude=-73.9714,
        aliases=("PFIZER",),
    ),
    CuratedCompany(
        name="Johnson & Johnson",
        domain="jnj.com",
        hq_city="New Brunswick",
        hq_state="NJ",
        latitude=40.4882,
        longitude=-74.4549,
        aliases=("JOHNSON & JOHNSON", "JOHNSON AND JOHNSON"),
    ),
    CuratedCompany(
        name="UnitedHealth Group",
        domain="unitedhealthgroup.com",
        hq_city="Minnetonka",
        hq_state="MN",
        latitude=44.9097,
        longitude=-93.4622,
        aliases=("UNITEDHEALTH",),
    ),
    CuratedCompany(
        name="Merck",
        domain="merck.com",
        hq_city="Rahway",
        hq_state="NJ",
        latitude=40.6082,
        longitude=-74.2776,
        aliases=("MERCK & CO", "MERCK AND CO", "MERCK,"),
    ),

    # ----- Retail / Logistics -----
    CuratedCompany(
        name="Walmart",
        domain="walmart.com",
        hq_city="Bentonville",
        hq_state="AR",
        latitude=36.3729,
        longitude=-94.2088,
        aliases=("WAL-MART", "WALMART"),
    ),
    CuratedCompany(
        name="UPS",
        domain="ups.com",
        hq_city="Atlanta",
        hq_state="GA",
        latitude=33.8438,
        longitude=-84.3635,
        aliases=("UNITED PARCEL SERVICE", "UPS INC"),
    ),
    CuratedCompany(
        name="FedEx",
        domain="fedex.com",
        hq_city="Memphis",
        hq_state="TN",
        latitude=35.0567,
        longitude=-89.9847,
        aliases=("FEDEX", "FEDERAL EXPRESS"),
    ),
    CuratedCompany(
        name="Ford Motor Co.",
        domain="ford.com",
        hq_city="Dearborn",
        hq_state="MI",
        latitude=42.3223,
        longitude=-83.2367,
        aliases=("FORD MOTOR",),
    ),
    CuratedCompany(
        name="General Motors",
        domain="gm.com",
        hq_city="Detroit",
        hq_state="MI",
        latitude=42.3296,
        longitude=-83.0395,
        aliases=("GENERAL MOTORS",),
    ),

    # ----- Industrials -----
    CuratedCompany(
        name="General Electric",
        domain="ge.com",
        hq_city="Boston",
        hq_state="MA",
        latitude=42.3667,
        longitude=-71.0500,
        aliases=("GENERAL ELECTRIC",),
    ),
    CuratedCompany(
        name="Honeywell",
        domain="honeywell.com",
        hq_city="Charlotte",
        hq_state="NC",
        latitude=35.2271,
        longitude=-80.8418,
        aliases=("HONEYWELL",),
    ),
    CuratedCompany(
        name="Caterpillar",
        domain="caterpillar.com",
        hq_city="Irving",
        hq_state="TX",
        latitude=32.8723,
        longitude=-96.9356,
        aliases=("CATERPILLAR",),
    ),
]


def find_for_organization(connected_org: str | None) -> CuratedCompany | None:
    """Match an FEC ``connected_organization_name`` to a curated company.

    Substring match, case-insensitive. The first matching alias wins;
    aliases are ordered specific-to-generic in the data above so that
    ``"AT&T"`` doesn't accidentally match ``"AT&T MOBILITY"`` and vice
    versa (though for our purposes both are AT&T anyway).
    """
    if not connected_org:
        return None
    haystack = connected_org.upper()
    for company in CURATED_COMPANIES:
        for alias in company.aliases:
            if alias.upper() in haystack:
                return company
    return None
