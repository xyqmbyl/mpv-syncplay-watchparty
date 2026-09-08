# Security and privacy

## What is never required

Do not put an AList administrator token, Tailscale auth key, account password,
cookie, or signed AList URL into this project. Viewer playback uses only a
public AList `/d/...` media URL transported over the private Tailscale network.

## Host guidance

- Share only the host device, not the host's Tailscale account.
- Keep Tailscale Funnel disabled.
- Expose only the dedicated AList media mount as guest-readable.
- Keep the AList administrator account protected with a strong password.
- Revoke the Tailscale device share after it is no longer needed.

## Public repository boundary

The repository and release builder exclude AList databases, administrator
passwords, local media, playback history, runtime JSON state, host mappings,
Tailscale session state, logs, caches, screenshots, and machine-specific
CUDA/TensorRT/VapourSynth files.

Report a suspected credential leak privately to the repository owner. Rotate
the affected credential before publishing details.
