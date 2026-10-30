# ============================================================================
# MCP server settings, from the environment. The token comes from a file and
# nowhere else: an argument or an environment variable would show up in a
# process list or an agent's config, and a file can be made private (0600).
# Error sentences name the problem and the fix, never the token's content.
# ============================================================================

import math
import os
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_URL = "http://127.0.0.1:3001"
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class ConfigError(ValueError):
    """A setting the server cannot start with; the message is the sentence."""


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str = ""
    timeout: float = 120.0

    def __repr__(self) -> str:  # keep the token out of any log line
        return f"Config(base_url={self.base_url!r}, timeout={self.timeout})"


def _base_url(raw: str) -> str:
    sentence = (
        "SLOWBOOKS_URL must be a loopback http:// address, "
        "like http://127.0.0.1:3001."
    )
    try:
        parts = urlsplit(raw.strip())
        host, _ = parts.hostname, parts.port
    except ValueError:
        raise ConfigError(sentence) from None
    if (
        any(c.isspace() for c in raw.strip())
        or "?" in raw
        or "#" in raw
        or parts.scheme != "http"
        or host not in _LOOPBACK_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ConfigError(sentence)
    return raw.strip().rstrip("/")


def _windows() -> bool:
    return os.name == "nt"


def _decode(raw: bytes) -> str:
    """UTF-8 (BOM or not) or UTF-16 with a BOM, which PowerShell writes."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def _under_profile(path: str) -> bool:
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")

    def norm(p):
        # realpath follows a junction or symlink and expands an 8.3 name.
        return os.path.normcase(os.path.realpath(p)).lower()

    root, target = norm(profile), norm(path)
    return target == root or target.startswith(root.rstrip("\\/") + os.sep)


def _read_token(raw_path: str) -> str:
    if not raw_path.strip():
        raise ConfigError(
            "SLOWBOOKS_TOKEN_FILE must name a file that holds the API token "
            "(create one under Settings, API Tokens)."
        )
    # %LOCALAPPDATA% from a cmd.exe or a config file, ~ from a shell.
    path = os.path.expanduser(os.path.expandvars(raw_path.strip()))
    if os.path.isdir(path):
        raise ConfigError(f"The token file {path} is a directory, not a file.")
    if not os.path.isfile(path):
        raise ConfigError(f"The token file {path} does not exist.")
    if _windows():
        # Windows reports 0o666 for every file; the profile folder's ACL is
        # what keeps other accounts out, so require the file to be inside it.
        if not _under_profile(path):
            raise ConfigError(
                "On Windows the token file must be under your user profile "
                "folder (for example %LOCALAPPDATA%\\SlowBooksPro\\data\\mcp-token), "
                "where only your account can read it."
            )
    elif os.stat(path).st_mode & 0o077:
        raise ConfigError(
            f"The token file {path} can be read or changed by other users. "
            f"Run: chmod 600 {path}"
        )
    try:
        with open(path, "rb") as fh:
            lines = _decode(fh.read()).strip().splitlines()
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"The token file {path} could not be read.") from None
    if len(lines) != 1 or not lines[0].strip().startswith("sbp_"):
        raise ConfigError(
            f"The token file {path} must hold one line: the API token, "
            "which starts with sbp_."
        )
    return lines[0].strip()


def _timeout(raw: str) -> float:
    sentence = "SLOWBOOKS_TIMEOUT_SECONDS must be a number from 1 to 3600."
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(sentence) from None
    if not math.isfinite(value) or not 1 <= value <= 3600:
        raise ConfigError(sentence)
    return value


def load_config() -> Config:
    base_url = _base_url(os.environ.get("SLOWBOOKS_URL") or DEFAULT_URL)
    token = _read_token(os.environ.get("SLOWBOOKS_TOKEN_FILE") or "")
    timeout = _timeout(os.environ.get("SLOWBOOKS_TIMEOUT_SECONDS") or "120")
    return Config(base_url=base_url, token=token, timeout=timeout)
