# workshop — CTFd plugin

Custom CTFd behavior for the workshop platform. Zero modifications to CTFd core: the plugin
is bind-mounted into `CTFd/CTFd/plugins/workshop` by `docker-compose.yml`.

Verified against CTFd 3.8.5. Design of record: `PLAN.md` §9-12,
`docs/CONTENT_CONVENTION.md`.

## Provided: challenge type `quiz` (Phase 1)

Auto-graded quiz, four kinds. The **question** is authored in the challenge description
(markdown); the **propositions** go in `quiz_spec` so the participant gets real controls;
correct answers live server-side in the `quiz_answers` JSON column and are **never
serialized to the client** (`read()` exposes only `quiz_type` and `quiz_spec`).

| kind | UI | `quiz_spec` | `quiz_answers` | wire submission |
|---|---|---|---|---|
| `single` | radio buttons | `{"items": [{"letter": "A", "text": "..."}]}` | `{"answer": "B"}` | `B` |
| `multiple` | checkboxes | same `items` shape | `{"answers": ["A","D"]}` | `A,D` (order-free) |
| `match` | one select per row | `{"left": [...], "right": [...]}` (same item shape) | `{"pairs": {"A":"c","B":"a"}}` | `A-c,B-a` (order-free, `:` ok) |
| `freeform` | text input | none | `{"patterns": ["^regex$"], "case_sensitive": false}` | free text, any pattern matches |

Grading happens in `attempt()` (`quiz.py`); submissions go through the standard
`/api/v1/challenges/attempt` endpoint, so solves, scoreboard, rate limiting, and
prerequisite unlocking are inherited from CTFd untouched.

`assets/view.html` overrides the theme's `{% raw %}{% block input %}{% endraw %}`: controls
write the wire grammar into the Alpine `submission` state and mirror it into a hidden
`#challenge-input`, so both the theme submit path and the plugin-script path read the same
value — the core `view.js` is reused untouched. A quiz without `quiz_spec` falls back to
the stock text input, so it is never un-answerable.

All plugin-visible strings are English; localization arrives later via CTFd's i18n
(`{% raw %}{% trans %}{% endraw %}` markers are already in place in `view.html`).

## Portability notes (for future CTFd upgrades)

- Structure copied from `CTFd/plugins/dynamic_challenges` — if that plugin still works on
  a new CTFd version, this one almost certainly does too.
- Touch points with core, in full: `CHALLENGE_CLASSES` registry, `BaseChallenge` subclass
  API (`create/read/update/attempt`), `register_plugin_assets_directory`,
  `ChallengeCreateException/ChallengeUpdateException`, and the two admin template blocks
  (`header`, `value`) extended by `assets/*.html`.
- The two admin-side pages (`feedback.py`, `answers.py`) touch core only through
  `admins_only`, `register_admin_plugin_menu_bar`, and `{% raw %}{% extends "admin/base.html" %}{% endraw %}`
  with its `content` block. They are plain Blueprints — nothing about them is challenge-type
  machinery, so they survive a CTFd upgrade as long as the admin theme keeps that block.
  `answers.py` additionally reads the `workshop_validation` config key written by the sync; if
  it is missing the page infers the mode and says so, so an un-synced instance still renders.
- Tables are created by `app.db.create_all()` in `load()` (new tables only). If a future
  change **alters** a column, add a `migrations/` directory and switch to
  `CTFd.plugins.migrations.upgrade(plugin_name="workshop")`.
