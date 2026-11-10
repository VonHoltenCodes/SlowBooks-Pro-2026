#!/usr/bin/env python3
"""Create a signed, notarized, and stapled DMG from one Actions artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from audit_bundle import audit_bundle
from prepare_bundle import prepare_bundle

BUNDLE_ID = "com.vonholtencodes.slowbookspro"
REPOSITORY_URL = "https://github.com/VonHoltenCodes/SlowBooks-Pro-2026"
NESTED_CODE_SUFFIXES = {".framework", ".bundle", ".plugin", ".xpc", ".appex", ".app"}
# Extra arguments for every notarytool call. notarytool reads the credential
# profile from the LOGIN keychain by default, which is locked in an SSH
# session (the in-fleet build box is driven over SSH); --notary-keychain
# points it at the keychain that actually holds the profile.
NOTARY_EXTRA_ARGS: list[str] = []
# A build made on the in-fleet Mac has no Actions run to cite; --local-build
# accepts build-info.txt with github_run_id=local and records the build
# host as the provenance instead. The signing, notarization and stapling
# gates are identical.
LOCAL_BUILD = False
IDENTITY_PATTERN = re.compile(
    r'^\s*\d+\)\s+[0-9A-Fa-f]+\s+"(Developer ID Application:[^"]+)"$',
    re.MULTILINE,
)


def _redacted_args(args: Iterable[str]) -> list[str]:
    return [
        (
            "<Developer ID Application identity>"
            if arg.startswith("Developer ID Application:")
            else arg
        )
        for arg in args
    ]


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(_redacted_args(args)), flush=True)
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if check and result.returncode != 0:
        # Both streams are captured, so a bare CalledProcessError would carry
        # only the command and the exit code — a transient codesign failure
        # cost a full rebuild to learn nothing (macbase1, 2.10.0 gate). Print
        # what the tool said before raising, and keep it on the exception.
        print(
            f"! exit {result.returncode}: {' '.join(_redacted_args(args))}", flush=True
        )
        if result.stdout:
            print(
                result.stdout,
                end="" if result.stdout.endswith("\n") else "\n",
                flush=True,
            )
        if result.stderr:
            print(
                result.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
                file=sys.stderr,
                flush=True,
            )
        raise subprocess.CalledProcessError(
            result.returncode, args, output=result.stdout, stderr=result.stderr
        )
    return result


# Apple's timestamp service (timestamp.apple.com) blips; codesign then fails
# with exactly this text and nothing else is wrong (macbase1, 2.10.0 gate:
# three runs, ~25 minutes each, lost to it). Retry that failure alone — any
# other signing error surfaces on the first try.
TIMESTAMP_FAILURE = "A timestamp was expected but was not found"
SIGN_ATTEMPTS = 3
SIGN_BACKOFF_SECONDS = (30, 60)
_sleep = time.sleep


def _is_timestamp_failure(exc: subprocess.CalledProcessError) -> bool:
    return TIMESTAMP_FAILURE in f"{exc.stdout or ''}{exc.stderr or ''}"


def _run_signing(*args: str) -> subprocess.CompletedProcess[str]:
    """codesign --timestamp with a narrow retry (see TIMESTAMP_FAILURE)."""
    for attempt in range(1, SIGN_ATTEMPTS + 1):
        try:
            return _run(*args)
        except subprocess.CalledProcessError as exc:
            if not _is_timestamp_failure(exc) or attempt == SIGN_ATTEMPTS:
                raise
            delay = SIGN_BACKOFF_SECONDS[min(attempt, len(SIGN_BACKOFF_SECONDS)) - 1]
            print(
                f"! timestamp service did not answer (attempt {attempt} of "
                f"{SIGN_ATTEMPTS}); retrying in {delay}s",
                flush=True,
            )
            _sleep(delay)
    raise AssertionError("unreachable")


# hdiutil create fails with "Resource busy" now and then on GitHub's macOS
# runners right after a large copy (the build job's unsigned DMG, run
# 40800371868, 2026-10-28; a known runner flake). The candidate DMG here is
# made the same way, straight after ditto, so it gets a few tries too. hdiutil
# verify failed once the same way, "Resource temporarily unavailable", right
# after codesign wrote the candidate DMG (run 42697156611, 2.31.11; #758), so
# every hdiutil call retries on those two errors alone; anything else fails
# on the first try, as before.
HDIUTIL_BUSY = "Resource busy"
HDIUTIL_UNAVAILABLE = "Resource temporarily unavailable"
HDIUTIL_TRANSIENT = (HDIUTIL_BUSY, HDIUTIL_UNAVAILABLE)
HDIUTIL_ATTEMPTS = 5
HDIUTIL_BACKOFF_SECONDS = (5, 10, 15, 20)


def _is_hdiutil_busy(exc: subprocess.CalledProcessError) -> bool:
    said = f"{getattr(exc, 'stdout', '') or ''}{getattr(exc, 'stderr', '') or ''}"
    return any(text in said for text in HDIUTIL_TRANSIENT)


def _hdiutil_delay(attempt: int) -> int:
    return HDIUTIL_BACKOFF_SECONDS[min(attempt, len(HDIUTIL_BACKOFF_SECONDS)) - 1]


def _run_hdiutil_create(*args: str) -> subprocess.CompletedProcess[str]:
    """hdiutil create with bounded retries on a transient error only; any
    other failure raises on the first try, as before."""
    for attempt in range(1, HDIUTIL_ATTEMPTS + 1):
        try:
            return _run("hdiutil", "create", *args)
        except subprocess.CalledProcessError as exc:
            if not _is_hdiutil_busy(exc) or attempt == HDIUTIL_ATTEMPTS:
                raise
            delay = _hdiutil_delay(attempt)
            print(
                f"! hdiutil create: resource busy (attempt {attempt} of "
                f"{HDIUTIL_ATTEMPTS}); retrying in {delay}s",
                flush=True,
            )
            _sleep(delay)
    raise AssertionError("unreachable")


def _record_hdiutil(
    report_path: Path, subcommand: str, *args: str
) -> subprocess.CompletedProcess[str]:
    """hdiutil <subcommand> kept as release evidence, with the same bounded
    retries as _run_hdiutil_create on a transient error. Every attempt is
    written to the report, so the evidence shows the retry, not just the
    pass."""
    for attempt in range(1, HDIUTIL_ATTEMPTS + 1):
        result = _record_run(report_path, "hdiutil", subcommand, *args, check=False)
        if result.returncode == 0:
            return result
        failure = subprocess.CalledProcessError(
            result.returncode, result.args, output=result.stdout, stderr=result.stderr
        )
        if not _is_hdiutil_busy(failure) or attempt == HDIUTIL_ATTEMPTS:
            raise failure
        delay = _hdiutil_delay(attempt)
        print(
            f"! hdiutil {subcommand}: resource busy (attempt {attempt} of "
            f"{HDIUTIL_ATTEMPTS}); retrying in {delay}s",
            flush=True,
        )
        _sleep(delay)
    raise AssertionError("unreachable")


def _record_run(
    report_path: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command and preserve both output streams as release evidence."""
    result = _run(*args, check=False)
    with report_path.open("a", encoding="utf-8") as report:
        report.write(f"$ {' '.join(_redacted_args(args))}\n")
        if result.stdout:
            report.write(result.stdout)
            if not result.stdout.endswith("\n"):
                report.write("\n")
        if result.stderr:
            report.write(result.stderr)
            if not result.stderr.endswith("\n"):
                report.write("\n")
        report.write(f"exit_code={result.returncode}\n\n")
    if check:
        result.check_returncode()
    return result


def _parse_key_values(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition("=")
        if not separator or not key or key in values:
            raise ValueError(f"invalid build metadata line: {line!r}")
        values[key] = value
    return values


def _verify_checksums(artifact_dir: Path) -> None:
    sums_path = artifact_dir / "SHA256SUMS"
    if not sums_path.is_file():
        raise ValueError(f"missing checksum manifest: {sums_path}")
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        expected, separator, raw_name = line.partition("  ")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError(f"invalid checksum line: {line!r}")
        name = raw_name.removeprefix("*")
        candidate = (artifact_dir / name).resolve()
        try:
            candidate.relative_to(artifact_dir.resolve())
        except ValueError as exc:
            raise ValueError(
                f"checksum path escapes artifact directory: {name}"
            ) from exc
        if not candidate.is_file():
            raise ValueError(f"checksummed file is missing: {name}")
        with candidate.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"checksum mismatch for {name}")


def _developer_identities(output: str) -> list[str]:
    return IDENTITY_PATTERN.findall(output)


def _select_identity(requested: str | None) -> str:
    result = _run("security", "find-identity", "-v", "-p", "codesigning")
    identities = _developer_identities(result.stdout)
    if requested:
        if requested not in identities:
            raise ValueError(
                "requested Developer ID Application identity is unavailable"
            )
        return requested
    if len(identities) != 1:
        raise ValueError(
            "expected exactly one Developer ID Application identity; "
            "pass --identity to select one explicitly"
        )
    return identities[0]


def _clear_xattrs(root: Path) -> None:
    _run("find", str(root), "-exec", "xattr", "-c", "{}", "+")


def _is_macho(path: Path) -> tuple[bool, bool]:
    description = _run("file", "-b", str(path)).stdout
    return "Mach-O" in description, "executable" in description


def _sign(path: Path, identity: str, hardened_runtime: bool) -> None:
    command = ["codesign", "--force", "--timestamp"]
    if hardened_runtime:
        command.extend(["--options", "runtime"])
    command.extend(["--sign", identity, str(path)])
    _run_signing(*command)


def _sign_app(app: Path, identity: str) -> None:
    macho_targets = []
    for path in app.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        is_macho, is_executable = _is_macho(path)
        if is_macho:
            macho_targets.append((path, is_executable))

    for path, is_executable in sorted(
        macho_targets,
        key=lambda item: len(item[0].parts),
        reverse=True,
    ):
        _sign(path, identity, hardened_runtime=is_executable)

    nested = [
        path
        for path in app.rglob("*")
        if path.is_dir() and path.suffix in NESTED_CODE_SUFFIXES
    ]
    for path in sorted(nested, key=lambda item: len(item.parts), reverse=True):
        _sign(path, identity, hardened_runtime=True)

    _sign(app, identity, hardened_runtime=True)


def _signed_targets(app: Path) -> Iterable[tuple[Path, bool]]:
    """Yield nested signed code and whether hardened runtime is required."""
    for path in sorted(app.rglob("*")):
        if path.is_symlink():
            continue
        if path.is_dir() and path.suffix in NESTED_CODE_SUFFIXES:
            yield path, True
        elif path.is_file():
            is_macho, is_executable = _is_macho(path)
            if is_macho:
                yield path, is_executable
    yield app, True


def _signature_team_id(details: str) -> str:
    match = re.search(r"^TeamIdentifier=(.+)$", details, re.MULTILINE)
    if not match:
        raise RuntimeError("signature is missing a Team ID")
    return match.group(1).strip()


def _verify_signature_details(
    details: str,
    identity: str,
    expected_team_id: str | None,
    hardened_runtime: bool,
) -> str:
    if f"Authority={identity}" not in details:
        raise RuntimeError("signature does not use the selected Developer ID identity")
    team_id = _signature_team_id(details)
    if expected_team_id is not None and team_id != expected_team_id:
        raise RuntimeError("nested signature Team ID differs from the outer app")
    if "Timestamp=" not in details:
        raise RuntimeError("signature is missing a secure timestamp")
    if hardened_runtime and "runtime" not in details:
        raise RuntimeError("executable signature does not enable hardened runtime")
    return team_id


def _expected_unnotarized_policy_result(
    syspolicy: subprocess.CompletedProcess[str],
    gatekeeper: subprocess.CompletedProcess[str],
) -> bool:
    """Recognize the lone pre-notary rejection produced by some macOS releases."""
    policy_output = syspolicy.stdout + syspolicy.stderr
    gatekeeper_output = gatekeeper.stdout + gatekeeper.stderr
    # "Internal Xprotect Error" is the ephemeral-CI-runner variant of the
    # same state: syspolicyd's local XProtect scan cannot run there, while
    # spctl still confirms a valid but unnotarized Developer ID signature.
    # Only accepted with every other guard intact; Apple's notary service
    # performs the authoritative scan immediately afterwards.
    return (
        syspolicy.returncode != 0
        and gatekeeper.returncode != 0
        and (
            "Gatekeeper rejected this file" in policy_output
            or "Internal Xprotect Error" in policy_output
        )
        and "source=Unnotarized Developer ID" in gatekeeper_output
        and policy_output.count("Severity: Fatal") == 1
        and "Severity: Warning" not in policy_output
        and "Incorrect Bundle Structure" not in policy_output
    )


def _verify_pre_notary_policy(app: Path, report_dir: Path) -> None:
    evidence = report_dir / "syspolicy-app.txt"
    syspolicy = _record_run(
        evidence,
        "xcrun",
        "syspolicy_check",
        "notary-submission",
        str(app),
        check=False,
    )
    if syspolicy.returncode == 0:
        return

    gatekeeper = _record_run(
        evidence,
        "spctl",
        "--assess",
        "--type",
        "execute",
        "--verbose=4",
        str(app),
        check=False,
    )
    if _expected_unnotarized_policy_result(syspolicy, gatekeeper):
        with evidence.open("a", encoding="utf-8") as report:
            report.write(
                "Expected pre-notary state: Developer ID signature is valid, "
                "but no Apple notarization ticket exists yet.\n"
            )
        return
    raise RuntimeError(f"app failed pre-notarization policy checks; see {evidence}")


def _verify_signed_app(app: Path, identity: str, report_dir: Path) -> None:
    evidence = report_dir / "codesign-app.txt"
    outer_details = _record_run(evidence, "codesign", "-dvvv", str(app)).stderr
    team_id = _verify_signature_details(
        outer_details,
        identity,
        expected_team_id=None,
        hardened_runtime=True,
    )

    for path, hardened_runtime in _signed_targets(app):
        relative = path.relative_to(app.parent)
        _record_run(
            evidence,
            "codesign",
            "--verify",
            "--strict",
            "--verbose=4",
            str(path),
        )
        details = _record_run(evidence, "codesign", "-dvvv", str(path)).stderr
        try:
            _verify_signature_details(
                details,
                identity,
                expected_team_id=team_id,
                hardened_runtime=hardened_runtime,
            )
        except RuntimeError as exc:
            raise RuntimeError(f"invalid signature on {relative}: {exc}") from exc

    _record_run(
        evidence,
        "codesign",
        "--verify",
        "--deep",
        "--strict",
        "--verbose=4",
        str(app),
    )
    _verify_pre_notary_policy(app, report_dir)


def _notarize(target: Path, profile: str, report_dir: Path, label: str) -> None:
    """Submit one artifact (a zipped .app or a DMG) to the notary service
    and fail unless Apple accepted it with a clean log. Evidence files are
    prefixed with ``label`` so the app and DMG submissions sit side by side
    in the report directory. Stapling is the caller's job (`_staple`)."""
    result = _run(
        "xcrun",
        "notarytool",
        "submit",
        str(target),
        "--keychain-profile",
        profile,
        *NOTARY_EXTRA_ARGS,
        "--wait",
        "--output-format",
        "json",
        check=False,
    )
    submit_path = report_dir / f"notary-{label}-submit.json"
    submit_path.write_text(result.stdout, encoding="utf-8")
    (report_dir / f"notary-{label}-submit.stderr.txt").write_text(
        result.stderr, encoding="utf-8"
    )
    try:
        payload = json.loads(result.stdout)
        submission_id = payload["id"]
        accepted = payload.get("status") == "Accepted"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"notarytool returned an unreadable result; see {submit_path}"
        ) from exc

    log_path = report_dir / f"notary-{label}-log.json"
    log_result = _run(
        "xcrun",
        "notarytool",
        "log",
        submission_id,
        str(log_path),
        "--keychain-profile",
        profile,
        *NOTARY_EXTRA_ARGS,
        check=False,
    )
    (report_dir / f"notary-{label}-log-command.txt").write_text(
        f"stdout:\n{log_result.stdout}\nstderr:\n{log_result.stderr}\n"
        f"exit_code={log_result.returncode}\n",
        encoding="utf-8",
    )
    if log_result.returncode or not log_path.is_file():
        raise RuntimeError(f"could not retrieve notarization log; see {log_path}")
    try:
        log_payload = json.loads(log_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"notarization log is unreadable; see {log_path}") from exc
    issues = log_payload.get("issues")
    if issues is None:
        issues = []
    if not isinstance(issues, list) or any(
        not isinstance(issue, dict) for issue in issues
    ):
        raise RuntimeError(f"notarization log has invalid issues data; see {log_path}")
    log_errors = [
        issue for issue in issues if str(issue.get("severity", "")).lower() == "error"
    ]
    if (
        log_payload.get("jobId", log_payload.get("id")) != submission_id
        or log_payload.get("status") != "Accepted"
        or log_errors
    ):
        raise RuntimeError(f"notarization log did not pass inspection; see {log_path}")
    if result.returncode or not accepted:
        raise RuntimeError(f"notarization was not accepted; see {log_path}")


# Right after notarization is Accepted, Apple's CDN may not serve the ticket
# yet. Either `stapler staple` fails with one of these markers, or (the 2.26.0
# sign jobs: three of four) it exits 0 and `stapler validate` fails a second
# later. Staple + validate are retried as one unit, ~5 minutes in total; any
# other staple error fails on the first try.
STAPLE_TICKET_MISSING = ("Could not find base64 encoded ticket", "Record not found")
STAPLE_ATTEMPTS = 6
STAPLE_BACKOFF_SECONDS = (20, 40, 60, 90, 90)


def _is_ticket_missing(output: str) -> bool:
    return any(marker in output for marker in STAPLE_TICKET_MISSING)


def _staple_and_validate(target: Path, staple_evidence: Path) -> tuple[bool, str]:
    """One staple + validate pass: (done, what the stapler said when not).

    Any failure is retried, not only the "no ticket" messages: this runs
    only after notarytool has seen the submission Accepted, and the v2.27.0
    tag's sign job failed with a stapler error that matched neither known
    message, then passed on a rerun (#459). A bundle that is really broken
    still fails, after the bounded retries, with the stapler's last words.
    """
    result = _record_run(
        staple_evidence, "xcrun", "stapler", "staple", "-v", str(target), check=False
    )
    if result.returncode:
        said = f"{result.stdout or ''}{result.stderr or ''}".strip()
        kind = (
            "ticket not available yet" if _is_ticket_missing(said) else "staple failed"
        )
        return False, f"{kind} (exit {result.returncode}): {said[-400:]}"
    validated = _record_run(
        staple_evidence, "xcrun", "stapler", "validate", "-v", str(target), check=False
    )
    if validated.returncode:
        said = f"{validated.stdout or ''}{validated.stderr or ''}".strip()
        return False, f"validate failed (exit {validated.returncode}): {said[-400:]}"
    return True, ""


def _staple(target: Path, report_dir: Path) -> None:
    """Attach the notarization ticket to ``target`` and prove it is there.

    Both the .app and the DMG get a ticket. The .app must be stapled
    BEFORE the DMG is built from it: a user drags the bundle out of the
    disk image, and that copy is what Gatekeeper evaluates on first
    launch. With a ticket only on the DMG, an offline Mac (or one on a
    network that cannot reach Apple) refuses the dragged app. `spctl`
    passing on a connected build machine is not evidence either way —
    it fetches the ticket from Apple — which is why this is verified
    with `stapler validate`, which only reads the local ticket.
    """
    staple_evidence = report_dir / "stapler.txt"
    for attempt in range(1, STAPLE_ATTEMPTS + 1):
        done, said = _staple_and_validate(target, staple_evidence)
        if done:
            return
        if attempt == STAPLE_ATTEMPTS:
            raise RuntimeError(
                f"no notarization ticket on {target.name} after {STAPLE_ATTEMPTS} "
                f"stapling attempts; the last: {said}; see {staple_evidence}"
            )
        delay = STAPLE_BACKOFF_SECONDS[min(attempt, len(STAPLE_BACKOFF_SECONDS)) - 1]
        print(
            f"! {said.splitlines()[0] if said else 'staple failed'} (attempt "
            f"{attempt} of {STAPLE_ATTEMPTS}); retrying in {delay}s",
            flush=True,
        )
        _sleep(delay)


def _verify_dmg_contents_stapled(
    dmg: Path, evidence: Path, work_dir: Path, bundle_name: str
) -> None:
    """Mount the shipped DMG and run `stapler validate` on the .app inside
    it — the artifact a user actually drags out. Fails the release if the
    ticket is missing there, whatever the DMG's own ticket says.
    ``bundle_name`` is the staged bundle's own name so a renamed .app is
    validated rather than reported missing (#100)."""
    mount = work_dir / "final-dmg-mount"
    mount.mkdir()
    _record_run(
        evidence,
        "hdiutil",
        "attach",
        str(dmg),
        "-mountpoint",
        str(mount),
        "-nobrowse",
        "-readonly",
        "-quiet",
    )
    try:
        inner = mount / bundle_name
        if not inner.is_dir():
            raise RuntimeError(
                f"{bundle_name} is not inside {dmg.name}; see {evidence}"
            )
        # `stapler validate` reads the ticket in the bundle and checks it
        # with Apple's ticket-delivery service, so it fails when that call
        # fails: on 2026-10-28 four sign jobs in a row died here or at the
        # staple above with a CloudKit lookup error from the runner, every
        # staple having exited 0 (#634). The staple's own retry covers the
        # earlier stapling; this last look gets the same bounded retries.
        for attempt in range(1, STAPLE_ATTEMPTS + 1):
            result = _record_run(
                evidence, "xcrun", "stapler", "validate", "-v", str(inner), check=False
            )
            if not result.returncode:
                break
            said = f"{result.stdout or ''}{result.stderr or ''}".strip()
            if attempt == STAPLE_ATTEMPTS:
                raise RuntimeError(
                    f"the app inside {dmg.name} is not stapled, after "
                    f"{STAPLE_ATTEMPTS} looks; the last: validate failed (exit "
                    f"{result.returncode}): {said[-400:]}; see {evidence}"
                )
            delay = STAPLE_BACKOFF_SECONDS[
                min(attempt, len(STAPLE_BACKOFF_SECONDS)) - 1
            ]
            print(
                f"! the app inside {dmg.name}: validate failed (exit "
                f"{result.returncode}) (look {attempt} of {STAPLE_ATTEMPTS}); "
                f"retrying in {delay}s",
                flush=True,
            )
            _sleep(delay)
    finally:
        _record_run(evidence, "hdiutil", "detach", str(mount), "-quiet", check=False)


def build_release(
    artifact_dir: Path,
    expected_sha: str,
    output_root: Path,
    identity: str | None,
    notary_profile: str,
) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("macOS release tooling must run on macOS")
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("--expected-sha must be a full lowercase commit SHA")

    artifact_dir = artifact_dir.resolve()
    _verify_checksums(artifact_dir)
    build_info = _parse_key_values(
        (artifact_dir / "build-info.txt").read_text(encoding="utf-8")
    )
    if build_info.get("git_sha") != expected_sha:
        raise ValueError("artifact commit does not match --expected-sha")
    if build_info.get("architecture") != "arm64":
        raise ValueError("artifact is not the expected arm64 build")
    run_id = build_info.get("github_run_id", "")
    run_attempt = build_info.get("github_run_attempt", "")
    run_url = build_info.get("github_run_url", "")
    if LOCAL_BUILD:
        if run_id != "local" or not build_info.get("build_host"):
            raise ValueError(
                "local build needs github_run_id=local and build_host in build-info"
            )
    else:
        if not run_id.isdigit() or not run_attempt.isdigit():
            raise ValueError("artifact is missing valid Actions run metadata")
        if run_url != f"{REPOSITORY_URL}/actions/runs/{run_id}":
            raise ValueError("artifact has an invalid Actions run URL")
    version = build_info.get("app_version", "")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("artifact has an invalid application version")

    basename = f"SlowBooksPro-{version}-macos-arm64"
    app_zip = artifact_dir / f"{basename}-unsigned-app.zip"
    if not app_zip.is_file():
        raise ValueError(f"missing unsigned app archive: {app_zip.name}")
    transport_dmg = artifact_dir / f"{basename}-unsigned.dmg"
    if not transport_dmg.is_file():
        raise ValueError(f"missing unsigned transport DMG: {transport_dmg.name}")

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_dir = output_root.resolve() / f"{basename}-{expected_sha[:12]}-{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)
    (report_dir / "source-build-info.txt").write_text(
        (artifact_dir / "build-info.txt").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    transport_evidence = report_dir / "transport-verification.txt"
    _record_run(transport_evidence, "unzip", "-tq", str(app_zip))
    _record_hdiutil(transport_evidence, "verify", str(transport_dmg))
    selected_identity = _select_identity(identity)

    with tempfile.TemporaryDirectory(prefix="slowbooks-release-") as temporary:
        work_dir = Path(temporary)
        _run("ditto", "-x", "-k", str(app_zip), str(work_dir))
        app = work_dir / "SlowBooks Pro.app"
        if not app.is_dir():
            raise RuntimeError("transport archive did not contain SlowBooks Pro.app")

        removed_links = prepare_bundle(app)
        (report_dir / "bundle-preparation.txt").write_text(
            "Removed Resources-to-Frameworks compatibility links:\n"
            + "".join(f"- {path.relative_to(app)}\n" for path in removed_links),
            encoding="utf-8",
        )
        _clear_xattrs(app)
        _sign_app(app, selected_identity)
        _verify_signed_app(app, selected_identity, report_dir)
        (report_dir / "native-linkage.txt").write_text(
            audit_bundle(app, "arm64"),
            encoding="utf-8",
        )

        # Notarize the bare bundle and staple it first, so the copy that
        # goes into the DMG (and from there into /Applications) carries
        # its own ticket. See _staple for why the order matters.
        notary_zip = work_dir / f"{basename}-notary-app.zip"
        _run("ditto", "-c", "-k", "--keepParent", str(app), str(notary_zip))
        _notarize(notary_zip, notary_profile, report_dir, "app")
        _staple(app, report_dir)
        _run("codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app))

        stage = work_dir / "dmg-stage"
        stage.mkdir()
        staged_app = stage / app.name
        _run("ditto", str(app), str(staged_app))
        (stage / "Applications").symlink_to("/Applications")
        _clear_xattrs(staged_app)
        _run(
            "codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=4",
            str(staged_app),
        )

        candidate_dmg = report_dir / f"{basename}-candidate.dmg"
        _run_hdiutil_create(
            "-volname",
            "SlowBooks Pro",
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(candidate_dmg),
        )
        _clear_xattrs(candidate_dmg)
        _run_signing(
            "codesign",
            "--force",
            "--timestamp",
            "--sign",
            selected_identity,
            "--identifier",
            f"{BUNDLE_ID}.dmg",
            str(candidate_dmg),
        )
        candidate_evidence = report_dir / "candidate-dmg-verification.txt"
        _record_hdiutil(candidate_evidence, "verify", str(candidate_dmg))
        _record_run(
            candidate_evidence,
            "codesign",
            "--verify",
            "--strict",
            "--verbose=4",
            str(candidate_dmg),
        )
        dmg_details = _record_run(
            candidate_evidence, "codesign", "-dvvv", str(candidate_dmg)
        ).stderr
        _verify_signature_details(
            dmg_details,
            selected_identity,
            expected_team_id=None,
            hardened_runtime=False,
        )
        _notarize(candidate_dmg, notary_profile, report_dir, "dmg")
        _staple(candidate_dmg, report_dir)

        final_dmg = report_dir / f"{basename}.dmg"
        candidate_dmg.rename(final_dmg)
        final_evidence = report_dir / "final-dmg-verification.txt"
        _record_hdiutil(final_evidence, "verify", str(final_dmg))
        _record_run(
            final_evidence,
            "codesign",
            "--verify",
            "--strict",
            "--verbose=4",
            str(final_dmg),
        )
        _record_run(
            final_evidence,
            "spctl",
            "--assess",
            "--type",
            "open",
            "--verbose=4",
            "--context",
            "context:primary-signature",
            str(final_dmg),
        )
        _verify_dmg_contents_stapled(final_dmg, final_evidence, work_dir, app.name)

    with final_dmg.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (report_dir / "SHA256SUMS").write_text(
        f"{digest}  {final_dmg.name}\n",
        encoding="utf-8",
    )
    print(f"Release candidate ready: {final_dmg}")
    return final_dmg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--identity")
    parser.add_argument("--notary-profile", default="slowbooks-notary")
    parser.add_argument(
        "--notary-keychain",
        type=Path,
        help="keychain file holding the notary profile (needed over SSH, "
        "where the login keychain is locked)",
    )
    parser.add_argument(
        "--local-build",
        action="store_true",
        help="the artifact was built on this Mac, not by Actions (build-info "
        "carries github_run_id=local and build_host)",
    )
    args = parser.parse_args()
    global LOCAL_BUILD
    LOCAL_BUILD = args.local_build
    if args.notary_keychain:
        NOTARY_EXTRA_ARGS[:] = ["--keychain", str(args.notary_keychain)]
    build_release(
        args.artifact_dir,
        args.expected_sha,
        args.output_root,
        args.identity,
        args.notary_profile,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
