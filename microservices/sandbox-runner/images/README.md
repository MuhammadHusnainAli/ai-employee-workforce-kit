# Sandbox images

Runs never name a raw image: only the allowlisted ones in `src/domain.rs` (`IMAGES`), pinned by tag.

| Name | Image | Runs |
|---|---|---|
| `python-3.13` | `python:3.13-alpine` | `python -I /tmp/main.py` |
| `node-22` | `node:22-alpine` | `node /tmp/main.js` |

Each needs `/bin/sh` and `base64` (both alpine images have them through busybox): the launcher decodes the code
and stdin from environment variables into `/tmp`, the only writable place. Adding an image means adding it to
`IMAGES`, with a test in `tests/sandbox.rs` that runs it. In production pin images by digest in the registry the
sandbox node pool pulls from.
