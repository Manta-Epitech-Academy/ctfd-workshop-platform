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
| `checkpoint` | text input, or a lone button | none | `{"code": "4b279a"}` | the instructor's code, or `done` in self-serve |

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

## The instance's mode (PLAN.md §25)

`workshop_mode` — `instructor_led` (default) or `self_serve` — is a CTFd config key, set at
provisioning and flippable at `/admin/workshop/settings`. It decides one thing: whether a
`checkpoint` step asks for the code an instructor reads out, or offers a button because there
is nobody to ask. `mode.py` is the only reader; `quiz.py` consults it at submit time and
`page.py` at render time, so a flip needs no re-sync and moves no solve.

The code is stored on the challenge either way and is never serialized to a client, which is
what makes the flip reversible. `checkpoint.py` carries the two admin-only routes the importer
needs: reading the codes back (`quiz_answers` is excluded from every read schema) and
converting a pre-§25 instance in place — `standard` challenge plus a `Flags` row becomes a
checkpoint challenge, same id, same solves.

## Syncing from the subject repository (PLAN.md §26)

`/admin/workshop/sync` imports the instance's workshop straight from GitHub: resolve the ref
to a commit, fetch the tarball, lint, then run `tools/sync_subject.py` — the same importer the
CLI runs, mounted read-only at `/opt/workshop/tools` rather than copied. `workshop_source`
holds `{repo, ref}`; `workshop_last_sync` holds what the last import brought in.

A workshop repo names its subjects by repo, and `ref: submodule` follows the pin the wrapper
records. A tarball carries submodules empty, so the pin is read from the contents API instead
— no git in the container, and none wanted.

Encrypted answers (`flags.yaml.gpg`) are opened **in the admin's browser** with a vendored
openpgp.js: the job parks, hands the blob to the page, and takes back the plaintext. The
passphrase never reaches the server. PGPy would have been the server-side route and does not
work — it calls `cryptography.utils.register_interface`, gone since cryptography 37, and the
image ships 45.

The job runs in a thread, which is a greenlet under the gevent worker, so an import neither
blocks the instance nor deadlocks when it calls the instance's own API.

## The way in from Jump (PLAN.md §31)

Jump is the only door into a workshop instance. `jump.py` verifies a short HMAC-signed ticket at
`GET /jump/enter?t=…`, creates the account on first arrival with **no password** (so CTFd's own
local sign-in refuses it), and opens the session. `jumpqueue.py` overrides `QuizChallenge.solve()`
to queue the solve in `workshop_jump_event` and a background drainer posts a signed progress
callback, so a slow or absent Jump never slows a participant down.

Two config keys, both provisioned by `tools/provision.py`: `workshop_jump_keys`
(`{kid: {origin, secret, label}}`) and `workshop_jump_instance` (this instance's slug). Empty
means every ticket is refused. `/admin/workshop/jump` edits both and shows the outbox.

The ticket is not a JWT because there is no JWT library in the image and none can be added — the
Dockerfile's plugin-requirements loop runs at build time over the `./CTFd` context while this
plugin arrives at run time as a bind mount. See the `jump.py` docstring for the frozen wire
contract shared with the Jump repository; changing either half means changing both.

## Portability notes (for future CTFd upgrades)

- Structure copied from `CTFd/plugins/dynamic_challenges` — if that plugin still works on
  a new CTFd version, this one almost certainly does too.
- **Three core templates are overridden outright** (`shell.py`, via CTFd's own
  `override_template`): `components/navbar.html`, `page.html` and `login.html`. An override is
  silent by construction — if upstream renames or restructures one of these, `overridden_templates`
  keeps serving ours and the only symptom is a page that quietly stops matching the theme. `load_shell`
  checks each stock file still exists and logs a warning at boot, so **read the boot log after a CTFd
  upgrade**. What each one depends on:
  - `navbar.html` — `Plugins.user_menu_pages`, and a `.theme-switch` wrapping an `<i class="fas">`,
    which `color_mode_switcher.js` dereferences with no null check.
  - `page.html` — that `views.static_html` still passes `title` alongside `content`.
  - `login.html` — `Forms.auth.LoginForm()`, `components/errors.html` and the `integrations.mlc()`
    branch. The form is core's field for field; only the layout around it is ours, so an upstream
    change to authentication does not have to be mirrored. It also carries the **"Continue with
    Jump" button**, in its own `{% raw %}{% if jump_enabled() %}{% endraw %}` above the MLC branch
    and never nested in it — so the MLC path stays byte for byte what core renders, and an upstream
    change to it is a straight re-copy.
- **The runtime pop-out renders its own page, not `base.html`** (`templates/workshop_runtime.html`,
  PLAN.md §30). It carries one frame and one bar, so inheriting the shell only to hide a header, a
  hero and a footer would be more markup rather than less. It therefore restates three things
  `base.html` would have supplied, and a CTFd upgrade can move any of them:
  - `window.init` with `urlRoot`, `csrfNonce` and `userId` — the three keys the host actually reads
    (the user id stamps the `localStorage` bucket, the nonce signs the snapshot POST). Same names as
    `base.html`, so the host needs no second code path.
  - `Assets.css("assets/scss/main.scss")` plus `Plugins.styles`, the same pair every page loads.
  - the theme, set inline from the `theme` key rather than by loading
    `color_mode_switcher.js` — that file dereferences `.theme-switch i.fas` with no null check and
    this page has no theme toggle to give it.
- **The participant path is translated** through CTFd's own flask-babel. `load()` appends
  `plugins/workshop/translations` to `BABEL_TRANSLATION_DIRECTORIES`, which works because
  `Domain.translation_directories` reads that config at lookup time rather than at init, and
  `get_translations` merges every directory in it. If a CTFd upgrade changes either of those two
  behaviours, the plugin's strings fall back to English rather than breaking — but check it.
- **`assets/vendor/` is a build artifact**, not committed: `tools/build_vendor.sh` fetches
  openpgp.js, canvas-confetti and lolight at pinned versions with their sha256 verified. Without it
  the sync page cannot decrypt answers, the workshop page draws no confetti, and code injected
  after a solve stays monochrome.
- **The plugin carries a second copy of lolight on purpose.** The core theme highlights `pre code`
  once on `DOMContentLoaded` and does not expose the library, so every step body the workshop page
  fetches after a solve would arrive unhighlighted. Pin it to whatever
  `themes/core/package.json` asks for — today `"lolight": "^1.4.0"`, so 1.4.1 — and after a CTFd
  upgrade check two things: that the core still tokenizes into `.ll-*` classes, and that its own
  auto-run still targets `.lolight` (a class nothing here uses) rather than something this page
  has. `workshop_page.js` re-tokenizes from `textContent` and is idempotent, so the two copies
  cannot double-wrap each other.
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
- Tables are created by `app.db.create_all()` in `load()` (new tables only), **and then**
  `CTFd.plugins.migrations.upgrade(plugin_name="workshop")` runs `migrations/`. Both, not either:
  the quiz and workspace tables predate the migrations directory and are in no revision, while
  `create_all()` never issues an `ALTER`, so any future column change has to be a revision or it
  surfaces as an `OperationalError` at request time on every instance at once. Revisions are
  written by hand and must be idempotent (`if "<table>" in get_all_tables(op): return`) — on a
  fresh instance `create_all()` has already built the table by the time the revision runs. The
  current head is in the `workshop_alembic_version` config key. `upgrade()` short-circuits to
  `create_all()` on sqlite, so a revision is only ever exercised on MySQL/MariaDB or Postgres.
- **The Jump handoff (`jump.py`, `jumpqueue.py`) is the plugin's only authentication code**, and
  it deliberately bypasses the instance's registration path — see the `jump.py` docstring. Its
  touch points with core are `login_user`, `session.regenerate()`, `cache.add`, `Users`, and
  `CTFd.plugins.migrations`. Two of those are worth re-reading after an upgrade:
  - `session.regenerate()` — copied from the local sign-in path (`auth.py`), not the OAuth one,
    which does not regenerate. If upstream changes how a session is rotated, this must follow it.
  - `cache.add` for single-use tickets. It must stay a SETNX; a get-then-set has a window, and
    two requests carrying one ticket would both succeed. Note that `cache.inc` is **not** usable
    beside it: flask-caching pickles what it stores, so redis `INCR` on a key written through
    `cache.set` fails.
