# Vendored from SlowBooks Switchboard: sbplive/config.py
# (see app/switchboard/SOURCE.md). Do not edit here: change the source and run scripts/sync_switchboard.py.
# ============================================================================
# Settings: where the app is and which token to use. They come from the
# store that `setup` writes (store.py), and the environment can override
# each one (SBPLIVE_URL, SBPLIVE_TOKEN_FILE, SBPLIVE_TIMEOUT_SECONDS).
#
# The token comes from a file and nowhere else: an argument or an
# environment variable would show up in a process list or a host's
# configuration, and a file can be made private (0600). Error sentences name
# the problem and the fix, never the token's content.
#
# The app's address: this computer by default. The office's Server Edition
# is reached at a LAN address, so any http:// or https:// address is taken,
# with one rule: plain http:// leaves this computer only for a host on one of
# the networks listed in _LOCAL_NETWORKS (private or link-local), since the
# token travels unencrypted. Every other host must be https://, and its
# certificate is always verified.
# ============================================================================

import ipaddress
import math
import os
import socket
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from app.switchboard import store

DEFAULT_URL = "http://127.0.0.1:3001"
_LOOPBACK_NAMES = {"localhost"}

UNENCRYPTED_WARNING = (
    "This address is on your own network and uses http://, so the API token "
    "and the books travel unencrypted on that network: anyone on it who can "
    "watch the traffic could read them. Use an https:// address if the "
    "server has one."
)


class ConfigError(ValueError):
    """A setting the server cannot start with; the message is the sentence."""


@dataclass(frozen=True)
class Config:
    base_url: str
    token: str = ""
    timeout: float = 120.0

    def __repr__(self) -> str:  # keep the token out of any log line
        return f"Config(base_url={self.base_url!r}, timeout={self.timeout})"


# Where plain http:// may go: this computer and the networks an office can
# own. The list is the whole rule. Anything not on it must be https://, and
# that includes ranges that are reserved without being anyone's network (the
# documentation and benchmark ranges, 240.0.0.0/4, the shared range
# 100.64.0.0/10).
_LOCAL_NETWORKS = tuple(
    ipaddress.ip_network(net)
    for net in (
        "127.0.0.0/8",  # loopback
        "::1/128",  # loopback
        "10.0.0.0/8",  # private (RFC 1918)
        "172.16.0.0/12",  # private (RFC 1918)
        "192.168.0.0/16",  # private (RFC 1918)
        "169.254.0.0/16",  # link-local
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
    )
)


def _address(text: str):
    """The address in text, without an IPv6 zone. An IPv4-mapped IPv6
    address (::ffff:192.168.1.20) is the IPv4 address inside it: that is
    where the connection goes, so that is what is judged. A zone (%eth0)
    belongs on an IPv6 address only: any other text with a % in it is not
    an address, whatever stands in front of the %."""
    head, percent, zone = text.partition("%")
    addr = ipaddress.ip_address(head)
    if percent and (addr.version != 6 or not zone):
        raise ValueError("a zone belongs on an IPv6 address")
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped if mapped is not None else addr


def _ip(host: str):
    try:
        return _address(host)
    except ValueError:
        return None


def _is_local_network(addr) -> bool:
    """Whether addr is on one of _LOCAL_NETWORKS."""
    return any(addr.version == net.version and addr in net for net in _LOCAL_NETWORKS)


def _resolve(host: str, port: int) -> list:
    infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    return [_address(i[4][0]) for i in infos]


def check_url(raw: str, *, must_resolve: bool = True) -> tuple:
    """(the clean address, a warning sentence or None), or ConfigError.

    must_resolve: setup looks a host name up and refuses one it cannot find;
    the server, started by a host, lets an unreachable name through so the
    tools can say the app did not answer."""
    shape = (
        "The address must look like http://127.0.0.1:3001 or "
        "https://books.example.com: http or https, a host and maybe a port, "
        "with nothing after it."
    )
    text = (raw or "").strip()
    try:
        parts = urlsplit(text)
        host, port = parts.hostname, parts.port
    except ValueError:
        raise ConfigError(shape) from None
    if (
        parts.scheme not in ("http", "https")
        or not host
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ConfigError(shape)
    clean = f"{parts.scheme}://{parts.netloc}"
    port = port or (443 if parts.scheme == "https" else 80)
    # The request is made to the host with its percent escapes undone
    # (urllib does that), so that is the host judged here: 10.0.0.1%2eoffice
    # goes to the name 10.0.0.1.office, not to 10.0.0.1.
    addr = _ip(unquote(host))
    if addr is not None and (addr.is_unspecified or addr.is_multicast):
        raise ConfigError(
            f"{host} is not the address of a computer. Use 127.0.0.1 for this "
            "computer, or the server's own address."
        )
    if host.lower() in _LOOPBACK_NAMES or (addr is not None and addr.is_loopback):
        return clean, None
    if parts.scheme == "https":
        return clean, None  # the certificate is verified on every request
    public = (
        f"{host} is not on a private network, so the address must start with "
        "https://: over plain http:// the API token would cross the internet "
        "unencrypted."
    )
    if addr is not None:
        if not _is_local_network(addr):
            raise ConfigError(public)
        return clean, UNENCRYPTED_WARNING
    if "%" in host:
        raise ConfigError(public)  # a name is written without one
    try:
        found = _resolve(host, port)
    except (OSError, UnicodeError):
        if must_resolve:
            raise ConfigError(
                f"{host} could not be found on the network. Check the name, or "
                "use the server's address, such as http://192.168.1.20:3001."
            ) from None
        return clean, UNENCRYPTED_WARNING
    if not found or not all(_is_local_network(a) for a in found):
        raise ConfigError(public)
    return clean, UNENCRYPTED_WARNING


def check_token_text(text: str, where: str) -> str:
    """The one line of a token, or ConfigError naming where it came from."""
    lines = (text or "").strip().splitlines()
    if len(lines) != 1 or not lines[0].strip().startswith("sbp_"):
        raise ConfigError(
            f"{where} must hold one line: the API token, which starts with sbp_."
        )
    token = lines[0].strip()
    if any(c.isspace() for c in token):
        raise ConfigError(f"{where} must hold the API token alone, with no spaces.")
    return token


def _read_token(raw_path: str, private: bool = True) -> str:
    if not raw_path.strip():
        raise ConfigError(
            "SBPLIVE_TOKEN_FILE must name a file that holds the API token "
            "(create one under Settings, API Tokens)."
        )
    path = os.path.expanduser(raw_path.strip())
    if not os.path.isfile(path):
        raise ConfigError(f"The token file {path} does not exist.")
    # Windows reports 0o666 for every file, so the check means nothing there.
    if private and os.name != "nt" and os.stat(path).st_mode & 0o077:
        raise ConfigError(
            f"The token file {path} can be read by other users. "
            f"Run: chmod 600 {path}"
        )
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"The token file {path} could not be read.") from None
    return check_token_text(text, f"The token file {path}")


def read_token_file(raw_path: str) -> str:
    """A token from a file the person names to setup. It need not be private:
    setup copies the token into its own private store."""
    return _read_token(raw_path, private=False)


def _timeout(raw: str) -> float:
    sentence = "SBPLIVE_TIMEOUT_SECONDS must be a number from 1 to 3600."
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(sentence) from None
    if not math.isfinite(value) or not 1 <= value <= 3600:
        raise ConfigError(sentence)
    return value


NOT_SET_UP = (
    "SlowBooks Switchboard is not set up on this computer for this user: no "
    "API token is stored. Run: sbplive setup (or set "
    "SBPLIVE_TOKEN_FILE to a file that holds the token)."
)


def load_config() -> Config:
    raw_url = os.environ.get("SBPLIVE_URL") or store.stored_url() or DEFAULT_URL
    base_url, _ = check_url(raw_url, must_resolve=False)
    token_file = os.environ.get("SBPLIVE_TOKEN_FILE")
    if token_file is None or token_file == "":
        if not store.has_token():
            raise ConfigError(NOT_SET_UP)
        token_file = str(store.token_path())
    token = _read_token(token_file)
    timeout = _timeout(os.environ.get("SBPLIVE_TIMEOUT_SECONDS") or "120")
    return Config(base_url=base_url, token=token, timeout=timeout)
