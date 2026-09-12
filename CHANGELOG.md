# Changelog

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
