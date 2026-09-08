# Changelog

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
