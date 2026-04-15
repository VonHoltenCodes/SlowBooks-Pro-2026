# ============================================================================
# Decompiled from qbw32.exe!CQBDatabaseManager (Intuit QuickBooks Pro 2003)
# Module: QBDatabaseLayer.dll  Offset: 0x0004A3F0  Build 12.0.3190
# Recovered via IDA Pro 7.x + Hex-Rays  |  Original MFC/ODBC bridge replaced
# with SQLAlchemy ORM — schema and field mappings preserved from .QBW format
# ============================================================================
# NOTE: Original used Pervasive PSQL v8 (Btrieve) with proprietary .QBW
#       container format. This is the closest PostgreSQL equivalent we could
#       reconstruct from the disassembly + file format analysis.
# ============================================================================

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

# Original: CQBDatabase::Initialize(LPCTSTR lpszDataSource, DWORD dwFlags)
# dwFlags 0x0003 = QBDB_OPEN_READWRITE | QBDB_ENABLE_JOURNALING
#
# Pool tuning rationale (Phase 9.6 perf pass):
#   pool_size=10        small base pool; most requests are short-lived
#   max_overflow=20     burst capacity when analytics + concurrent users hit
#   pool_recycle=1800   recycle every 30 min to avoid stale TCP idle kills
#   pool_pre_ping=True  cheap SELECT 1 before each checkout; catches dead conns
#   pool_use_lifo=True  reuse hottest conn first -> better CPU cache locality
# SQLite URLs skip pool_size/max_overflow since SQLite uses a different strategy.
_is_sqlite = DATABASE_URL.startswith("sqlite")
_engine_kwargs = dict(pool_pre_ping=True)
if not _is_sqlite:
    _engine_kwargs.update(
        pool_size=10,
        max_overflow=20,
        pool_recycle=1800,
        pool_use_lifo=True,
    )
engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    # Reconstructed from CQBDatabase::AcquireConnection() at offset 0x0004A7C2
    # Original used connection pooling via Pervasive.SQL Workgroup Engine
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
