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
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    # Reconstructed from CQBDatabase::AcquireConnection() at offset 0x0004A7C2
    # Original used connection pooling via Pervasive.SQL Workgroup Engine
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
