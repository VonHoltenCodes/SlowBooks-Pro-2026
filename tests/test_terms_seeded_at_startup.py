"""A database create_all built on its own (a native install started without
`alembic upgrade head`) still starts with the terms the forms always offered
(#664, 2.31.5 gate): the app's startup seeds an empty list, and only an
empty one. Tested the way the other startup checks are, on the helper the
lifespan calls."""

from app.main import _seed_payment_terms
from app.models.payment_terms import PaymentTerm
from app.services import payment_terms as parser


def test_startup_seeds_an_empty_terms_list(db_session):
    assert db_session.query(PaymentTerm).count() == 0
    _seed_payment_terms(db_session)
    names = {t.name for t in db_session.query(PaymentTerm).all()}
    assert {name for name, _ in parser.SEED} <= names


def test_the_seed_leaves_a_list_in_use_alone(db_session):
    db_session.add(PaymentTerm(name="Net 7", kind=parser.STANDARD, net_days=7))
    db_session.commit()
    _seed_payment_terms(db_session)
    assert [t.name for t in db_session.query(PaymentTerm).all()] == ["Net 7"]


def test_the_lifespan_calls_the_seed():
    import inspect

    import app.main as main

    assert "_seed_payment_terms(_db)" in inspect.getsource(main.lifespan)
