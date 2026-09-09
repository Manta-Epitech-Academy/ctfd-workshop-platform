# DESIGN.md

Visual contract for the Epitech reskin. `plugins/workshop/assets/epitech-theme.css` is its one
implementation, injected via `register_plugin_stylesheet()` in `plugins/workshop/__init__.py` and
loaded on every page through CTFd core's `{{ Plugins.styles }}`. `CTFd/` is never touched for this
— same rule as everywhere else in this repo (see `CLAUDE.md`, "Never edit inside `CTFd/`").

No automated check enforces any of this (no `lint:design`, no contract test). This is prose, not
a gate — a deliberate scope call, not an oversight: see "What this deliberately isn't" below.

## Source of truth

Every value below is ported verbatim from `jump/frontend/src/routes/layout.css` (a sibling
Epitech product's own design contract) — its contrast ratios are already computed and commented
there; don't re-derive them here. If a token changes, port the diff from that file rather than
picking a new number by eye.

## Tokens

| Bootstrap var | Light | Dark |
|---|---|---|
| `--bs-primary` | `#013afb` (epi-blue) | `#809dfd` |
| `--bs-success` | `#007a46` (epi-tech-ink) | `#00ff97` (epi-tech) |
| `--bs-danger` | `#b5361a` (epi-together-ink) | `#ff9878` |
| `--bs-warning` | `#8a5a00` (epi-warning-ink) | `#ffd15c` |
| `--bs-dark` | `#0b0e1a` (epi-grey-900, jump's "chrome") | same |
| `--bs-body-bg` | `#f6f7fb` | `#0c0e13` |
| `--bs-body-color` | `#181818` | `#f1f2f6` |
| `--bs-border-color` | `#d9dce8` | `#262b38` |
| `--bs-secondary-bg` / `--bs-tertiary-bg` | `#eceef5` | `#1b1f29` |
| `--bs-secondary-color` | `#555b71` | `#8b90a3` |
| `--bs-info` | CTFd default, untouched | CTFd default, untouched |

**Why light and dark get different hue values, when CTFd's own don't**: `--bs-primary` etc. are
the *same literal value* in both of CTFd's compiled theme blocks. We deliberately diverge,
because the "ink" shades (`epi-tech-ink`, `epi-together-ink`, `epi-warning-ink`) are calibrated
for a light surface and stop clearing contrast on a dark one — `jump` solved this once with a
light/dark split; reuse it, don't re-solve it.

**`--bs-info` is a deliberate gap.** `jump` defines no "info" role. Inventing one here would be
new design, not reuse — so it stays at CTFd's own default in both themes rather than guessing.

## Typography

Self-hosted (see `plugins/workshop/assets/fonts/LICENSE.md` — never a Google Fonts `<link>`; this
codebase's own rule is "vendor, don't CDN", `PLAN.md` §18, because a live session on school wifi
cannot be trusted to fetch anything external).

- **IBM Plex Sans Variable** — base font everywhere (`html, body, .container`), replacing Lato.
- **Anton** — `.jumbotron .container` **only**, replacing Raleway. Single weight (it only has
  one). Do not extend this to a bare `h1` or any heading selector sitewide: Anton is a poster
  face, and `jump`'s own `layout.css` documents the exact bug that follows from applying it
  broadly (a browser fakes the missing bold weight by smearing the glyphs). A display face is a
  choice a heading states, not a default it inherits.
- **Space Mono** — monospace contexts (code blocks, below).

## Bootstrap limitations — read this before touching a color

A root-variable remap does **not** reach everything. Bootstrap bakes several component classes
with literal hex into their own scoped `--bs-btn-*` (etc.) vars at build time, so they never read
`--bs-primary` at runtime. If a future change to `--bs-primary` doesn't move a button, this is
why — the fix is a selector-level override, not a bigger variable.

Already handled in `epitech-theme.css`, and the reason each needed its own block:

- `.btn-primary`, `.btn-dark` (also every **unsolved challenge-board card** —
  `.challenge-button.btn-dark`), `.btn-success`, `.btn-danger`, `.btn-warning`,
  `.btn-outline-primary` — literal `--bs-btn-bg`/`-border-color`/`-hover-*`/`-active-*`.
- `.form-control:focus` / `.form-select:focus` — Bootstrap's alpha box-shadow ring, replaced with
  a solid outline (same reasoning `jump` gives: a box-shadow ring doesn't survive an
  `overflow-hidden` ancestor — the challenge modal, here — an outline does).
- `.form-check-input:checked` — literal, used by quiz radio/checkbox options.
- `.dropdown-menu`, `.list-group`, `.pagination`, `.progress-bar` — literal active-state
  backgrounds.
- `.table-primary` — literal computed tint, not var-based.
- `.challenge-button.challenge-solved` — hardcoded `#29c830` in **both** theme blocks of CTFd's
  own `_challenge.scss`, totally independent of `--bs-success`. `jump` already answered "what is
  solved green" (`--success: var(--epi-tech-ink)`); reused here instead of inventing a third
  green.

**Not fixable in CSS at all**: the scoreboard/category charts are Chart.js-via-ECharts,
`<canvas>`-rendered, colored per-name by a JS `colorHash()` — genuinely CSS-immune. Left at
Bootstrap default. Fixing it means a `CTFd/` JS edit, out of scope for this pass and for the whole
"never edit `CTFd/`" rule as it stands today.

## Code highlighting uses `.ll-*`, not `.hljs-*`

CTFd core ships `lolight`, not highlight.js. Classes are `.ll-key` / `.ll-str` / `.ll-com` /
`.ll-num` / `.ll-nam` / `.ll-rex` / `.ll-pct`, themed via `[data-bs-theme="dark"] .ll-*`. **Any
future port of a `jump`-style `.hljs-*` override is a silent no-op here** — check
`CTFd/CTFd/themes/core/assets/scss/includes/utils/_lolight.scss` for the live class list before
porting a syntax theme from anywhere else.

Code blocks (`pre`/`pre code`) render on a forced-dark background in **both** themes — the same
move `jump` makes for the same reason: the neon `--epi-tech` green is only legible on a dark
surface (1.33:1 on white per `jump`'s own contrast note), so code gets one always-dark surface
instead of a palette that has to switch with the page.

## "Always-dark furniture" — reused three times, not a system

`jump` has a fuller concept here (`chrome`, `.on-dark`, ink inversion per space). This codebase
has one audience, not four, so it borrows only the piece that's load-bearing: some surfaces are
dark regardless of page theme, and stay legible with the vivid (non-ink) brand hues rather than
the light-surface ink ones. Three places use it: the navbar (`--bs-dark`, always the epi-grey-900
"chrome" value), code blocks (above), and the "Parcours" path graph (below). No fourth is planned;
don't build a general mechanism for three call sites.

## The "Parcours" graph

`plugins/workshop/assets/workshop.css` (`.ws-parcours-*`, `.ws-lg-*`) and
`plugins/workshop/assets/graph.js` (literal SVG fill/stroke colors) **must stay in sync** — the
same solved/open/locked/current-path encoding, recolored to `--epi-tech`(-ink) / `--epi-blue` /
`--epi-grey-200`/`--epi-grey-900` instead of the stock dark-arcade palette it shipped with. A
future color change to one needs the same change in the other; neither reads the other's values.

## Radius

One flat value, `0.125rem` (jump's charte default), everywhere — `--bs-border-radius*`. No
per-space tiering: `jump` has one because it has four audiences with different needs (a talent
surface wants softer corners than a staff table); this codebase has one audience, so there is
nothing for a second tier to key off. Not exhaustively audited for Bootstrap components that bake
radius as a literal the way color does — verify visually (buttons, cards, inputs, badges) before
trusting full variable coverage on a new component.

## What this deliberately isn't

- **Not enforced.** No `lint:design`, no contract test. `jump`'s version has both because it has
  many contributors and an agent-editable component library; this is one CSS file behind one
  registration call, and the cost of a test harness here would outweigh what it catches. If drift
  becomes a real problem, revisit — don't build the enforcement speculatively.
- **Not a copy of `jump/DESIGN.md`.** That file documents concepts this codebase doesn't have:
  per-space skins, a component library, staff-table density rules. Copying its structure here
  would describe things that don't exist and rot the moment `jump` changes something CTFd never
  had in the first place.
- **Not per-subject.** Every instance shares one stylesheet. Per-subject visual customization is a
  separate, later, explicitly-scoped pass — not folded in here.
