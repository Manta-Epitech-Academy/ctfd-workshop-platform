# workshop — CTFd plugin

Custom CTFd behavior for the workshop platform. Zero modifications to CTFd core: the plugin
is bind-mounted into `CTFd/CTFd/plugins/workshop` by `docker-compose.yml`.

Verified against CTFd 3.8.5. Design of record: `PLAN.md` §9-12,
`docs/CONTENT_CONVENTION.md`.

## Provided: challenge type `quiz` (Phase 1)

Auto-graded quiz, six kinds. The **question** is authored in the challenge description
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
| `quizset` | every question of the step, one submit | `{"questions": [{"kind": "single", "items": [...]}, ...]}` | `{"questions": [{"kind": "single", "answer": "B"}, ...]}` | `B\|A,D` (`\|` between questions) |

`quizset` is what a step declared `validation: quiz` in the content becomes
(`tools/sync_subject.py`, convention §3.3b): its own questions are the control, graded
together, and a wrong submission names the questions to revisit rather than saying only
"Not yet" — never the answer.

Grading happens in `attempt()` (`quiz.py`); submissions go through the standard
`/api/v1/challenges/attempt` endpoint, so solves, scoreboard, rate limiting, and
prerequisite unlocking are inherited from CTFd untouched.

`assets/view.html` overrides the theme's `{% raw %}{% block input %}{% endraw %}`: controls
write the wire grammar into the Alpine `submission` state and mirror it into a hidden
`#challenge-input`, so both the theme submit path and the plugin-script path read the same
value — the core `view.js` is reused untouched. A quiz without `quiz_spec` falls back to
the stock text input, so it is never un-answerable.

Plugin-visible strings are English msgids, marked with `{% raw %}{% trans %}{% endraw %}`
in `view.html`. The participant path carries a French translation in `translations/`,
and the `3a6016a2732e` migration sets every instance to French by default (PLAN.md §42).

## The toolbox page (`/toolbox`, convention §3.4b)

Reference material an author fences in the content — `<!-- ws:toolbox -->` around a step's
tools, `<!-- ws:glossary -->` around a glossary — is lifted out of the step and gathered on
`/toolbox`, a navbar entry that opens in its own tab. The step keeps a line in the same
position naming what was there (names derived from the `🧰`/`🗺️` titles, nothing to
maintain) and linking to it. A section is open exactly when its step is: a locked step shows
its name and nothing else, so the page has the same shape from the first minute. Everything
lives in `toolbox.py` (pure helpers) and `page.py` (`_reference`, the route); the markers
travel inside the challenge description like `ws:resume` does, so nothing is stored twice.

## Enlarging an image, and its caption (`assets/zoom.js`)

`workshop.css` caps content images at `max-height: 26rem`, because a 750px screenshot pushed the
first step of a part below the fold. That cap is right for reading the page and wrong for the one
moment the image is doing its job: a participant holding their own screen against it.

Clicking one opens it in a `<dialog>` at up to 78vh, with the image's `alt` shown underneath as a
visible caption. The `alt` is the caption by convention — `workshop-capture` writes it as one —
and until now it reached screen readers and nobody else.

A `<dialog>` rather than a positioned div: the top layer clears the runtime pane without either
knowing the other's z-index, and Escape, the backdrop and the focus trap come from the platform.
Focus is parked on the dialog itself, because `showModal()` otherwise focuses the close button and
Chromium counts that as `:focus-visible` — opening with the mouse drew a keyboard focus ring.

Registered globally with `register_plugin_script`, like `hints.js`: `.challenge-desc` is core's own
container, so the board at `/challenges` renders subject images too, and this plugin's CSS has
always styled them there. The selector is exactly the two containers the CSS caps, so what is
zoomable is what was shrunk. A linked image is left alone — the author meant "go there".

**Images get no `tabindex`, deliberately.** A screen reader already announces the `alt`, which is
all the dialog adds, so the tab stop would buy an assistive-technology user nothing while costing
every keyboard user one stop per image on a page that is mostly images. The trade runs the other
way for a sighted keyboard-only reader; the comment in `workshop.css` marks the line to revisit.

## The runtime cue (`<!-- ws:cue runtime -->`, convention §3.4d)

The page carries **two launchers for one action** — the hero button while it is in view, the
fixed edge tab once it has scrolled away — and `runtime.js` shows exactly one at a time. So a
sentence that says « clique sur le bouton en haut de cette page » is read at a moment when that
button is no longer there, which is how beta testers reached the first step with the runtime
never opened.

An author marks the moment in the content; `cue.py` turns the marker into a 1px anchor and
`runtime.js` watches it with an `IntersectionObserver` trimmed to the middle fifth of the
viewport. Whichever launcher is live pulses, and keeps pulsing until it is pressed — not until
a timer runs out, because the participant this exists for is the one who never noticed the
control. It does nothing when the runtime is already open, and becomes a still ring under
`prefers-reduced-motion`. Nothing waits on `animationend`, which is what lets the version with
no animation end at all; the press clears the class in `activate`, and `updateLauncher` covers a
runtime that came up some other way.

Everything is in `cue.py` (a pure helper, called once at the top of `page.py`'s `_body`),
`assets/runtime.js` (`watchCues`) and the `.ws-cue` rules in `assets/workshop.css`. A subject
with no marker behaves exactly as before.

**A guided tour does not belong here.** The intended split is that the platform owns this cue —
one signal, on a control it owns, whose position it alone knows — and a runtime owns any tour of
its own interface, inside its frame, where the targets are always present. A tour library driven
from this side would be measuring `.ws-runtime-handle` while it still carries `hidden`.

## Switching the plugin off (PLAN.md §39)

`workshop_enabled` — a CTFd config key, unset meaning **on** — is flipped at
`/admin/workshop/plugin`, which is the only workshop page that answers in both states.

Off, every route this plugin registers is a 404 and `/` is CTFd's own front door again, so the
instance behaves as a plain CTFd between two sessions. The set of routes is read off the app at
boot (`toggle.py:guard_routes`: every blueprint whose `import_name` is inside this package), so
a blueprint added later is covered the day it is written.

Three things are deliberately left on: **the Epitech theme**, in full — the shell overrides and
both stylesheets, because turning the workshop off is not a request for CTFd's dark navbar back;
**the `quiz` challenge type**, because taking it out of `CHALLENGE_CLASSES` would break every
quiz already in the database; and **the content**, meaning challenges, solves, the authored Pages
the sync created and every supervisor account. A switch that deleted content would not be a
switch.

`scripts/toggle_check.py <base-url> <admin-pass>` walks both states and puts the instance back.

## The instance's mode (PLAN.md §25)

`workshop_mode` — `instructor_led` (default) or `self_serve` — is a CTFd config key, set at
provisioning and flippable at `/admin/workshop/settings`. It decides one thing: whether a
`checkpoint` step asks for the code an instructor reads out, or offers a button because there
is nobody to ask. `mode.py` is the only reader; `quiz.py` consults it at submit time and
`page.py` at render time, so a flip needs no re-sync and moves no solve. It does not decide who
may come in: both modes are entered with a registration code (PLAN.md §62).

The code is stored on the challenge either way and is never serialized to a client, which is
what makes the flip reversible. `checkpoint.py` carries the two admin-only routes the importer
needs: reading the codes back (`quiz_answers` is excluded from every read schema) and
converting a pre-§25 instance in place — `standard` challenge plus a `Flags` row becomes a
checkpoint challenge, same id, same solves.

## The external door, retired (PLAN.md §42, §64)

`/external/join` was a hidden second door: a code, then an account made from a name, an e-mail
and a password. It is gone, with its admin page and its two settings. The token door admits the
same groups from a registration code and asks for none of the three (§60, §61).

What is left, in `external.py`:

- **The accounts it made keep working.** They have a password, so they sign in at
  `/login?staff=1`.
- **Each still carries its code** in the CTFd custom field **External code**. Nothing writes that
  field any more; it is core's data and stays in `/admin/users` and in an export.
- **The code is still the cohort**: such an account sees its own cohort only (next section), and
  `/admin/workshop/codes` lists these cohorts under the registration codes, with one button to
  ban a cohort or let it back in. The list is drawn only on an instance that has such accounts.

`scripts/audience_check.py <base-url> <admin-pass>` covers the wall and the retirement, and puts
the instance back.

## Several audiences on one instance (PLAN.md §43, §50)

A participant who came through Jump and one who came through another door are in the same
room and not in the same cohort, and neither are two Jump sessions: an instance serves several
events over its life, and last spring's Coding Club is not today's room. **The audience of an account is the
external code it carries, else the Jump session its link was filed under, else the default** (an
admin-made account, a supervisor, a Jump account that has not entered since tickets named a
session). Each sees only its own: two external cohorts do not see each other, two sessions do not
either, which is the same rule rather than a second one. The keys are tagged tuples, since a code
is free text and could otherwise spell a session id.

Staff see everything. An anonymous visitor is given the default audience, because an external
cohort is exactly the group we were asked to keep out of sight, and a session is a room of
minors. **Teams mode is not filtered at all**: a scoreboard
row is then a team, an audience is a property of a user, and a team drawn from both cohorts has
no correct answer.

CTFd has no hook in `get_standings()` and its only notion of a divided scoreboard is *brackets*,
which annotate every row and let the **client** filter — a tab, not a wall. So `audience.py`
builds the wall, in three ways chosen by where CTFd cuts each list into a page:

- **one account**: 404 on another cohort's account and everything hanging off it;
- **a list CTFd pages itself** (`/users`, `/api/v1/users`): its own query is narrowed to the
  reader's audience before it runs (`with_loader_criteria`, from a `do_orm_execute` listener
  active on those two endpoints only), so the page of fifty and its page count are the reader's;
- **a list CTFd caches** (the scoreboard, a step's solver list): `get_standings()` is memoized
  on its arguments, so narrowing its query would store one room's ranking under the instance's
  key. The full list is filtered on the way out, and the score graph's top ten is rebuilt from
  the full standings, since the instance's top ten is not a room's. The solve *count* on a step
  stays the whole instance's: it names nobody.

`/scoreboard` and the score graph are Alpine components reading those APIs, and `/users` is
CTFd's own view on its own query, so no markup moves and no template is overridden.

`scripts/audience_check.py <base-url> <admin-pass>` builds three accounts in three cohorts, gives
each a score, and asks every surface as each of them.

## Two registries the ways in plug into (PLAN.md §59)

Nothing outside a way in's own module knows it exists. A way in registers itself, as it loads, in
one or both of:

- **`cohorts.py`**: a `CohortProvider` says which room each of its accounts is in
  (`memberships()`, read by `audience.py`) and how the supervision pages pick one (`groups()`,
  `rooms()`, `members()`, read by `scope.py`). Registration order is precedence, so the order of
  the `load_*` calls in `__init__.py` matters and is commented there.
- **`doors.py`**: a `Door` is a fragment on the participant side of `/login`, drawn while
  `open()` says so.

Registered today: the retired external door (the cohorts of its accounts only), the registration
codes and the token door (§60, §61), and Jump (both).

To add a way in: write its module, subclass the two classes, call the two `register()` from its
`load_*`, and place that call in `__init__.py` where its precedence belongs.

## Registration codes (PLAN.md §60)

A cohort without Jump. An admin makes a code at `/admin/workshop/codes` and puts it on a slide;
every account made with it is in that code's cohort. **The code is all a participant gives**: the
account gets a generated name (`names.py`, « Renard-Bleu-42 »), a random address under `.invalid`
and no password.

- **A code carries the cohort's facts**: campus, event, date, content, an optional window and an
  optional cap. It is compared forgivingly (case, spaces, dashes, `O`/`0`, `I`/`L`/`1`).
- **The code is the cohort**, through a cohort provider (§59): the wall of `audience.py` and the
  room picker of the supervision pages work with no Jump.
- **Closed, not deleted**: retired, outside its window, or full. Only a code nobody came in on can
  be deleted. Banning a cohort retires its code and is undone from the same page.
- **Two JSON requests**, `POST /api/v1/workshop/door/code` and `.../door/register`. The page that
  sends them is the token door (§61). Every refusal is the same status and body, bar a code that
  is not open yet: that one says the event has not started, and carries `opens` (UTC) for the
  page to write in the reader's own time zone.
- **The throttle counts wrong codes, not addresses**, in the cache. What it does not stop is
  written in PLAN.md §60.4: read it before giving a short word as a code.

Tables: `workshop_registration_code`, `workshop_token_account` (migration `c5d81e4a7f30`).
`scripts/registration_check.py <base-url> <admin-pass>` walks all of it and puts the instance
back; it closes the door for up to a minute while it runs.

## The token door (PLAN.md §61)

The page the registration codes are typed on, and the way back in. It is the first door on the
participant side of `/login` (`tokendoor.py`, `templates/workshop_token_door.html`,
`assets/door.js`), and it is always open.

- **One box** takes the code on the slide and the personal code a participant was given. The
  browser tells them apart (a personal code is 28 characters with a checksum), so a mistyped one
  is refused on the page and sent nowhere. The box writes the dashes itself, every four
  characters.
- **The personal code is the account**: 128 random bits made in the browser, shown once as seven
  groups of four with a file to download, a copy button and a QR code. The account is made only
  when the participant has ticked « J'ai noté mon code ». A lost code is a lost account.
- **The file is a PDF**, `mon-code-atelier.pdf`, written by `assets/pdf.js` in the browser: a text
  file opens in an editor, where one key changes the code. It is plain ASCII with nothing
  compressed, so the door reads the code back out of it as text. Keep it that way: compress the
  page, and no file handed out opens the door any more.
- **The page holds a card**, a bank card's size, in the platform's look: `drawCard` in `door.js`.
  Its typefaces and the wordmark come from `assets/pdf-brand.json`, which is generated: after
  changing a font or the logo, run `python scripts/build_pdf_brand.py` (needs `fonttools` and
  `brotli`) and commit the result. Without that file the PDF falls back to the standard fonts.
- **The server never sees it.** The browser derives a sign-in value with HKDF and sends that;
  the server stores its SHA-256. A second derived value is an encryption key that stays in the
  browser's IndexedDB, non-extractable, for saved code later. The salt and the two info strings
  in `door.js` are frozen: changing one locks every existing account out.
- **Coming back**: type, paste, choose or drop the file (the PDF, or a text file of before it),
  or scan the QR code.
  `POST /api/v1/workshop/door/signin`, not throttled.
- **No settings for these accounts**: no navbar entry, `/settings` redirects, and a write to
  `/api/v1/users/me` is refused, also with the plugin switched off.
- **Needs https (or localhost) and JavaScript.** Without a secure context the page says so and
  disables the box.

No table of its own. `scripts/token_door_check.py <base-url> <admin-pass>` holds the server's
half and `scripts/token_door_browser_check.js <base-url> <admin-pass>` the page's (Playwright).

## The certificate (PLAN.md §63)

A token account downloads what it did here as a PDF, "Télécharger mon certificat", from the band
of the workshop pages (`templates/workshop_certificate.html`, in both `workshop_index.html` and
`workshop_page.html`). Nothing is sent anywhere and nothing is stored.

- **`certificate.py` decides and signs**, `assets/certificate.js` draws with `assets/pdf.js`.
  `GET /api/v1/workshop/certificate` answers the sentences, translated, and the signed text: a
  compact JWS, Ed25519, whose payload is the account's name, the instance's address, the XP and
  the steps done out of the total, and the campus, date and event of its registration code.
- **The key is a deploy secret**, `WORKSHOP_CERTIFICATE_KEY` (a 32-byte seed in base64, or a PEM
  private key), the same on every instance. Make one with
  `python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"`. Without it an
  instance makes its own and keeps it in the `workshop_certificate_key` config.
- **The public key is at `/api/v1/workshop/certificate/key`**, a JWK set, open to anybody.
- **Only for an account made at the token door**, and only once a step is done: 403 and 409
  otherwise, and the button is not drawn, or is hidden.
- **The signed text is in the file twice**: in the QR code and as a line of undrawn text, found
  with `eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{86}`.
- **The light typeface** is `assets/pdf-brand-light.json`, built with the rest by
  `scripts/build_pdf_brand.py`.

No table. `scripts/certificate_check.js <base-url> <admin-pass>` holds it (Playwright).

## The front door (PLAN.md §62)

An anonymous visitor to `/` is sent to `/login`, whose participant side is the token door
(`landing.py`, `anonymous_to_door`). The mode, core's registration setting and Jump's keys
change nothing about it.

- **No page sends a visitor to another site.** A configured Jump key draws a button under the
  token door and that is all: the redirect of §53 is gone, with `Door.takeover`.
- **Self-serve takes a code too**, a private one made on `/admin/workshop/codes`. An instance
  with no code admits nobody.
- **Core's own `/register` is closed by configuration**: `tools/provision.py setup` writes
  `registration_visibility: private` and empties core's `registration_code` in both modes, and
  the k8s overlay presets the same. The plugin does not override the setting.
- **Staff are where they were**: `/login?staff=1`, a bare `/admin` sent to it, the supervisor
  join.

`scripts/front_door_check.py <base-url> <admin-pass>` switches the settings both ways, so it
belongs on a scratch instance. `scripts/standalone_acceptance_check.js <base-url> <admin-pass>
[runtime-id|none]` (Playwright) changes no setting: it walks an instance about to be used, from
the door to a step, the runtime, the way back in and what an export holds, and removes the two
codes and two accounts it made.

## Supervisors (PLAN.md §32)

The people running the room, without the admin panel. CTFd has two user types and no group
system, so the tier is plugin-side: a supervisor is an ordinary `user` row, `hidden=True`, that
the `workshop_staff` table names (migration `7d2f5a9c41be`). `staff.py` carries the predicate,
the `staff_only` decorator (`admins_only` widened to supervisors), the way in and the management
POST. `is_admin()` stays false for them, so everything under `/admin` keeps refusing them by
default; what opens is exactly the plugin's read-only pages — `stats.py` and `submissions.py`
(new, in place of core's Statistics and Submissions), `answers.py` and `feedback.py`.

**All four read one population** (`scope.py`, PLAN.md §50): participants only, and, once a
supervisor picks a campus and optionally one of its sessions from the picker at the top of each
page, only the accounts filed under it. The choice is kept in the Flask session, so every page and
CSV export follows it.

The way in is `/supervisor/join` with the `workshop_supervisor_code` config, empty by default
and empty means closed. It deliberately ignores `registration_visibility`, like the Jump route
does. Accounts are managed on `/admin/workshop/settings`: code, create, grant, revoke, delete.
`scripts/supervisor_check.py` is the feature's test suite.

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

## Starting the subject again, as an admin (PLAN.md §47)

An admin tests a subject by doing it. The account menu carries **Reset my progress** for admins:
`POST /api/v1/workshop/progress/reset` deletes the signed-in admin's own solves, attempts, hint
unlocks, awards and ratings, then clears the caches the page reads solves through. The account is
the session's and there is no user id to pass, so it cannot be aimed at a participant. The work in
progress saved for the runtime is erased too, in both places it lives: the server row, and the
browser's localStorage, which `reset.js` clears before the page reloads. `reset.py`, `assets/reset.js`, `scripts/reset_check.py`.

## Start-up options for a runtime (PLAN.md §48)

A subject can set options its runtime reads when it starts, in `runtime.params`. For tic80,
`repl: lua | py | js | off` chooses the REPL panel or removes it. `runtime_options.py` holds the
allowlist and writes the option into the frame's address (`?repl=off`); `runtime.py` and
`page.py` apply it, so the pane and the popped-out window open the same address. A value the
runtime does not take is dropped and logged. `scripts/runtime_options_check.py` tests the module
with no instance. See docs/RUNTIME_PROTOCOL.md §4b.

## An admin validates a code step without the code (PLAN.md §49)

On a step validated by the instructor's code, an admin sees one more button beside « Submit »:
**Skip validation code**. The rest of the step is what a participant sees. The button submits the
step with `skip_code: true`; `quiz.py` accepts that for an admin and ignores it for anybody else,
so the template's `is_admin()` is a convenience and not the guard. The submission is recorded as
`(admin: validation code skipped)`. Only `checkpoint` steps: a quiz, a flag or a token is an
answer, not a permission, and is not skipped. `scripts/skip_code_check.py`.

## The way in from Jump (PLAN.md §31)

Jump is one door into a workshop instance, and not the front one any more (PLAN.md §62): on the
login page it is a button under the token door. `jump.py` verifies a short HMAC-signed ticket at
`GET /jump/enter?t=…`, creates the account on first arrival with **no password** (so CTFd's own
local sign-in refuses it), and opens the session. `jumpqueue.py` overrides `QuizChallenge.solve()`
to queue the solve in `workshop_jump_event` and a background drainer posts a signed progress
callback, so a slow or absent Jump never slows a participant down.

Two config keys, both provisioned by `tools/provision.py`: `workshop_jump_keys`
(`{kid: {origin, server_origin?, secret, label}}`) and `workshop_jump_instance` (this instance's
slug). Empty means every ticket is refused. `/admin/workshop/jump` edits both and shows the outbox.

**`origin` is where a browser goes; `server_origin` is where this server posts** (PLAN.md §52).
The login page and the declaration calls use `origin`, Jump's public address. The progress
callback and the erasure question go to `server_origin` when it is set, which on the cluster is
Jump's Service: the public edge challenges a server as a bot. A redirect is never followed, so a
wrong address shows on the outbox as `HTTP 301 to ...` instead of passing for delivered.

**The slug names the host; the content names the activity** (PLAN.md §51). The slug stays the
same when an instance moves on to another content. What Jump files a talent's participation and
XP under is the content the instance serves, `workshop_content`, which the sync writes: the
`workshop.slug` of a composed workshop, the `project.slug` of a lone subject. Every progress
callback names it, the ticket names the content the talent meant to enter, and a ticket naming
another is refused. `/admin/workshop/jump` shows it, since Jump's activity carries exactly that
slug. Re-sync an instance once after upgrading, so it has a content recorded.

**A step's value is the XP it earns in Jump** (PLAN.md §57). Every callback carries `points`, the
value of every visible step the talent solved, optional ones included and hint costs not taken
off, and Jump grants exactly that. So the step reads "25 XP" and its solve floats "+25 XP". The
scoreboard keeps "points": it sums the whole instance and takes hint costs off, so it is not a
figure Jump shows.

**Jump has to declare both the host and the content** before it sends anyone. The same page
prints the two calls to Jump's admin API that do it, one pair per key, built from the slug, the
synced content and the address the page was opened at, so nobody retypes them (DEPLOY.md §6d).
The address is filled in by the browser: behind the cluster's tunnel the server sees another host,
over http.

**A label is owned by the accounts it namespaces, not by the key row that declares it.** Each
link row records it (`jump_label`, revision 2), and both the settings page and `resolve_account`
refuse a second key id taking a label that already has accounts behind it — the configuration is
rewritten wholesale by provisioning, so a rule enforced against it stops holding the first time
somebody renames a key id.

**The ticket names the session** (PLAN.md §50): the Jump event the talent's participation was
pinned to, its campus, and a label for each. `file_session` records it in `workshop_jump_session`
(revision `8b3e6f1d9a24`) and points the account's link at it, following it when a later ticket
names another (§51.4). The four claims are optional as a set, so a ticket from an older Jump still
opens.

**An erased talent's account is deleted.** Once an hour, and at start-up, the drainer asks each
configured Jump which of its linked talents it has erased (`POST /api/workshops/erasures`, signed
like a callback) and deletes those accounts through `accounts.py`. Only on proof: a talent Jump does
not know is kept. `POST /api/v1/workshop/jump/erasures` (admins) runs a pass now, and answers 502
when a Jump did not answer.

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
    change to authentication does not have to be mirrored. Its participant side is
    the **doors** (`doors.py`, PLAN.md §59): each way in registers a fragment, and
    `login_is_staff()` (`doors.py`) picks between that side and the staff form. The MLC branch
    stays inside the staff door, byte for byte what
    core renders, so an upstream change to it is a straight re-copy. The page also leans on core
    re-rendering `login.html` itself after a failed POST (that is how the error lands on the staff
    door) and on core's footer being `body > .footer`, which `epitech-theme.css` hides on the pages
    built on `workshop_login_layout.html` — this one and the supervisor's join page — whose foot
    carries the credit instead.
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
  openpgp.js, canvas-confetti, lolight and qrcode-generator at pinned versions with their sha256
  verified. Without it the sync page cannot decrypt answers, the workshop page draws no confetti,
  code injected after a solve stays monochrome, and the token door's handout has no QR code.
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
- The four staff pages (`stats.py`, `answers.py`, `submissions.py`, `feedback.py`) touch core
  only through `admins_only` / `is_admin`, `register_admin_plugin_menu_bar`, and
  `{% raw %}{% extends base_template %}{% endraw %}` with its `content` block, where
  `base_template` is `admin/base.html` for an admin. They are plain Blueprints — nothing about
  them is challenge-type machinery, so they survive a CTFd upgrade as long as the admin theme
  keeps that block.
- **`templates/workshop_staff_base.html` is a copy of the admin theme's `admin/base.html`** with
  the nav replaced (PLAN.md §32): what a supervisor's page extends, because core's nav is inline
  in that file rather than in a block. It restates the head — the four admin stylesheets, the
  `init` object, `main.js` and the registered admin scripts and stylesheets. After a CTFd upgrade
  diff it against `admin/base.html` and carry over whatever moved.
- **`staff.py` is the plugin's second piece of authentication code**, after the Jump handoff. It
  creates accounts the way core's register does (`Users(...)`, `login_user`). Deleting one, for a
  supervisor and for an erased Jump talent alike, is `accounts.py`, which does it the way core's
  `DELETE /api/v1/users/<id>` does: the list of tables it clears first is copied from
  `api/v1/users.py` and has to follow it.
- **`codes.py` is the third piece of authentication code** (PLAN.md §60). It makes a `Users` row
  with no password and signs it in with `login_user` (through `doors.sign_in`). It leans on three
  things in core: a JSON write is refused without the session's `CSRF-Token`, an anonymous session
  has a nonce, and core's own login form refuses an account whose password is NULL. The last one
  is what keeps a token account closed to anybody without the token: check it after an upgrade
  (`scripts/registration_check.py` does).
- **`tokendoor.py` names two core endpoints** (PLAN.md §61.6): `views.settings` and
  `api.users_user_private`, the settings page and the request it saves with. A token account is
  kept off both by a `before_request` hook. If upstream renames either, the hook stops matching
  and nothing fails: a participant could then type a name into an account that is meant to hold
  none. `scripts/token_door_check.py` catches it. The door's template also reads `Session.nonce`
  and the `authed` jinja global, and `door.js` relies on core answering a JSON write that has a
  stale nonce with an HTML page (it reloads on anything that is not JSON).
- **`audience.py` names core endpoints by their Flask endpoint name** (`api.users_user_list`,
  `api.challenges_challenge_solves`, …). flask-restx derives those from the namespace and the
  resource class, so a renamed class upstream silently stops being filtered: after a CTFd upgrade,
  run `scripts/audience_check.py` and `scripts/jump_check.py`. Two more things in it follow core:
  - `_top` is a copy of the body of `get_scoreboard_detail` (`utils/scoreboard/__init__.py`), fed
    a list of accounts instead of a count. If upstream changes the shape of a row there, the
    score graph a room reads changes shape with it only once this copy does.
  - `/users` and `/api/v1/users` are narrowed in their query, which relies on both views reading
    `Users` through the ORM (`Users.query...paginate`). A view rewritten on raw SQL would no
    longer be narrowed, and nothing would fail.
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
- **Revision `3a6016a2732e` writes a core setting.** It inserts `default_locale = fr` into
  core's `config` table when no row exists, then calls `CTFd.cache.clear_config()` (PLAN.md §42).
  After a CTFd upgrade, check three things: `get_locale()` in `utils/user/__init__.py` still
  reads `default_locale`, the table is still `config(key, value)`, and a `PRESET_CONFIGS` entry
  still outranks the database. That last one is why the key must never go into a preset.
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
