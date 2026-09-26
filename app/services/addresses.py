"""Addresses as the sales documents print them.

The invoice, estimate and statement templates each wrote
``{{ city }}, {{ state }} {{ zip }}``, so an address with no state or ZIP
printed "Port Alder," with a comma hanging off it, and a customer abroad
got no country at all — Bäckerei Müller in München printed as if it were
in the United States (2.17.3 exploratory F21, W-L2). Registered as Jinja
globals by pdf_service.
"""

# ISO code -> name, the same list the customer form offers
# (COUNTRIES in app/static/js/utils.js).
COUNTRY_NAMES = {
    "US": "United States",
    "CA": "Canada",
    "IE": "Ireland",
    "GB": "United Kingdom",
    "AU": "Australia",
    "AR": "Argentina",
    "AT": "Austria",
    "BE": "Belgium",
    "BR": "Brazil",
    "BG": "Bulgaria",
    "CL": "Chile",
    "CN": "China",
    "CO": "Colombia",
    "HR": "Croatia",
    "CZ": "Czech Republic",
    "DK": "Denmark",
    "EG": "Egypt",
    "EE": "Estonia",
    "FI": "Finland",
    "FR": "France",
    "DE": "Germany",
    "GR": "Greece",
    "HK": "Hong Kong",
    "HU": "Hungary",
    "IS": "Iceland",
    "IN": "India",
    "ID": "Indonesia",
    "IL": "Israel",
    "IT": "Italy",
    "JP": "Japan",
    "KE": "Kenya",
    "LV": "Latvia",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "MY": "Malaysia",
    "MX": "Mexico",
    "MA": "Morocco",
    "NL": "Netherlands",
    "NZ": "New Zealand",
    "NG": "Nigeria",
    "NO": "Norway",
    "PK": "Pakistan",
    "PE": "Peru",
    "PH": "Philippines",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SA": "Saudi Arabia",
    "SG": "Singapore",
    "SK": "Slovakia",
    "SI": "Slovenia",
    "ZA": "South Africa",
    "KR": "South Korea",
    "ES": "Spain",
    "SE": "Sweden",
    "CH": "Switzerland",
    "TW": "Taiwan",
    "TH": "Thailand",
    "TR": "Turkey",
    "UA": "Ukraine",
    "AE": "United Arab Emirates",
    "UY": "Uruguay",
    "VN": "Vietnam",
}

# The home country, however it was typed: never printed.
_HOME = {"US", "USA", "U.S.", "U.S.A.", "UNITED STATES", "UNITED STATES OF AMERICA"}


def city_line(city, state, zip_code) -> str:
    """The line under the street: "Port Alder, OR 97000"; without a state or
    ZIP just "Port Alder"; with no city, "OR 97000"."""
    city = (city or "").strip()
    region = " ".join(p for p in ((state or "").strip(), (zip_code or "").strip()) if p)
    if city and region:
        return f"{city}, {region}"
    return city or region


def country_line(country) -> str:
    """The country to print under an address — its name for a code the
    customer form stores ("DE" -> "Germany"), what was typed otherwise — or
    "" for the home country (the United States) and for none."""
    value = (country or "").strip()
    if not value or value.upper() in _HOME:
        return ""
    return COUNTRY_NAMES.get(value.upper(), value)
