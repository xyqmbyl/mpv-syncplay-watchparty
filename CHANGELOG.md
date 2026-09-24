# Changelog

## 0.4.1 - 2026-09-25

- Fixed the invalid Material Icons font that made mpv display icon names such
  as `chevron_right` instead of icons. Windows builds now reject a damaged font.
- Pinned the font download to a verified upstream commit and corrected its
  SHA-256 for both Windows and macOS CI builds.
- Moved the Intel Mac CI job off the retired `macos-13` runner.
- Corrected Windows CI's embedded-Python help check for non-UTF-8 runner locales
  and made the macOS mpv bundle lookup work at any archive depth.

## 0.4.0 - 2026-09-24

- Added macOS packages for Apple Silicon and Intel: `build-macos-package.sh`
  builds host and viewer bundles with mpv, the python.org standalone runtime,
  AList, Tailscale, uosc, fonts, first-run setup and the same audit rules as
  the Windows packages; binaries keep their official signatures with an
  ad-hoc fallback so Gatekeeper does not flag unpacked files as damaged.
- Split the Windows packages into x64 and x86 builds: x64 reuses the proven
  64-bit runtime, x86 bundles the official mpv 0.41.0 i686 runtime plus the
  python.org 3.14.2 embedded distribution; every packaged binary is audited
  for its PE machine type and every shipped installer has a matching
  per-architecture SHA-256 pin.
- Simplified release download names to `WatchParty-Host-Windows-x64.zip`,
  `WatchParty-Viewer-Windows-x86.zip`, `WatchParty-Host-macOS-AppleSilicon.zip`
  and so on, each accompanied by a `.sha256` checksum file and a
  `PACKAGE-CONTENTS.sha256` manifest inside the archive.
- Release viewer packages now ship without a preset host address: the
  first-run wizard asks for the host's Tailscale address until one is
  entered. `-TailscaleHost` / `--tailscale-host` still bakes a preconfigured
  package for private distribution.
- Made the Tailscale integration cross-platform: Windows keeps the bundled
  per-architecture MSI, macOS uses the official Standalone pkg and
  `/Applications/Tailscale.app` CLI, and the first-run wizards now accept the
  host address interactively.
- Added a GitHub Actions workflow that builds and audits the six non-x64
  packages from a `v*` tag push.
- Fixed viewers being treated as not ready while still buffering without a
  `time-pos` during the paused loading phase, which left them on a black
  screen after the host started playback; readiness now also checks the
  media duration and established audio/video tracks.

## 0.3.0 - 2026-09-23

- Bundled the danmaku plugin uosc_danmaku 2.2.0 (MIT) into both the host and
  the viewer package, together with its license text and third-party notices.
- Changed the danmaku defaults to a font size of 35 and a display area of 0.4,
  and every style change made in the danmaku settings menu is now written to
  `portable_config/script-opts/uosc_danmaku.conf` as the new default, so it
  survives a restart; “restore default” removes the entry again.
- Kept danmaku a purely local OSD overlay driven by the local `time-pos`
  observer: the host and each viewer toggle it independently, it freezes while
  the video is paused and realigns on seek. Syncplay playback, pause, seek,
  delay compensation and MiniServer behaviour are unchanged.
- Extended both packaging audits to require the bundled danmaku scripts and
  license while still rejecting `danmaku-history.json` and machine state.

## 0.2.0 - 2026-09-09

- Added a complete portable host package with mpv, embedded Python, Syncplay,
  AList v3.64.0, first-run setup, launch helpers, licenses, and reproducible
  package manifests.
- Standardized remote media access on Tailscale Device Sharing and direct
  `http://100.x.x.x:5244` URLs while keeping Funnel and Serve disabled.
- Added host automation for AList startup, `0.0.0.0:5244`, `/media`, anonymous
  guest access, signing removal, Tailscale address discovery, and Range 206
  verification.
- Added viewer-side diagnostics which distinguish installation, login, Device
  Share, TCP 5244, `/ping`, guest, signing, and HTTP Range failures in Chinese.
- Added clean-install and repeat-run coverage, including preservation of an
  existing administrator password and `alist_map` configuration.
- Fixed the AList v3 setting-save request used by a completely new database.
- Updated the two-person quick-start documentation and host/viewer packaging
  audits.

## 0.1.2 - 2026-09-09

- Added clear diagnostics when the host/viewer scripts are run from a source
  checkout that does not contain the full mpv/Python runtime.
- Hardened the helper scripts for paths containing `!` when delayed expansion
  is enabled in the parent `cmd.exe` process.
- Refreshed the viewer package and checksum after the batch compatibility fixes.

## 0.1.1 - 2026-09-09

- Fixed Windows `cmd.exe` path and encoding errors in the Tailscale batch helpers;
  they are now UTF-8 without a BOM and use CRLF line endings for reliable
  Explorer/double-click execution; the embedded Python output encoding is
  explicitly set to UTF-8 so status text is readable in the Windows console.
- Host, viewer, and status helpers now prefer the matching Tailscale CLI beside
  the portable player instead of relying on a stale inherited PATH.
- Viewer startup now finds the current official `Tailscale IPN` installation
  through the bundled helper, while retaining compatibility with older paths.
- Added a regression test for bundled Tailscale CLI discovery.

## 0.1.0 - 2026-09-08

- Added the compact two-level Syncplay control panel for mpv.
- Added AList local-path to public-media URL mapping.
- Added remote media probing, HTTP Range diagnostics, and actionable errors.
- Added automatic remote loading with cache and readiness coordination.
- Added Tailscale host/viewer setup helpers.
- Added a minimal, audited Windows viewer package builder.
- Fixed typed mpv IPC property writes for pause, speed, cache, and read-ahead.
## Unreleased

- 修复观看者在暂停加载阶段没有 `time-pos` 时被误判为未就绪，导致房主开始播放后观看者仍黑屏的问题。现在会同时检查 mpv 的媒体时长和已建立的音视频轨道。
