# v0.3.0 uosc UI baseline

`uosc/` contains the 29 Lua/JSON UI files from the official v0.3.0
`MPV-Syncplay-Host.zip`, without its platform-specific Ziggy executable.
It is the project's customized uosc 5.12.0 UI, including the playback,
statistics, thumbnail and danmaku controls. Do not replace it with the
upstream uosc 5.12.0 files: the upstream archive has different controls,
scaling and menu behavior despite reporting the same version number.

Source: https://github.com/xyqmbyl/mpv-syncplay-watchparty/releases/tag/v0.3.0

- Source ZIP SHA-256: `255A56FD32174D44829289B556FD8AA11BF09E591E220AFAAC97BD6F15109714`
- `uosc/main.lua` SHA-256: `20482D3906000C52DE11D4FFD73D19C530B447AE28A2857BBA6DD072DDEEA319`

The packaging process adds the Ziggy executable for the target platform
separately. It must keep `ziggy-darwin` for macOS and `ziggy-windows.exe`
for Windows without altering these UI files.
