# Vendored fonts

Self-hosted per the "vendor, don't CDN" rule (see `docs/DEPLOY.md` / `PLAN.md` §18) — an
external Google Fonts link is a new dependency this codebase otherwise avoids, and the venue's
wifi is exactly the thing that rule protects against. Files copied from the `@fontsource*` npm
packages already vendored in `jump/frontend` (same font, same subset, same provenance everywhere),
Latin subset only (covers French diacritics — the workshop content's language).

| Family | Files | Weight(s) | Source package |
|---|---|---|---|
| IBM Plex Sans Variable | `ibm-plex-sans-latin-wght-normal.woff2` | 100–700 (variable) | `@fontsource-variable/ibm-plex-sans` |
| Anton | `anton-latin-400-normal.woff2` | 400 (only weight it has) | `@fontsource/anton` |
| Space Mono | `space-mono-latin-{400,700}-normal.woff2` | 400, 700 | `@fontsource/space-mono` |

All three: **SIL Open Font License, Version 1.1.**

- IBM Plex Sans — Copyright 2019 IBM Corp.
- Anton — Copyright 2020 The Anton Project Authors.
- Space Mono — Copyright 2016 The Space Mono Project Authors.

Full license text: https://openfontlicense.org
