# Release DMG

Vocal-More ships macOS builds from version tags through GitHub Actions. Pushing
a tag such as `v0.2.2` or `0.2.2` starts `.github/workflows/release.yml`.
The current release runner is Apple Silicon, so official DMGs are arm64-only.
The application and bundled binary wheels require macOS 14.0 or newer; this
floor is declared with `LSMinimumSystemVersion` and is also used when compiling
the native audio library.

The workflow checks that the tag version matches `pyproject.toml`, runs tests,
imports the Developer ID certificate, builds a signed DMG, notarizes and
staples it, then uploads the DMG to the matching GitHub Release.
Frontend checks and Python tests run concurrently. The production build reuses
that prepared Python environment and frontend bundle, skips the intermediate
ad-hoc signature, and signs independent nested Mach-O files concurrently.
It also signs and publishes `appcast.xml` to the stable `sparkle-feed` release,
which lets installed builds check for and install new versions with Sparkle.
For each release, the workflow downloads the preceding official DMG, uses
Sparkle's `generate_appcast` tool with fast LZFSE compression to create a
signed delta update, and uploads the resulting `.delta` alongside the new full
DMG. Sparkle uses the matching delta when possible and automatically falls
back to the full DMG if the installed app does not match or the patch cannot be
applied.

The app build compiles `libvocal_more_audio.dylib` with the Apple SDK, embeds it
under `Vocal More.app/Contents/Frameworks`, prunes the complete bundle, and then
signs the dylib as a nested Mach-O before signing the app. The library exposes a
versioned C ABI and links only Apple system frameworks and runtime libraries
under `/System/Library` and `/usr/lib`—including AVFoundation, Accelerate,
Foundation, libc++, and libSystem. It does not receive a separate entitlement;
microphone permission belongs to the main signed app identity.

After notarization and stapling, the release workflow mounts the final DMG
read-only and verifies the exact app that will be uploaded. The verifier checks
that the native library exists, is arm64-only, declares macOS 14.0, has the
expected `@rpath` install name, links only Apple system dependencies, exports
the required C ABI, and carries a valid nested code signature:

```bash
python3 packaging/macos/verify_release_artifact.py \
  "dist/Vocal-More-$(python3 packaging/macos/read_version.py).dmg"
```

Routine feature verification should run the test suites and
`scripts/build_native_audio.sh`; it must not build an unsigned local DMG. The
official tagged workflow remains responsible for the DMG, Developer ID
signature, notarization, stapling, release upload, and signed Sparkle appcast.

## Required GitHub Secrets

Configure these repository secrets before pushing a release tag:

| Secret | Purpose |
| --- | --- |
| `MACOS_CERTIFICATE_P12_BASE64` | Base64-encoded Developer ID Application `.p12` containing the private key. |
| `MACOS_CERTIFICATE_PASSWORD` | Password used when exporting the `.p12`. |
| `APPLE_ID` | Apple ID used for notarization. |
| `APPLE_TEAM_ID` | Apple Developer Team ID. |
| `APPLE_APP_SPECIFIC_PASSWORD` | App-specific password for the Apple ID. |
| `SPARKLE_PRIVATE_KEY` | Ed25519 private key used to sign update archives and the appcast feed. |

Export the certificate from Keychain Access as a `.p12` file, then encode it:

```bash
base64 -i DeveloperIDApplication.p12 | pbcopy
```

Paste the copied value into `MACOS_CERTIFICATE_P12_BASE64`.

With the GitHub CLI, the same setup can be done from the repo root:

```bash
base64 -i DeveloperIDApplication.p12 | gh secret set MACOS_CERTIFICATE_P12_BASE64
gh secret set MACOS_CERTIFICATE_PASSWORD --body "<p12-export-password>"
gh secret set APPLE_ID --body "<apple-id-email>"
gh secret set APPLE_TEAM_ID --body "<team-id>"
gh secret set APPLE_APP_SPECIFIC_PASSWORD --body "<app-specific-password>"
gh secret set SPARKLE_PRIVATE_KEY < sparkle-private-key
```

## Release Flow

Every release must include a non-empty `docs/releases/<version>.md` file. This
file is the source of truth for both the GitHub Release body and the release
notes embedded in the signed Sparkle appcast. The workflow rejects a tag when
the matching file is missing or empty, and reapplies it when retrying an
existing release.

Update `pyproject.toml`, keep the editable project version in `uv.lock` aligned,
write the release notes, commit the changes, then push a matching tag:

```bash
git tag v0.2.2
git push origin v0.2.2
```

The workflow fails early if the tag version does not match the project version
or `docs/releases/0.2.2.md` is absent.

Delta generation depends on the preceding GitHub Release retaining its
original `Vocal-More-<version>.dmg` asset. Do not delete the most recent stable
release asset. The release job fails if that historical DMG is available but
no delta can be generated, which prevents silently publishing a full-only
update.
