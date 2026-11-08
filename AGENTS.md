# Agent instructions

## Preview must stay current
- The UI preview lives at `https://sazzadvps.taildd3bd9.ts.net/fsne-ui/`.
- After ANY edit lands that affects the UI (`ui/template.html`, `ui/build.py`, pipeline output feeding it), rebuild and republish:
  ```bash
  uv run python ui/build.py && cp ui/index.html ~/previews/fsne-ui/index.html  # the `preview` helper may be absent; this is equivalent
  ```
- Always print the final preview URL in your reply.
- Edit `ui/template.html`, never `ui/index.html` (generated).
