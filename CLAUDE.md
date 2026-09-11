# CTFd Coding Platform

Turning CTFd into the backend for a **workshop** platform (not code-only — see PLAN.md §9).
CTFd's own frontend, extended by a plugin, is the participant UI.

**Status: on `main`.** (The branch was called `mockup` until 2026-08-18; it stopped being one
long ago.) Phases 0–2 are done: a `workshop` plugin (quiz
challenge type, challenge-graph endpoint, single-page workshop view), a subject-repo → CTFd
content pipeline, and a validation suite (`scripts/phase2_validate.py`, 152 checks against a
fresh instance). A **purpose-built SPA is on hold** — PLAN.md §13 replaced the modal-per-
challenge board with a single-page workshop view inside CTFd, and §14 embeds the runtimes that
already exist as standalone web apps instead of rebuilding them.

`PLAN.md` is the design of record — read it before proposing architecture. §13 (participant UI)
and §14 (runtime embedding) are the two most recent decisions.

**Visual work: read [`DESIGN.md`](./DESIGN.md) first.** Token contract (colors, fonts, radius),
the Bootstrap component classes that bake literal color and ignore a variable remap, and why code
highlighting is `.ll-*` not `.hljs-*`. Its one implementation is
`plugins/workshop/assets/epitech-theme.css` — plugin-injected, `CTFd/` untouched, same rule as the
rest of this file.

## Repo layout

```
CTFd/               git submodule → kevin-cazal/CTFd (fork of Manta-Epitech-Academy/CTFd), master
plugins/workshop/   the CTFd plugin — bind-mounted into the image, never a core edit
tools/              ws_parser.py (shared parser/linter), sync_subject.py (repo → CTFd import)
content/            GONE from this checkout, and not tracked on any branch — the subjects
                    live in their own `*_subject` repos (deploy/instances.yaml lists them)
                    and an instance imports from GitHub through /admin/workshop/sync.
                    scripts/phase2_validate.py still syncs `content/pypong`, so its
                    instance half cannot run here until that is reconciled.
scripts/            phase*_validate.py — the regression suite, needs a FRESH instance
docs/               CONTENT_CONVENTION.md — the authoring convention
docker-compose.yml  stock CTFd on :8080 (8000/8001 are taken by ctfd_replication)
frontend/           the early React mockup — superseded, kept for reference only
PLAN.md             architecture + phased plan + decisions
CLAUDE.md           this file
```

### Never edit inside `CTFd/`

It is a submodule and must stay pristine so upstream updates remain a `git pull`. All custom code
goes in `plugins/workshop/`, bind-mounted over `CTFd/CTFd/plugins/workshop` at runtime. If you
think you need to modify CTFd core, say so and stop — that is a design change, not an
implementation detail.


### Do not implement code until things are figured out

I wan't the plan to be fully clarified before implementing code.

## Context: this is a real workshop, not a hypothetical

- **Audience: French lycéens, 15 to 18 years old, beginner to intermediate.**
- Subject: build a simple shooter game in **TIC-80 + Lua** ("Santa Shooter" -> check the repo santa_shooter on my github).
- Content source repo: `../santa_shooter` (separate repo, not vendored here).
- Frontend base: `../tic80-web-editor` — React 19 + Vite + TS, dockview panels, Monaco with TIC-80
  Lua completions, TIC-80 PRO WASM. **A working editor already exists; do not rebuild it.**

### Content conventions (inherited from `santa_shooter/CLAUDE.md`)

- All workshop content is written **in French**.
- Simple writing style. No "AI" phrasing, no uncommon characters, no em dashes.
- **Never give a student the answer.** Guide toward a simple solution. (This applies to hint text
  and any generated content, not to the maintainer.)

## Workshop structure

Four ordered chapters. Headings already map 1:1 onto challenges — the split is done, it needs a
manifest, not a parser:

| Chapter | Heading level = challenge | Count | Reference solutions | Topology |
|---|---|---|---|---|
| `starter1.md` | `##` (scope by parent `#`) | 9 | none (only `starter.lua`, final state) | linear |
| `visual_effects2.md` | `##` | 7 | `visual_effect/step1..7.lua` | 3 linear + 4 optional bonus |
| `code_refactor3.md` | `##` minus 2 prose-only | 5 | `code_refactor/step1..5.lua` | linear, **gates gameplay** |
| `gameplay4.md` | `###` | 10 | `gameplay/*.lua` (names match) | **free choice, independent** |

Two gotchas when writing the manifest:

1. **Not every heading is a challenge.** `code_refactor3.md` has 7 `##` but only 5 steps —
   "Notion d'objet en Lua" and "Pourquoi ce refactor" are prose. Never assume heading count equals
   challenge count.
2. **`starter1.md` headings repeat** ("Sprite" x3, "Input" x2). Slugs must be scoped by the parent
   `#`, e.g. `bullet/sprite`, or they collide.

Chapter order is encoded in the filename digit. `gameplay4.md` states explicitly that its challenges
are independent and may be done in any order, so it is a menu, not a chain, and should not be
rendered as a linear list.

## Key CTFd facts (verified against this checkout, CTFd 3.8.5)

- **Progressive unlocking is native.** `Challenges.requirements` is a JSON column:
  `{"prerequisites": [id, ...], "anonymize": "preview"}`. Locked challenges are filtered from list
  responses at `CTFd/CTFd/api/v1/challenges.py:196` and blocked from solving at `:711`. No custom
  code needed.
- **CTFd has zero CORS support** (no `Access-Control` anywhere in the codebase). A separate-origin
  SPA cannot call the API. Solution: one nginx, one origin, SPA at `/` and CTFd at `/api/`.
- **CSRF nonce is only injected server-side into Jinja**
  (`CTFd/CTFd/themes/core/templates/base.html:22`). A standalone SPA needs a small plugin endpoint
  to fetch it.
- `Authorization: Token ctfd_<hex>` bypasses CSRF entirely
  (`CTFd/CTFd/utils/initialization/__init__.py:388`) and needs `Content-Type: application/json`.
  The scheme word is ignored (`token.split(" ", 1)`). Not usable for attendees — tokens are
  self-generated per user in the settings UI.
- **`attempt()` is synchronous** in a Flask worker. Never execute user code there.
- `ChallengeResponse(status, message)` — `message` is a flat string, so structured test results
  need their own endpoint. Valid statuses: `correct`, `incorrect`, `partial`, `ratelimited`.
  `partial` is supported end to end (`api/v1/challenges.py:897`) and is how "4/6 passing" is
  reported.
- Custom challenge types subclass `BaseChallenge` with a polymorphic-identity model subclass.
  Template to copy: `CTFd/CTFd/plugins/dynamic_challenges/__init__.py`.
- CTFd has **no pending-review state**. Human-graded work uses the `Awards` API plus a plugin-side
  queue.

## Working with the submodule

```bash
git clone --recurse-submodules <this repo>
git submodule update --init --recursive   # if already cloned
```

## 1 CTFd instance for one workshop

I can have a lot of user (~2000) sometimes, a shared CTFd instance for all workshop whould not be good at scaling.


### 1 workshop can be multiple subject

A workshop can be multiple subject: generally an easy "starter" subject and after the starter more complex subjects can be present.
The constant is that the starter must ALWAYS be done before doing more advanced subject. Advanced subject can be in fixed order or not.

### Ideally 1 CTFd instance per workshop SESSION

A workshop session is the workshop being done at some place at some moment by some (identified) people (usually instructor-led).
But a CTFd instance can be session-free: anyone can do the workshop at any moment anywhere

## Never try to invent a workshop yourself

Use existing workshop from notion/github repo I gave you when you'll implement the project.


## Try to keep CTFd code clean

### Use a plugin approach (if applicable to CTFd codebase)

### If the plugin approach is not applicable

Derive from existing classes from CTFd, do not re-invent the wheel.

### Prefer simple/reusable implementation over complex/spaggetti code

### Document the feature you add to CTFd so it can be ported easily to future version of CTFd

### Platform strings: English in the source, French on the participant path

Every string the platform emits is **written in English in the source** and translated through
gettext. That has not changed: an English msgid is still what you type.

What changed is that the i18n phase happened, for the participant path only — the workshop index,
a part page, a step and its controls, the shell around them. Those are wrapped in `{% trans %}` /
`gettext` and carry a French translation in `plugins/workshop/translations/`, and an instance is
set to `fr` by default (`tools/provision.py`). The audience is French lycéens, under 18, arriving
from Jump — which speaks to them in French, with emoji. English chrome around French content was
the single loudest thing telling them they had left.

Three rules follow:

- **Add a string, wrap it.** `{% trans %}` in a template, `gettext` in a request, `lazy_gettext`
  for anything built at import time (`page.py`'s `NOTES` and `CARD_TEXT`). A bare literal is
  English forever and no linter will say so.
- **The admin panel stays untranslated.** Its audience is us and the instructors, one locale is
  one less thing to keep in sync, and none of it is in the catalogue.
- **Strings CTFd already translates are left alone.** flask-babel merges catalogues, so
  "Scoreboard", "Settings" and "Logout" take upstream's French for free. Our catalogue carries
  only what CTFd does not have — plus `Finish`, where upstream's "Fin" means the end of a CTF and
  ours means "termine this first".

Workshop *content* is unaffected and stays in the audience's language: that is data, not
interface.


## The frontend should be heavily focused on UI/UX

The mockup you provided does not really fit my needs.

- Easily understandable, even for a code beginner.
- Emphasis on the main part, hide all unnecessary UI elements (eventually add a menu to show them when needed)
- Do not overload the interface
- Some workshops already have a runtime existing as a web app, take that into account. I don't want to rebuild a runtime for a workshop if it already exist. Usually the runtime is a backendless web app (only HTML + JS + CSS, with sometimes WASM)
