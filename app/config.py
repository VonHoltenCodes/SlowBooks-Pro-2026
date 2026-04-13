# ============================================================================
# Decompiled from qbw32.exe!CQBPreferences + CCompanyInfo
# Offset: 0x0023F000 (Prefs) / 0x00241200 (CompanyInfo)
# Original stored in Windows Registry: HKCU\Software\Intuit\QuickBooks\12.0
# and in the .QBW file header (first 512 bytes, encrypted with XOR 0x1F).
# We moved everything to .env because it's 2026 and registry is not a config.
# ============================================================================

import os
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def build_database_url(env: dict | None = None) -> str:
    env = env or os.environ
    host = env.get("POSTGRES_HOST", "localhost")
    port = env.get("POSTGRES_PORT", "5432")
    dbname = env.get("POSTGRES_DB", "bookkeeper")
    user = quote_plus(env.get("POSTGRES_USER", "bookkeeper"))
    password = quote_plus(env.get("POSTGRES_PASSWORD", "bookkeeper"))
    sslmode = env.get("POSTGRES_SSLMODE", "disable")

    url = f"postgresql://{user}:{password}@{host}:{port}/{dbname}"
    if sslmode:
        url += f"?sslmode={sslmode}"
    return url


def resolve_database_url(env: dict | None = None) -> str:
    env = env or os.environ
    explicit_url = (env.get("DATABASE_URL") or "").strip()
    if explicit_url:
        return explicit_url
    return build_database_url(env)


DATABASE_URL = resolve_database_url()
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "3001"))
APP_DEBUG = os.getenv("APP_DEBUG", "false").lower() == "true"

# CCompanyInfo fields — originally at .QBW header offset 0x40
COMPANY_NAME = os.getenv("COMPANY_NAME", "My Company")
COMPANY_ADDRESS = os.getenv("COMPANY_ADDRESS", "")
COMPANY_PHONE = os.getenv("COMPANY_PHONE", "")
COMPANY_EMAIL = os.getenv("COMPANY_EMAIL", "")
DEFAULT_TERMS = os.getenv("DEFAULT_TERMS", "Net 30")
DEFAULT_TAX_RATE = float(os.getenv("DEFAULT_TAX_RATE", "0.0"))
