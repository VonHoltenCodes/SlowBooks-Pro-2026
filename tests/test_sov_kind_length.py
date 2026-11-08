"""Every schedule-of-values kind fits its column (SQLite never checks a
String length; PostgreSQL refused "general_conditions" in a String(16))."""

from app.models.jobs import SOV_KINDS, SovLine


def test_every_sov_kind_fits_the_kind_column():
    length = SovLine.__table__.c.kind.type.length
    too_long = [k for k in SOV_KINDS if len(k) > length]
    assert too_long == [], f"longer than String({length}): {too_long}"
