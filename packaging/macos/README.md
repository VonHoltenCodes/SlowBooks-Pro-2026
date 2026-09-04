# macOS release

Maintainer: **@ContractorKeith**. Owner review is required for merges to
`main`.

The first supported native build is Apple Silicon (`arm64`) on macOS 14 or
newer. Intel support needs a separate build and installed-app acceptance pass.

## Release model

GitHub Actions builds and smoke-tests an exact commit without Apple
credentials in the `build` job. PyInstaller ad-hoc signs the transport app,
but the artifact is still called `unsigned` because it is not suitable for
distribution.

Since 2026-08, the `sign` job then runs `release.py` on the runner itself:
it imports the Developer ID Application certificate from repo secrets
(`MACOS_CERT_P12`/`MACOS_CERT_PASSWORD`) into a throwaway keychain, stores
notarization credentials (`APPLE_ID`/`APPLE_TEAM_ID`/`APPLE_APP_PASSWORD`)
as the `slowbooks-notary` profile, and executes the exact script documented
below. On `v*` tags the signed, notarized, stapled DMG — the versioned name,
the stable `SlowBooksPro-macos-arm64.dmg` copy the website links to,
`SHA256SUMS.macos`, and the evidence bundle — is attached to the GitHub
release automatically.

The local path below remains fully supported: same script, same evidence
output. Use it as the fallback if CI signing is unavailable, and for
installed-app acceptance, which stays a human gate either way.

Developer ID signing and notarization on the maintainer's Mac:

1. Actions builds the app, verifies its native-library closure, exercises the
   Cocoa backend, creates a company, starts the server, and renders a PDF.
2. Actions uploads a checksummed app ZIP, transport DMG, dependency inventory,
   and build metadata for seven days. It never creates or changes a GitHub
   Release.
3. `release.py` verifies the artifact and source SHA, replaces every ad-hoc
   signature from the inside out, signs the final DMG, submits that DMG to
   Apple, retrieves the notarization log, staples the ticket, and runs final
   command-line gates.
4. Installed-app acceptance and public-release verification remain separate
   human gates.

Apple certificates, private keys, passwords, and notarization credentials must
never enter Git, Actions, build logs, or project notes.

## Build the transport artifact

While the workflow exists only on `macos-build`, pushing that branch triggers
the build. After the workflow reaches the default branch, it can also be
manually dispatched with the full commit SHA in `expected_sha`.

The artifact name is:

```text
SlowBooksPro-macos-arm64-<full-commit-sha>-unsigned
```

Download it with GitHub CLI and verify the files before signing:

```bash
repo="VonHoltenCodes/SlowBooks-Pro-2026"
run_id="<successful-macos-run-id>"
sha="<full-40-character-commit-sha>"
artifact="SlowBooksPro-macos-arm64-${sha}-unsigned"
download_dir="$HOME/Downloads/$artifact"

mkdir -p "$download_dir"
gh run download "$run_id" --repo "$repo" \
  --name "$artifact" --dir "$download_dir"
(cd "$download_dir" && shasum -a 256 -c SHA256SUMS)
test "$(awk -F= '$1 == "git_sha" {print $2}' \
  "$download_dir/build-info.txt")" = "$sha"
```

## One-time notarization setup

Store notarization authentication in Login Keychain. The command prompts for
the app-specific password; do not put it on the command line:

```bash
xcrun notarytool store-credentials slowbooks-notary \
  --apple-id "<developer-apple-id>" \
  --team-id "<apple-team-id>"
```

The Mac must have exactly one valid `Developer ID Application` identity, or the
release command must receive the exact identity through `--identity`.

## Sign and notarize

Run from a checkout of the same source commit used by Actions:

```bash
python3 packaging/macos/release.py "$download_dir" \
  --expected-sha "$sha" \
  --output-root "$HOME/Downloads/SlowBooksPro-release-candidates" \
  --notary-profile slowbooks-notary
```

The command creates a new evidence directory instead of overwriting a previous
attempt. A successful directory contains the final
`SlowBooksPro-<version>-macos-arm64.dmg`, its SHA-256 manifest, the source build
metadata (including the Actions run URL and attempt), transport and final-DMG
verification results, native-linkage report, nested code-signing details, and
Apple notarization submission and inspected log files.

`release.py` never uses `codesign --deep` to sign. It signs actual Mach-O files
and nested code from the deepest item outward. `--deep` is used only for the
final recursive verification gate.

## Installed-app acceptance

Do not publish on command-line proof alone. Using the exact final DMG:

- Mount it and launch once from the image.
- Copy the app to a new temporary Applications-equivalent directory, eject the
  image, and launch that copy. Never overwrite an existing installation during
  acceptance:

  ```bash
  acceptance_root="$(mktemp -d "${TMPDIR%/}/SlowBooksPro-Applications.XXXXXX")"
  ditto "/Volumes/SlowBooks Pro/SlowBooks Pro.app" \
    "$acceptance_root/SlowBooks Pro.app"
  open "$acceptance_root/SlowBooks Pro.app"
  ```

- Create and reopen a company, then quit and relaunch.
- Confirm data persists under
  `~/Library/Application Support/SlowBooksPro/data`.
- Upload a logo and render a PDF that contains it.
- Create a backup and exercise an export/download.
- Confirm no missing-library dialog or fatal entry appears in `launcher.log`.

After owner-approved merge, rebuild from the exact release-tag commit. Never
publish a development-branch artifact under an existing tag. Upload only the
signed, notarized, stapled DMG, then independently re-download it and repeat
the checksum, Gatekeeper, mount, and launch checks.

## In-fleet local signing on Macbase1 (VonHolten fleet)

The VonHolten fleet has a dedicated macOS signer, **Macbase1** (M1 Mac mini,
`ssh macbase1`), that signs + notarizes under the owner's Developer ID —
the in-fleet stand-in for the maintainer's Mac. Proven for SlowBooks v2.8.0 on
2026-09-04 (.app + .dmg notarized + stapled, `spctl` = "Notarized Developer ID").

Full operational runbook: **`devbase1:~/CLAUDE.md`** → "Local macOS build → sign →
notarize on Macbase1", and `/Users/macbase1/CLAUDE.md` on the box. In brief: run
this repo's `.github/workflows/macos.yml` build steps locally (venv off brew
`python3`, `export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix)/lib"`, install the
three requirements files, `pyinstaller … SlowBooksPro-mac.spec`), then Developer-ID
sign inside-out and notarize with the shared `fleet-signing.keychain-db` identity.

Fleet-specific gotchas that differ from a normal dev Mac:
- **Pass `--keychain ~/Library/Keychains/fleet-signing.keychain-db` to every
  `notarytool` call** — over SSH the default *login* keychain is locked.
- **`create-dmg` hangs headless** (its Finder AppleScript needs a GUI). Build the
  DMG with `hdiutil create -format UDZO`, then codesign it.
- The `--smoke-test` writes to `SLOWBOOKS_DATA_DIR`; keep that OUTSIDE the `.app`
  and sign **after** any launch — anything written into the bundle post-signing
  breaks the seal and Apple rejects notarization.

These are proof-of-pipeline builds; a real release still follows the tag-commit
rebuild + installed-app acceptance gates documented above.
