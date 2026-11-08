# Seed data for #550. The two-level division/section numbering. The division titles ship here;
# reserved division numbers (15-20, 24, 29, 30, 36-39, 47, 49) have no title
# and are not created (a company can add them). The full numbered section
# catalogue isn't ours to publish, so only a modest set of common sections
# ships — companies import their own full list through
# POST /api/cost-codes/import.

import re

from sqlalchemy.orm import Session

DIVISIONS = (
    ("00", "Procurement and Contracting Requirements"),
    ("01", "General Requirements"),
    ("02", "Existing Conditions"),
    ("03", "Concrete"),
    ("04", "Masonry"),
    ("05", "Metals"),
    ("06", "Wood, Plastics, and Composites"),
    ("07", "Thermal and Moisture Protection"),
    ("08", "Openings"),
    ("09", "Finishes"),
    ("10", "Specialties"),
    ("11", "Equipment"),
    ("12", "Furnishings"),
    ("13", "Special Construction"),
    ("14", "Conveying Equipment"),
    ("21", "Fire Suppression"),
    ("22", "Plumbing"),
    ("23", "Heating, Ventilating, and Air Conditioning (HVAC)"),
    ("25", "Integrated Automation"),
    ("26", "Electrical"),
    ("27", "Communications"),
    ("28", "Electronic Safety and Security"),
    ("31", "Earthwork"),
    ("32", "Exterior Improvements"),
    ("33", "Utilities"),
    ("34", "Transportation"),
    ("35", "Waterway and Marine Construction"),
    ("40", "Process Interconnections"),
    ("41", "Material Processing and Handling Equipment"),
    ("42", "Process Heating, Cooling, and Drying Equipment"),
    ("43", "Process Gas and Liquid Handling, Purification, and Storage Equipment"),
    ("44", "Pollution and Waste Control Equipment"),
    ("45", "Industry-Specific Manufacturing Equipment"),
    ("46", "Water and Wastewater Equipment"),
    ("48", "Electrical Power Generation"),
)

# Common sections ("DD SS SS", parent = the division). A modest, widely used set.
SECTIONS = (
    ("01 10 00", "Summary"),
    ("01 20 00", "Price and Payment Procedures"),
    ("01 30 00", "Administrative Requirements"),
    ("01 40 00", "Quality Requirements"),
    ("01 50 00", "Temporary Facilities and Controls"),
    ("01 60 00", "Product Requirements"),
    ("01 70 00", "Execution and Closeout Requirements"),
    ("02 41 00", "Demolition"),
    ("02 41 19", "Selective Demolition"),
    ("03 10 00", "Concrete Forming and Accessories"),
    ("03 20 00", "Concrete Reinforcing"),
    ("03 30 00", "Cast-in-Place Concrete"),
    ("03 40 00", "Precast Concrete"),
    ("04 20 00", "Unit Masonry"),
    ("04 40 00", "Stone Assemblies"),
    ("05 10 00", "Structural Metal Framing"),
    ("05 30 00", "Metal Decking"),
    ("05 50 00", "Metal Fabrications"),
    ("06 10 00", "Rough Carpentry"),
    ("06 20 00", "Finish Carpentry"),
    ("06 40 00", "Architectural Woodwork"),
    ("07 10 00", "Dampproofing and Waterproofing"),
    ("07 20 00", "Thermal Protection"),
    ("07 40 00", "Roofing and Siding Panels"),
    ("07 50 00", "Membrane Roofing"),
    ("07 60 00", "Flashing and Sheet Metal"),
    ("07 90 00", "Joint Protection"),
    ("08 10 00", "Doors and Frames"),
    ("08 30 00", "Specialty Doors and Frames"),
    ("08 40 00", "Entrances, Storefronts, and Curtain Walls"),
    ("08 50 00", "Windows"),
    ("08 70 00", "Hardware"),
    ("08 80 00", "Glazing"),
    ("09 20 00", "Plaster and Gypsum Board"),
    ("09 21 16", "Gypsum Board Assemblies"),
    ("09 30 00", "Tiling"),
    ("09 50 00", "Ceilings"),
    ("09 60 00", "Flooring"),
    ("09 90 00", "Painting and Coating"),
    ("10 10 00", "Information Specialties"),
    ("10 20 00", "Interior Specialties"),
    ("10 40 00", "Safety Specialties"),
    ("11 30 00", "Residential Equipment"),
    ("11 40 00", "Foodservice Equipment"),
    ("12 30 00", "Casework"),
    ("12 50 00", "Furniture"),
    ("14 20 00", "Elevators"),
    ("21 10 00", "Water-Based Fire-Suppression Systems"),
    ("22 10 00", "Plumbing Piping"),
    ("22 40 00", "Plumbing Fixtures"),
    ("23 00 00", "Heating, Ventilating, and Air Conditioning (HVAC)"),
    ("23 05 00", "Common Work Results for HVAC"),
    ("23 30 00", "HVAC Air Distribution"),
    ("23 80 00", "Decentralized HVAC Equipment"),
    ("26 05 00", "Common Work Results for Electrical"),
    ("26 20 00", "Low-Voltage Electrical Transmission"),
    ("26 50 00", "Lighting"),
    ("27 10 00", "Structured Cabling"),
    ("28 10 00", "Access Control"),
    ("28 46 00", "Fire Detection and Alarm"),
    ("31 10 00", "Site Clearing"),
    ("31 20 00", "Earth Moving"),
    ("31 60 00", "Special Foundations and Load-Bearing Elements"),
    ("32 10 00", "Bases, Ballasts, and Paving"),
    ("32 30 00", "Site Improvements"),
    ("32 31 00", "Fences and Gates"),
    ("32 31 11", "Gate Operators"),
    ("32 31 13", "Chain Link Fences and Gates"),
    ("32 31 19", "Decorative Metal Fences and Gates"),
    ("32 31 23", "Plastic Fences and Gates"),
    ("32 31 29", "Wood Fences and Gates"),
    ("32 90 00", "Planting"),
    ("33 10 00", "Water Utilities"),
    ("33 30 00", "Sanitary Sewerage Utilities"),
    ("33 40 00", "Storm Drainage Utilities"),
)

# Default trade packages -> the sections they bid. Packages are what subs bid
# and get awarded; sections are what costs post to. A package can span
# divisions; a division can be split across packages.
TRADE_PACKAGES = (
    (
        "General Conditions",
        (
            "01 10 00",
            "01 20 00",
            "01 30 00",
            "01 40 00",
            "01 50 00",
            "01 60 00",
            "01 70 00",
        ),
    ),
    ("Demolition", ("02 41 00", "02 41 19")),
    ("Concrete", ("03 10 00", "03 20 00", "03 30 00", "03 40 00")),
    ("Masonry", ("04 20 00", "04 40 00")),
    ("Structural Steel and Misc. Metals", ("05 10 00", "05 30 00", "05 50 00")),
    ("Rough Carpentry", ("06 10 00",)),
    ("Finish Carpentry and Millwork", ("06 20 00", "06 40 00", "12 30 00")),
    ("Waterproofing and Insulation", ("07 10 00", "07 20 00")),
    ("Roofing and Sheet Metal", ("07 40 00", "07 50 00", "07 60 00")),
    ("Joint Sealants", ("07 90 00",)),
    ("Doors, Frames and Hardware", ("08 10 00", "08 30 00", "08 70 00")),
    ("Glass and Glazing", ("08 40 00", "08 50 00", "08 80 00")),
    ("Drywall and Ceilings", ("09 20 00", "09 21 16", "09 50 00")),
    ("Tile", ("09 30 00",)),
    ("Flooring", ("09 60 00",)),
    ("Painting", ("09 90 00",)),
    ("Specialties", ("10 10 00", "10 20 00", "10 40 00")),
    ("Equipment and Furnishings", ("11 30 00", "11 40 00", "12 50 00")),
    ("Elevators", ("14 20 00",)),
    ("Fire Protection", ("21 10 00",)),
    ("Plumbing", ("22 10 00", "22 40 00")),
    ("HVAC", ("23 00 00", "23 05 00", "23 30 00", "23 80 00")),
    ("Electrical", ("26 05 00", "26 20 00", "26 50 00")),
    ("Low Voltage and Life Safety", ("27 10 00", "28 10 00", "28 46 00")),
    ("Earthwork", ("31 10 00", "31 20 00", "31 60 00")),
    ("Paving and Site Improvements", ("32 10 00", "32 30 00")),
    (
        "Fences and Gates",
        ("32 31 00", "32 31 11", "32 31 13", "32 31 19", "32 31 23", "32 31 29"),
    ),
    ("Landscaping", ("32 90 00",)),
    ("Site Utilities", ("33 10 00", "33 30 00", "33 40 00")),
)

# A division title by its two-digit number, so a section whose division is
# not yet in the chart can create it on the fly.
DIVISION_TITLES = dict(DIVISIONS)

# ---------------------------------------------------------------------------
# Code shape: "DD SS SS" with an optional ".SS" extension. A division is
# exactly two digits as typed; a section is all digits and separators with 6
# (DD SS SS) or 8 (with a .SS extension) digits. Anything else — a custom
# chart's "1000", "0330", "1.5", "L-100", "EQ" — is left exactly as typed, so
# a company that numbers its own way is never rewritten.
# ---------------------------------------------------------------------------

_MF_SHAPE = re.compile(r"^[\d\s.\-]+$")
_DIVISION = re.compile(r"^\d{2}$")
_SECTION = re.compile(r"^\d{2} \d{2} \d{2}(\.\d{2})?$")


def normalize_code(raw: str) -> str:
    """Normalize a typed cost code to 'DD SS SS' (optional '.SS'). Only a
    two-digit division or a 6-/8-digit section is reshaped; everything else is
    returned stripped but otherwise as typed, so numeric custom charts
    ('1000', '0330', '1.5') are never mangled."""
    s = (raw or "").strip()
    if not s:
        return s
    if _DIVISION.match(s):  # a division is exactly two digits as typed
        return s
    if _MF_SHAPE.match(s):
        digits = re.sub(r"\D", "", s)
        if len(digits) == 6:
            return f"{digits[0:2]} {digits[2:4]} {digits[4:6]}"
        if len(digits) == 8:
            return f"{digits[0:2]} {digits[2:4]} {digits[4:6]}.{digits[6:8]}"
    return s


def code_level(code: str) -> str | None:
    """'division', 'section', or None for a code that isn't division/section
    shaped."""
    c = (code or "").strip()
    if _DIVISION.match(c):
        return "division"
    if _SECTION.match(c):
        return "section"
    return None


def division_of(code: str) -> str | None:
    """The division code 'DD' a normalized section belongs under, else None."""
    if code_level(code) == "section":
        return code[:2]
    return None


def ensure_division(db: Session, number: str, cache: dict | None = None):
    """The division row a section parents to, created from the title table
    when the chart doesn't have it. `cache` (code.lower() -> CostCode), when
    given, is read and updated in place so a bulk load avoids re-querying."""
    from app.models.cost_codes import CostCode

    number = (number or "")[:20]
    key = number.lower()
    if cache is not None and key in cache:
        return cache[key]
    row = db.query(CostCode).filter(CostCode.code.ilike(number)).first()
    if row is None:
        row = CostCode(
            code=number,
            name=DIVISION_TITLES.get(number, number),
            cost_type="other",
            level="division",
        )
        db.add(row)
        db.flush()
    if cache is not None:
        cache[key] = row
    return row


def default_package_id(db: Session, code) -> int | None:
    """The trade package a new section should join, or None: the one the
    standard catalog puts it in (by package name), else the single package its
    division's other sections already belong to. None when the section is not
    a section, is already mapped, or the choice is not exactly one package."""
    from app.models.cost_codes import CostCode
    from app.models.trade_packages import TradePackage, TradePackageSection

    if code.level != "section" or not db.query(TradePackage.id).first():
        return None
    if (
        db.query(TradePackageSection)
        .filter(TradePackageSection.cost_code_id == code.id)
        .first()
    ):
        return None
    for name, section_codes in TRADE_PACKAGES:
        if code.code in {normalize_code(c) for c in section_codes}:
            pkg = db.query(TradePackage).filter(TradePackage.name == name).first()
            if pkg is not None:
                return pkg.id
    div = division_of(code.code)
    if div is None:
        return None
    siblings = db.query(CostCode.id).filter(
        CostCode.level == "section",
        CostCode.code.like(f"{div} %"),
        CostCode.id != code.id,
    )
    ids = {
        row[0]
        for row in db.query(TradePackageSection.package_id).filter(
            TradePackageSection.cost_code_id.in_(siblings)
        )
    }
    return ids.pop() if len(ids) == 1 else None


def map_new_section(db: Session, code) -> None:
    """Put a newly created section on its default package, so a bid line on it
    lands in the Buyout and the schedule of values without a trip to Settings."""
    from app.models.trade_packages import TradePackageSection

    pkg_id = default_package_id(db, code)
    if pkg_id is not None:
        db.add(TradePackageSection(package_id=pkg_id, cost_code_id=code.id))
        db.flush()


def load_standard_catalog(db: Session) -> dict:
    """Load the division titles and the common sections: divisions first,
    then each section parented to its division (created from the title table
    when missing). Codes already present are skipped, so it is safe to
    repeat. Seeded codes carry cost_type 'other' — the per-line type is what
    reports read."""
    from app.models.cost_codes import CostCode

    by_code = {c.code.lower(): c for c in db.query(CostCode).all()}
    created = 0

    for code, name in DIVISIONS:
        if code.lower() not in by_code:
            created += 1
        ensure_division(db, code, by_code)

    for raw, name in SECTIONS:
        code = normalize_code(raw)
        if code.lower() in by_code:
            continue
        div = division_of(code)
        parent = None
        if div:
            if div.lower() not in by_code:
                created += 1
            parent = ensure_division(db, div, by_code)
        row = CostCode(
            code=code,
            name=name,
            cost_type="other",
            level="section",
            parent_id=parent.id if parent else None,
        )
        db.add(row)
        db.flush()
        by_code[code.lower()] = row
        map_new_section(db, row)
        created += 1

    db.flush()
    return {"created": created}


def seed_trade_packages(db: Session) -> int:
    """Seed the default trade packages and their section mappings, once, when
    the chart has at least one section and the company has no packages yet.
    Maps each package by section code and skips a code the chart doesn't have.
    Returns the number of packages created."""
    from app.models.cost_codes import CostCode
    from app.models.trade_packages import TradePackage, TradePackageSection

    if db.query(TradePackage.id).first():
        return 0
    # Nothing to bid against without sections, so a custom (non-standard)
    # chart gets no empty packages.
    if not db.query(CostCode.id).filter(CostCode.level == "section").first():
        return 0
    by_code = {c.code.lower(): c for c in db.query(CostCode).all()}
    created = 0
    for order, (name, section_codes) in enumerate(TRADE_PACKAGES):
        pkg = TradePackage(name=name, sort_order=order)
        db.add(pkg)
        db.flush()
        for raw in section_codes:
            code = by_code.get(normalize_code(raw).lower())
            if code is not None:
                db.add(TradePackageSection(package_id=pkg.id, cost_code_id=code.id))
        created += 1
    db.flush()
    return created
