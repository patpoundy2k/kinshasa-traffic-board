# Kinshasabouge — iOS app

Thin iOS wrapper around the live Kinshasa Traffic Board at
https://kinshasabouge.com/ — the same idea as the Android app
(`android/kinshasa-traffic-board/` in the workspace): the site does all
the work (French-first bilingual board: live artery map, roadworks,
flights, weather, radio), and the app shows it full-screen, always
current. Nothing is bundled.

- **App name:** Kinshasabouge
- **Bundle ID:** `ca.patsrides.kinshasatrafficboard`
- **Version:** 1.0 (build 1)
- **Deployment target:** iOS 16.0, iPhone only, Swift 5, SwiftUI
- **No third-party dependencies** — WebKit only, so the project builds
  with zero network fetches beyond Xcode itself.

## What the app does

- Opens https://kinshasabouge.com/ in a `WKWebView` (JavaScript on;
  `localStorage` persists between launches, so the site's language toggle
  and jam reports are remembered).
- Thin loading bar pinned to the top, driven by the web view's progress.
- Bilingual offline screen (French first) with a **Réessayer / Retry**
  button, shown only when the main-frame load fails (network error or
  HTTP ≥ 400) — same wording as the Android app.
- `tel:` / `mailto:` / `sms:` links open in the system apps; `http:` /
  `https:` links stay inside the app.
- No geolocation, no file uploads, no camera/mic/location permissions.

## Build it in Xcode

1. Open `Kinshasabouge/Kinshasabouge.xcodeproj` in Xcode 15 or 16.
2. Select the **Kinshasabouge** scheme and an iPhone simulator (or a
   device, once signing is set up — see below).
3. Run (⌘R). To run on a real iPhone without a paid account, Xcode can
   sign with a free personal team for development; for the App Store you
   need the paid program (see checklist).

## CI

`.github/workflows/ios-build.yml` runs on macOS runners, with no secrets:

1. **Simulator smoke test** — `xcodebuild build-for-testing` for the iOS
   Simulator, unsigned. This proves the project compiles.
2. **Unsigned archive** — `xcodebuild archive` for generic iOS,
   unsigned, packaged as `Kinshasabouge-unsigned.ipa` and uploaded as a
   workflow artifact.

It triggers on pushes to `ios/**` (and to the workflow file itself), or
manually from Actions → iOS build → Run workflow.

## What an unsigned build can and cannot do

- ✅ Verifies the project compiles and the app launches in the simulator.
- ✅ Produces an `.ipa` artifact you can inspect (e.g. check the bundle
  ID, icon, and that the site loads).
- ❌ **Cannot install on a real iPhone.** iOS refuses unsigned apps on
  devices. The simulator job is compile verification only.

## App Store checklist (owner still needs)

- [ ] **Apple Developer Program** — $99/year, enrolled with the owner's
      own Apple ID (only the owner can enroll and pay).
- [ ] **App Store Connect app record** — create "Kinshasabouge" with
      bundle ID `ca.patsrides.kinshasatrafficboard`.
- [ ] **Distribution certificate + provisioning profile** — created in
      the developer account, then stored as GitHub secrets (see the
      commented section at the bottom of `.github/workflows/ios-build.yml`)
      so CI can sign the release build.
- [ ] **Screenshots** — iPhone 6.9" and 6.5" displays (required sizes),
      plus the app description, keywords, support URL, and privacy
      policy URL (the site already has a privacy page).

Note for review: Apple sometimes rejects apps that are only a website in
a wrapper (Guideline 4.2). The listing should show the board's real,
interactive features — live map, traffic reports, flights, radio —
rather than presenting it as just the website.
