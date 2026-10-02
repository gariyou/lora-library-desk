# Changelog

## 0.1.0-alpha.2 — 2026-10-03

Security hardening.

- Send saved reference-image prompts and recorded txt2img settings to the current Forge checkpoint with one click. Persist recorded dimensions and scheduler in reference metadata; report unsupported fields.

- Reject requests whose Host header is not `localhost`, an IP address or a host given with `--allowed-host` (DNS rebinding protection).
- Accept `chrome-extension://` origins only from the bundled extension. The extension ID is now fixed by a `key` in its manifest (`cmfddaijajbljjdalpabmocolipfkafj`); extra IDs can be allowed with `--extension-id` or `LIBRARY_DESK_EXTENSION_IDS`. **Remove the old unpacked extension, load it again and re-enter the server URL.**
- `--lan` (or any non-loopback `--host`) now requires an access token for clients other than this PC. The token is stored in `data/lan-token.txt`, printed in the startup URL and can be regenerated with `--reset-lan-token`.
- Reject cross-site API requests that carry no Origin header (e.g. `<img>` tags on other web sites).
- Add GitHub Actions workflow running the Python and Node test suites.
- Sync `forge_bridge/` with gariyou/sd-forge-library-desk-bridge: the Forge routes now reject non-loopback Host headers, and button binding no longer depends on component creation order. CI checks that the bundled copy matches the pinned bridge revision, so separate pull requests and later upstream changes do not break the comparison.
- Fix startup on Python 3.10/3.11 (the documented minimum): remove backslashes inside f-string expressions and replace `hashlib.file_digest` (3.11+).

- Verify the updated bridge in real Windows Chrome/Forge Neo, including all-setting save/persistence/restore, Host rejection, and a local download imported by the fixed-ID extension. Update the public validation record and extension popup version label.

## 0.1.0-alpha.1 — 2026-10-02

First public preview for Windows and Forge Neo. Source and ZIP distributions contain no personal library data.

Includes configurable loopback connection URLs and per-checkpoint Forge settings. Authenticated downloads send Civitai cookies only on the initial HTTPS request and never forward them on redirects.
