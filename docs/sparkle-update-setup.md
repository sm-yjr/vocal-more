# Sparkle update setup

This repository now contains the app-side update entry point and Sparkle hooks. The menu bar item stays disabled until you finish the external setup below.

## Already done in code

- Added a `Check for Updates…` menu item.
- Added optional Sparkle integration in `VocalMore/VocalMore/AppDelegate.swift`.
- Added placeholder Sparkle config keys in `VocalMore/VocalMore/Info.plist`.
- Kept the code safe before Sparkle is linked by guarding imports with `#if canImport(Sparkle)`.

## Still required from you

1. Add the Sparkle package in Xcode.
2. Generate the Sparkle signing key pair.
3. Replace the placeholder values in `Info.plist`.
4. Build, sign, notarize, and zip the app.
5. Generate `appcast.xml`.
6. Upload the archive and appcast to your HTTPS host.

## 1. Add Sparkle to the Xcode project

In Xcode:

1. Open `VocalMore.xcodeproj`.
2. Select the project root.
3. Open `Package Dependencies`.
4. Add:

```text
https://github.com/sparkle-project/Sparkle
```

5. Link the `Sparkle` product to the `VocalMore` target.

## 2. Generate Sparkle signing keys

You need:

- Public EdDSA key for `SUPublicEDKey`
- Private signing key kept outside the repository

Replace this placeholder:

```xml
<key>SUPublicEDKey</key>
<string>REPLACE_WITH_SPARKLE_PUBLIC_ED_KEY</string>
```

## 3. Point the feed to your HTTPS host

Replace:

```xml
<key>SUFeedURL</key>
<string>REPLACE_WITH_UPDATE_FEED_URL</string>
```

with your stable HTTPS appcast URL, for example:

```xml
<key>SUFeedURL</key>
<string>https://updates.example.com/appcast.xml</string>
```

Do not use a temporary signed URL. Keep it stable.

## 4. Suggested hosting layout

```text
/appcast.xml
/releases/VocalMore-0.4.0.zip
/release-notes/0.4.0.html
```

Use versioned archive names. Do not overwrite a single `latest.zip`.

## 5. Release flow

High-level flow:

1. Build the `.app`
2. Sign with Developer ID
3. Notarize
4. Zip the app bundle
5. Sign the archive with Sparkle tooling
6. Generate `appcast.xml`
7. Upload files to your HTTPS host

Typical zip command:

```bash
ditto -c -k --sequesterRsrc --keepParent VocalMore.app VocalMore-0.4.0.zip
```

## 6. Automatic checks

This repository currently keeps:

```xml
<key>SUEnableAutomaticChecks</key>
<false/>
```

That is intentional. Enable automatic checks only after the manual update path works end-to-end.

## Notes

- If `SUPublicEDKey` or `SUFeedURL` still starts with `REPLACE_WITH_`, the update menu item will stay disabled.
- This is for non-App-Store distribution.
- Sparkle updates the full app bundle, which fits this project's bundled backend model.
