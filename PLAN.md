# CTFd as a Code Workshop Platform — Research & Plan

Target: use CTFd 3.8.5 (vendored at `CTFd/`) as the **backend of record** (accounts, progression,
scoring, persistence) while replacing the frontend with a purpose-built code-workshop UI.

---

## 1. What CTFd already gives you (do not rebuild these)

| Need | CTFd mechanism | Verdict |
|---|---|---|
| Accounts, sessions, teams | `/api/v1/users`, `/api/v1/teams`, auth views | Free |
| **Progressive unlocking** | `Challenges.requirements` | **Free — this is the big one** |
| Scoring / scoreboard | `Solves`, `Awards`, `/api/v1/scoreboard` | Free |
| Hints (cost points to reveal) | `/api/v1/hints`, `/api/v1/unlocks` | Free |
| Attempt rate limiting & max attempts | built into `/api/v1/challenges/attempt` | Free |
| Grouping exercises into tracks | `Topics`, `Tags` | Free |
| Reference solutions post-solve | `Solutions` (3.8 feature) | Free |
| Admin CRUD + import/export | admin theme, `export.py` / `import.py` | Free |

### Progressive unlocking, concretely

`Challenges.requirements` is a JSON column:

```json
{ "prerequisites": [1, 2], "anonymize": "preview" }
```

`CTFd/api/v1/challenges.py:196-210` filters locked challenges out of the list response, and
`:711-724` blocks solve attempts on locked challenges. `anonymize` controls whether a locked
challenge is hidden entirely or shown as a greyed-out teaser.

**This means "small challenges unlocking progressively" needs zero backend code.** It is a data
modelling exercise: create challenges, set prerequisites, done.

---

## 2. What CTFd does NOT give you (this is the actual project)

1. **CTFd executes no code.** Nothing anywhere in the codebase runs untrusted user input. The
   sandboxed runner is 100% yours to build, and it is the hard part.
2. **No CORS, anywhere.** Grepping the whole codebase for `cors`/`Access-Control` returns *zero*
   hits. A browser SPA on a different origin **cannot** call this API. This dictates the deployment
   topology (see §3).
3. **`attempt()` is synchronous** inside a Flask request worker. Running a test suite there blocks
   a worker for the duration. Needs an async job + polling design.
4. **Structured results have no home.** `ChallengeResponse` is `dataclass(status: str, message: str)`
   — `message` is a flat string. Per-test pass/fail, stderr, and diffs do not fit. They need a
   separate endpoint.
5. **No "pending review" state.** Statuses are only `correct` / `incorrect` / `partial` /
   `ratelimited`. Human-graded exercises need a workaround (see §5, Family B).

---

## 3. Architecture

### 3.1 The CORS constraint drives everything

Three ways to put a custom frontend on CTFd:

| Option | CORS? | Verdict |
|---|---|---|
| A. Custom CTFd theme (Jinja + Vite inside CTFd) | N/A same-origin | Works, but you inherit CTFd's routing and template model — the thing you're trying to escape |
| B. **Standalone SPA, reverse-proxied to one origin** | None needed | **Recommended** |
| C. Standalone SPA on its own origin + `flask-cors` plugin | Must add it | Most friction: `credentials: include`, cookie `SameSite`, CSRF — avoid |

**Recommendation: Option B.** One nginx, one origin:

```
https://workshop.local/          → SPA static build (Vite: React/Svelte + Monaco editor)
https://workshop.local/api/      → CTFd (gunicorn)
https://workshop.local/themes/   → CTFd (admin theme still works for you as instructor)
https://workshop.local/runner/   → code execution service
```

Same origin ⇒ session cookie flows automatically, no CORS, no token distribution.

### 3.2 Auth: use the session cookie, not API tokens

Two paths exist:

- **`Authorization: Token ctfd_<hex>`** — bypasses CSRF entirely
  (`CTFd/utils/initialization/__init__.py:388`) and requires `Content-Type: application/json`.
  Note the parser does `token.split(" ", 1)` and *ignores the scheme word*. But tokens are
  per-user and self-generated in the settings UI — unusable for workshop attendees.
- **Session cookie** — what you want, given same-origin. Caveat: non-GET requests need the CSRF
  nonce, which CTFd only injects server-side into Jinja (`themes/core/templates/base.html:22`,
  `'csrfNonce': "{{ Session.nonce }}"`). A standalone SPA can't read it.

**Fix:** a ~10-line plugin endpoint `GET /api/v1/workshop/session` returning
`{user, team, csrf_nonce}` for the current session. The SPA calls it on boot and sends the nonce
as `CSRF-Token` on writes.

### 3.3 Custom challenge type

Subclass `BaseChallenge` and register in `CHALLENGE_CLASSES`. Follow
`CTFd/plugins/dynamic_challenges/__init__.py` as the structural template — model subclass via
polymorphic identity:

```python
class CodeChallenge(Challenges):
    __mapper_args__ = {"polymorphic_identity": "code"}
    id = db.Column(db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"),
                   primary_key=True)
    language      = db.Column(db.String(32))
    starter_code  = db.Column(db.Text)
    test_suite    = db.Column(db.Text)   # never sent to the client
    time_limit_ms = db.Column(db.Integer, default=5000)
```

### 3.4 The submission flow (secure by construction)

Do **not** let the client assert that its code passed. Keep results server-side and submit a
job handle:

```
1. SPA   POST /api/v1/workshop/run   {challenge_id, code}   → {job_id}   (202)
2. Runner executes in sandbox, writes structured result to DB, keyed (job_id, user_id, challenge_id)
3. SPA   GET  /api/v1/workshop/jobs/<job_id>  → {state, tests:[{name,passed,stderr,diff}], ...}
   (poll; render per-test results in the UI)
4. SPA   POST /api/v1/challenges/attempt  {challenge_id, submission: job_id}
5. CodeChallenge.attempt() looks the job up server-side, verifies it belongs to this user AND
   this challenge AND passed → ChallengeResponse("correct"|"partial"|"incorrect", summary)
```

Step 5 is why this is safe: a forged `job_id` belongs to someone else or doesn't exist. And by
routing the final step through CTFd's **standard** attempt endpoint, you inherit solves,
scoreboard, rate limiting, and **prerequisite unlocking** for free — untouched.

Use `status="partial"` for "4/6 tests passing" — it's already supported end to end
(`api/v1/challenges.py:897-916`).

### 3.5 Sandboxing

Do not hand-roll this. Self-host an execution API:

- **Piston** (`engineer-man/piston`) — 60+ languages, resource limits, purpose-built. Best fit.
- **Judge0** — heavier, more features, also solid.
- Roll-your-own Docker-per-submission only if you need a language neither supports.

Non-negotiables: no network in the sandbox, hard wall-clock + memory + PID caps, read-only FS,
non-root, output truncation.

---

## 4. Recommended repo layout

```
CTFd_coding_platform/
├── CTFd/                     # vendored upstream — keep clean, never edit in place
├── plugins/
│   └── code_challenges/      # bind-mounted into CTFd/plugins/ at runtime
│       ├── __init__.py       # CodeChallenge model + BaseChallenge subclass
│       ├── api.py            # /workshop/session, /workshop/run, /workshop/jobs
│       └── migrations/
├── frontend/                 # Vite SPA + Monaco
├── runner/                   # Piston wrapper / job queue
├── content/                  # exercises as version-controlled YAML + tests
│   └── track-01-basics/
└── docker-compose.yml        # nginx, ctfd, mysql, redis, runner
```

Keeping `CTFd/` pristine and bind-mounting the plugin means upstream updates stay a `git pull`.

**Author exercises as files, not through the admin UI.** A `content/` tree of YAML + test files,
plus a sync script that pushes to the CTFd API, gives you review, diffs, and reproducible resets
between workshop runs.

---

## 5. The two exercise families need different mechanics

### Family A — auto-gradable ("write a function that…")

Fully covered by §3.4. Prerequisites chain them into a track. This is the bulk of the workshop
and it works well.

### Family B — creative / open-ended ("code a shooter in TIC-80")

**There is no pass/fail here, and pretending otherwise will produce a bad workshop.** A shooter
game has no unit tests. Options, best first:

1. **Instructor review + Awards API.** Attendee submits their cart; you review and grant points via
   `/api/v1/awards`. CTFd has no pending-review state, so track submission status in your own
   plugin table and surface a review queue in the SPA. Most honest mechanic.
2. **Embed TIC-80's WASM build in the SPA** so attendees run their cart in-page, plus a
   self-attested checklist that reveals the flag. Great experience, weak rigor — fine for a
   workshop.
3. **Static analysis for partial credit** — does the cart define `TIC()`, call `btn()`, call
   `spr()`? Awards `partial` automatically, with a human for the rest.

Suggested: **2 + 1** — embedded player for the feedback loop, instructor Awards for the score.

---

## 6. Phased plan

**Phase 0 — Stand it up (½ day).** `docker-compose up` upstream CTFd unmodified, run setup, create
3 challenges by hand, wire `requirements` between them, and confirm via `curl` that locked
challenges disappear from `/api/v1/challenges`. *Validates the whole progression premise before you
write any code.*

> **DONE 2026-08-03.** Root `docker-compose.yml` runs the submodule read-only (`:8080`, no
> nginx yet); `scripts/phase0_validate.py` automates the whole check against a fresh instance —
> 18 assertions pass. Content: first PyPong exercises, not invented. Verified along the way:
> a locked challenge is hidden (or `'???'` with `anonymize`), attempting it is a hard 403
> (`api/v1/challenges.py:728`), solving unlocks and de-anonymizes the next one immediately.
> Reset: `docker compose down && docker run --rm -v "$PWD/.data:/d" alpine sh -c 'rm -rf /d/*'
> && docker compose up -d`.

**Phase 1 — Plugin skeleton (1 day).** `code_challenges` plugin: `CodeChallenge` model, migration,
`attempt()` stubbed to accept a hardcoded value, admin create/update templates. Confirm it appears
in the admin challenge-type dropdown.

> **DONE 2026-08-03 — rescoped to the `quiz` type** (the CodeChallenge/runner path is deferred,
> §9; quiz was explicitly required, §12). `plugins/workshop/` bind-mounted over the image's
> plugins dir — the `./CTFd` checkout mount was dropped since the image already contains the
> pristine submodule and a read-only parent blocks the overlay mountpoint.
> `scripts/phase1_validate.py`: 19 checks — type registered, all four kinds graded server-side
> (case/order/separator tolerance), answers never in any client payload, score credited.
> Admin create form verified rendering. Details: `plugins/workshop/README.md`.

**Phase 2 — Runner (2–3 days).** Piston in compose. `/workshop/run` + `/workshop/jobs/<id>`.
Structured per-test results. Wire `attempt()` to job lookup. **The riskiest phase — schedule it
early, not last.**

> **Rescoped & DONE 2026-08-03 — content pipeline instead of runner** (runner deferred
> indefinitely, §9). `tools/ws_parser.py` — shared parser/linter for convention 2.0
> (`subject.yaml`, `ws:` markers, resume regions, hint `<details>`, quiz bodies; exercises carry
> their preceding prose as context). `tools/sync_subject.py` — idempotent import into CTFd keyed
> by admin-only Topics (`ws:<subject>:<slug>`): entrypoint → index page, exercises → chained
> standard challenges (validation codes generated into a gitignored instructor sheet), quizzes →
> `quiz` challenges gated by their host exercise. `content/pypong/` — pypong_new converted to
> convention 2.0 (verbatim text, real quiz answers, images via raw.githubusercontent).
> `scripts/phase2_validate.py`: 28 checks including idempotent re-sync (0 re-created, solves
> preserved), in-place content edits, and **board ordering** — the parser assigns a subject-wide
> source-reading `order` (exercise then its hosted quizzes), synced to CTFd's native
> `Challenges.position` so the challenge board follows reading order instead of alphabetical.

**Phase 3 — SPA (3–5 days).** ~~Vite + Monaco. Session bootstrap, track/challenge navigation,
editor, run button, test-result panel, submit-on-pass.~~

> **ON HOLD 2026-08-04 (user).** The CTFd frontend may well be enough: §13's single-page
> workshop view already solves the "not ideal frontend" problem, and the editors/runtimes the
> SPA would have rebuilt **already exist as standalone web apps** (tic80-web-editor,
> pacman-ghost-ai, v86-runner + shell-1/shell-rpg, a p5.js editor to come). The remaining
> question is not "build an SPA" but "embed a runtime into the workshop page" — see §14.

**Phase 4 — Content pipeline (1–2 days).** `content/` YAML + sync script. Author the real
exercises. Expect this to take longer than you think — writing good progressive exercises with
solid test suites is the real intellectual work.

**Phase 5 — TIC-80 track (2–3 days).** ~~Embedded player + review queue + Awards.~~

> **ON HOLD 2026-08-04 (user).** Superseded in part by §14: the TIC-80 surface is the existing
> tic80-web-editor embedded through the runtime protocol, not a player written here. The review
> queue (§11) stays a separate, still-wanted piece of work.

**Phase 6 — Workshop hardening (1 day).** Reset script between cohorts, backups, load-test the
runner at your expected concurrency, rehearse a full run.

---

## 7. Open decisions

1. **Languages?** Drives runner config and test-harness format. One language is dramatically
   simpler than four.
2. **Teams or individuals?** CTFd is configured one way or the other at setup and it is painful to
   change later.
3. **Expected concurrency?** 15 attendees and 150 are different runner architectures.
4. **Is the scoreboard desirable?** Competition motivates some workshops and poisons others. CTFd
   can hide it — decide deliberately.
5. **Live workshop or self-paced?** Affects whether unlocking is prerequisite-driven or
   time-gated.
6. **Does an attendee's code persist between sessions?** Not a CTFd concept — needs a plugin table
   if you want it. (Strongly recommended; losing work mid-workshop is demoralizing.)

---

## 8. Biggest risks

| Risk | Mitigation |
|---|---|
| **Sandbox escape / resource exhaustion** — you are running untrusted code | Use Piston/Judge0, no network, hard limits. Never `exec()` in the Flask process. |
| **Runner blocks Flask workers** | Async job + poll (§3.4). Never execute inside `attempt()`. |
| **CORS discovered late** | Settled now: same-origin reverse proxy. |
| **Upstream CTFd drift** | Plugin bind-mount, zero edits to `CTFd/`. |
| **Open-ended exercises don't fit the model** | Accept it — Awards + human review, not fake tests. |
| **Content authoring underestimated** | Start writing exercises during Phase 2, not Phase 4. |

---

## 9. Révision 2026-08-03 — generalization beyond code workshops

Source: the real workshop catalog (Notion database `sujets-coding-club`,
`notion.so/epitech-academy/28521bfd6dd7806e9736c8dbd7255384`). 21 workshops across TIC-80/Lua,
Python (CLI + datascience/jupyter), p5js/JS, HTML/web, shell/Linux, Arduino/IOT, and CTF/cybersec.
Durations 1h ("Immersion") to a full day. Some already run on CTFd (`lab.epitech.academy/ctfd/spring`).

The Notion pages are fetchable without auth via the public API
(`POST <site>/api/v3/loadCachedPageChunkV2` / `queryCollection`) — a content sync script can pull
directly from Notion, so authors keep writing there.

### What the catalog changes

1. **Content structure is consistent and maps onto CTFd.** Self-serve workshops (e.g. "Devine le
   Nombre") follow: Lore → Prérequis → ordered Étapes, each Tutoriel + Mise en pratique (objective
   + expected output). This is exactly chapters → challenges.
2. **Validation must be pluggable per challenge**, not tests-only:
   - `flag` — CTF workshops, native CTFd, zero code
   - `checkpoint` — guided builds: student compares output with expected, self-validates (or enters
     an instructor code). Today's de-facto mechanic for everything non-CTF.
   - `tests` — auto-graded, needs a runner; only where suites actually exist (currently: nowhere)
   - `review` — creative work, Awards + plugin review queue
3. **The workspace panel is per-workshop config**: TIC-80 WASM, Python console, p5js iframe,
   or `external`/`none` (online-python.com, physical Arduino — platform only tracks progression).
4. **The server-side runner drops to lowest priority.** TIC-80 WASM, p5js, and Pyodide cover most
   domains in-browser; `checkpoint` covers the rest. Piston/Judge0 only matters if/when real
   auto-grading is wanted.
5. **Mode is per-workshop admin config**: `self_serve` (full prose consignes) vs `instructor_led`
   (bullet reminders). Every challenge carries both consigne variants; the frontend switches on a
   plugin config value read at session bootstrap.

### Revised priorities

1. Content model + Notion sync (chapters/challenges, two consigne variants, validation kind,
   workspace kind) — the platform's core value
2. Frontend workshop shell (the early mockup in `frontend/` demonstrates this: workshop-generic layout,
   mode toggle, checkpoint + tests validation, TIC-80 + Python workspaces)
3. CTFd plugin: session endpoint, workshop config (mode), checkpoint validation mapped to attempts
4. Embedded runtimes (TIC-80 WASM exists in `../tic80-web-editor`; Pyodide for Python)
5. Runner (deferred indefinitely — only for real auto-grading)

---

## 10. Deployment model — one CTFd instance per session (CLAUDE.md, 2026-08-03)

Terminology: **subject** (one content repo) < **workshop** (starter subject + advanced subjects)
< **session** (a workshop, at a place and time, with identified people). See
`docs/CONTENT_CONVENTION.md` §3.0/§4.1.

- **One CTFd instance per workshop session.** Total audience can reach ~2000 users; a single
  shared instance for all workshops does not scale and mixes unrelated cohorts. Instances are
  cheap: compose stack (nginx + ctfd + db + redis) + content sync at provisioning.
- **Session-free instances** are the self-serve variant: permanently up, anyone anytime, may
  follow a branch head via webhook instead of a pinned ref.
- **A workshop repo is almost self-sufficient to deploy an instance.**
  `provision(repo_url, ref)` is the whole interface: the repo's `workshop.yaml` carries the
  subject composition (pinned refs) and optional CTFd setup values (`instance:` block —
  user_mode, scoreboard). A single subject repo is directly deployable as an implicit
  one-subject workshop. Only mode (manual for now), secrets (`flag_env`), and attendees stay
  outside the repo.
- **Provisioning flow:** `provision(repo_url, ref)` → compose up fresh instance → CTFd setup
  from `instance:` values → sync subjects at pinned tags/commit ids → import attendee
  accounts → set mode (instructor-led / self-serve, manual). Teardown or archive after the
  session; scoreboard/awards export before teardown if wanted.
- **Starter gating across subjects:** the starter subject's final exercise(s) become CTFd
  prerequisites of every advanced subject's entry challenges — native `requirements`, no
  custom unlock code. Ordered advanced subjects chain; unordered ones fan out from the starter.

---

## 11. Instructor-led review queue (2026-08-03)

In instructor-led mode, "mark as done" is a **submission for review**, not a self-validation.
Flow: participant submits → pending entry in the instructor's queue → instructor approves or
rejects. **An unreviewed submission never blocks progression unless the exercise says
`review: blocking`** (convention §3.5).

CTFd mapping (it has no pending-review state, §2.5):

- Plugin table `submissions(id, user_id, challenge_id, status: pending|approved|rejected,
  payload, auto_result, submitted_at, reviewed_by, comment)` — the queue is plugin-owned.
- **Non-blocking (default):** the CTFd solve is recorded *at submission time* — native
  prerequisites unlock immediately, points are provisional. On approval the solve stands and
  observables are checked off. On rejection the solve is deleted: points retracted, exercise
  back to "à refaire". Accepted trade-off: deleting the solve can re-lock descendants, but
  in-room review latency is minutes, and the participant should redo the exercise anyway.
- **`review: blocking`:** no solve until approval; the participant waits. Reserved for gates
  (e.g. the last exercise of a starter chapter) where building on a wrong foundation wastes
  the participant's time.
- Auto-checkable validations (tests, flag, quiz) still auto-check in instructor-led mode; the
  result is attached to the submission (`auto_result`) so the instructor reviews outcomes.
- The review UI shows the exercise's `obs` entries as checkboxes — grading and competency
  tracking in one gesture. This supersedes the Awards-based workaround from §5 Family B for
  standard exercises; Awards remain for ad-hoc bonuses.

## 12. QUIZ submission type on the CTFd backend (2026-08-03)

Quizzes (convention §3.7, parsed by `quiz_lib.py`) become a first-class CTFd challenge type:

- `QuizChallenge(Challenges)` — polymorphic identity `quiz`, following the
  `dynamic_challenges` template. Columns: `quiz_type` (single|multiple|match|freeform),
  `spec` (JSON: question, items — what the client may see), `answers` (JSON, **never
  serialized to the client**; excluded from the read schema).
- One quiz = one CTFd challenge, linked to its parent exercise by `requires` at import, so a
  quiz can gate or simply reward. Points from the `ws:` marker (or default).
- Submission goes through the **standard** `/api/v1/challenges/attempt` endpoint with a JSON
  `submission` (selected letters, match pairs, or free text) — solves, scoreboard, rate
  limiting inherited untouched.
- `attempt()` grades single/multiple/match by comparison; **`freeform` is graded by regular
  expression** — the answer entry in `quiz_answers.yaml` is a regex (or list of regexes, any
  match wins) applied to the trimmed free text. CTFd already ships regex flag matching
  (`CTFd/plugins/flags`, type `regex`) — reuse its compare logic, including the
  case-insensitive option. The linter compiles every regex at CI time so a broken pattern
  fails the build, not the workshop.
- **Answers at rest:** correct answers never appear in the markdown (upstream rule). They live
  in `quiz_answers.yaml` at the subject repo root, imported into the `answers` column at sync.
  Public repo caveat: plaintext answers are lookup-able; where that matters, the file is
  encrypted (age/sops) with a deploy-time key — same trust model as `flag_env`. Author's
  choice per subject.

## 13. Participant UI: one page per workshop, not one modal per challenge (2026-08-04)

Decided with the user. CTFd's board (a card grid opening a modal per challenge) is a CTF
interface: it optimizes for *picking* an unrelated challenge out of many. A workshop is the
opposite — an ordered document you work through — and the modal actively hurts there: it hides
the surrounding context, has no place to show progress, loses the reading position, and is
awkward on a small screen. It is the classic "modal for primary content" antipattern.

**Replacement:** `GET /workshop` (plugin blueprint, `plugins/workshop/page.py`), linked in the
navbar via `register_user_page_menu_bar`. The whole subject is one scrollable document:

```
<workshop title>            progress bar, "n / total steps completed"
[ sticky stepper: Intro › part › part › part … ]     each chip: icon + state + n/total
## Intro                    the entrypoint document (index page), folded once work has begun
## <part>                   = challenge category = chapter heading of the subject
   [ stepper: step › step › step ]                   only when the part has more than one
   > <step>  accordion: statement + hints + answer form + inline feedback
```

Design rules that came out of it:

- **It is a stepper, not a breadcrumb.** Breadcrumbs mean containment (`Home > Cat > Item`);
  these are *siblings in sequence*. Same chevrons, different promise — so it is styled and
  labelled as progress, and the nested one sits inside its part instead of being a second
  sticky bar.
- **Never state by colour alone** (~8% of men are colour-blind, and projectors wash colour
  out): every chip and step carries an icon (✓ ▶ ○ 🔒) plus a screen-reader state word.
- **Do not make people click to read.** The current step is open on load, done steps are
  folded, locked ones show what unlocks them. Accordion-by-default would add a click per step.
- **Locked steps show their title, never their statement** (`REVEAL_LOCKED_TITLES`). A workshop
  syllabus is not a secret — seeing what is coming is the point — but content stays server-side
  until the prerequisite is solved.
- **Nothing reloads.** Answering re-reads `/api/v1/workshop/graph`, updates the steppers, and
  fills whatever just unlocked via `/api/v1/workshop/step/<id>`, which renders the *same*
  Jinja partial the page uses — one implementation of the quiz controls, no JS copy to drift.

**Landing (2026-08-04):** a signed-in participant lands on `/workshop` — both `/` and the
default post-authentication redirect are retargeted in `plugins/workshop/landing.py`
(`before_request` / `after_request`, no core edit). CTFd honours an explicit `?next=` *before*
its own fallback, so deep links are untouched; anonymous visitors still get the public index
page, which is also the Intro section of the workshop page for everyone else.

Consequences: the challenge board and its modal still exist (untouched, still reachable at
`/challenges`, just no longer the first thing a participant sees); the Parcours graph becomes secondary — the inline steppers cover progress for a linear subject, and
the graph earns its place only once subjects really branch.

## 14. Embedding existing runtimes into the workshop page (2026-08-04)

> **Approved by the user 2026-08-04.** This is the design of record for runtimes, not a
> proposal: same-origin iframe + the WRP protocol below. The open questions in §14.6 are
> parameters still to pick, not a re-litigation of the approach.

**Question.** The runtimes already exist as standalone web apps — `tic80-web-editor` (React +
Vite + Monaco + TIC-80 PRO WASM), `pacman-ghost-ai` (static, Monaco + Fengari Lua),
`v86-runner` and its two products `shell-1` / `shell-rpg` (Alpine guest in v86 + xterm), with a
p5.js editor to come. How do they get into the workshop page as a hideable pane, and how does a
*future* runtime get wired in without hassle?

### 14.1 What the existing runtimes already tell us

Read before deciding — this is not a green field:

- **They are already built to be embedded under a path prefix.** `v86-runner` builds static with
  `VITE_BASE` (documented example: `/lab/`), `tic80-web-editor` publishes a GHCR image and a
  Pages build. Serving them from our own origin under `/runtime/<id>/<version>/` costs a compose
  service and an nginx location.
- **They already compose by wrapping, not by forking.** `shell-1` and `shell-rpg` are
  *products*: a submodule of `v86-runner` plus an `integrations/main.js` entry that imports
  `@runner/app.js` and registers plugins. A runtime adapter belongs in exactly that slot.
- **`shell-1` already carries the subject inside the runtime** (`#subject-panel` next to the
  terminal), and `pacman-ghost-ai` ships its `atelier*.md` in the repo. Moving that text to CTFd
  is the whole point: the runtime becomes a dumb execution surface, the platform owns content,
  progress and scoring.
- **Focus is already a known problem — and an iframe *fixes* it.**
  `shell-1/integrations/plugins/scrollFocus.js` exists solely because xterm and v86 capture
  wheel and keyboard globally, so the subject pane and the terminal fight over input inside one
  document. An iframe is a native focus and event boundary: that entire class of hack goes away.
- **`shell-1` already ships a CTFd plugin** (`integrations/ctfd_shell1_flags`: dynamic HMAC
  flags + custom Python validators, spec carried in `connection_info`). It works, but it is
  *per-workshop* plugin code — the thing to generalize into the workshop plugin later, not the
  pattern to repeat per runtime.
- **Assets are big.** The shell disk images are 256–512 MB. For a room of 30 (or a session of
  2000) this is the dominant operational constraint and argues for serving runtime assets from
  the session's own deployment, not GitHub Pages.

### 14.2 Decision: same-origin iframe

| Option | Verdict |
|---|---|
| **Same-origin iframe**, served next to CTFd | **Recommended** |
| Cross-origin iframe (GitHub Pages, separate host) | Rejected as default |
| Mount the runtime into the workshop page (no iframe) | Rejected |
| Separate tab/window | Keep as an optional "pop out", not the default |

*Why not same-document:* every runtime is a whole app — React vs vanilla, its own global CSS,
its own global key/wheel handlers. Mounting them into CTFd's Alpine/Bootstrap page means CSS
collisions, the `scrollFocus.js` war on every runtime, one runtime crash taking the page with
it, and rebuilding each app as a mountable library. It also breaks their standalone use.

*Why not cross-origin:* storage for a cross-site iframe is partitioned (Chrome) or blocked
(Safari ITP). `tic80-web-editor` autosaves the cart to `localStorage` — embedded, a student's
work would silently diverge from the standalone app, or vanish. Clipboard also needs explicit
`allow=` grants, and copy-paste into a shell is not optional in a Linux workshop. CTFd has zero
CORS support (§3.1), so anything beyond `postMessage` would need new endpoints anyway.

*Why same-origin works:* `localStorage` behaves exactly as standalone, clipboard and focus are
free, `postMessage` still gives a clean contract, and one nginx already serves `/` and `/api/`
(§3.1) so adding `/runtime/…` is a location block.

**Trust caveat, stated honestly.** A same-origin iframe can reach CTFd's cookies and API as the
user. For *our own* runtimes this is acceptable, and it grants a student nothing they don't
already have with devtools on any CTFd page — they still cannot read a flag they haven't earned.
It would be unacceptable for a third-party runtime: the rule is **embed only runtimes we
control**. If that ever changes, the escape route is designed in below (host-owned persistence
makes a cross-origin, storage-partitioned iframe viable).

### 14.3 The contract: a small runtime protocol (WRP)

One versioned `postMessage` protocol, implemented once on the CTFd side (generic, never changes
per runtime) and once per runtime as a ~50-line adapter. A runtime that speaks it is wired in by
metadata alone.

Guest → host: `ready` (handshake: protocol version, runtime id, capabilities) · `result`
(a task is done, with detail) · `submit` (propose an answer) · `state` (dirty/snapshot).
Host → guest: `init` (protocol, session, options) · `load` (the active step and its workspace:
cart, starter files, disk image) · `focus` · `snapshot?`.

Three rules carry most of the value:

0. **`result` is advisory — a runtime never solves a step** (§14.6). Student code shares the
   page context in at least one runtime, so anything auto-submitted on the runtime's word is
   forgeable by the student.
1. **The runtime never calls the CTFd API.** It asks the host to submit; the host owns the CSRF
   nonce, the endpoint and the error handling. So credential handling exists in one place, a
   runtime bug cannot become a scoring bug, and every runtime stays fully usable standalone
   (GitHub Pages, offline, tests) because without a host the protocol is simply silent.
2. **One iframe per workshop, not per step.** Booting a 256 MB VM per accordion step is
   unacceptable; the pane is mounted lazily on first open, kept alive, and told which step is
   active via `load`. §13's single-page view is what makes this possible — a modal or a page
   reload per challenge would kill the VM every time.

### 14.4 Wiring a runtime in (the "without hassle" part)

Declared in the subject/workshop metadata, consistent with §10's "the repo is enough to
deploy":

```yaml
runtime:
  id: tic80
  source: ghcr.io/kevin-cazal/tic80-web-editor:<tag>   # or a release dist tarball
  mount: /runtime/tic80/<version>/
  pane: { title: "TIC-80", default: open, size: "55%" }
```

with per-step overrides in the markdown marker (`<!-- ws: runtime: {cart: carts/step3.tic} -->`).
Distribution: GHCR image behind nginx where one already exists (tic80), otherwise a static dist
unpacked into a shared volume. Both are pinned by version and cached immutably — which also
answers the 256 MB disk image: pull once per room, serve from the LAN.

Adoption cost per runtime, in order of payoff:

| Runtime | Work |
|---|---|
| **tic80-web-editor** (pypong) | Adapter + an `?embed=1` chrome-less mode. Already has autosave and a URL-param convention. |
| **pacman-ghost-ai** | Move `atelier*.md` into a subject repo (content convention); the game already knows when the ghost behaves, so it can emit `result`. Highest payoff: it proves "content in CTFd, runtime dumb". |
| **shell-1 / shell-rpg** | Adapter as one more `integrations/plugins/*.js`; **delete** the in-runtime subject pane and most of `scrollFocus.js`. Then generalize `ctfd_shell1_flags` (dynamic HMAC + validators) into the workshop plugin. |
| **p5.js editor (future)** | Built against WRP from day one — it is the template. |

### 14.5 Suggested phases (replacing the on-hold Phase 3)

- **A. Spec, no code** — `docs/RUNTIME_PROTOCOL.md` + the metadata schema above. **DONE 2026-08-04.**
- **B. Host side** — the hideable, resizable, lazily-mounted pane in the workshop page plus the
  protocol client, validated against a trivial mock runtime (so tests never boot a VM).
  **DONE 2026-08-04** — `plugins/workshop/runtime.py`, `assets/runtime.js`,
  `assets/runtime/mock/`. The mock proves the advisory rules: emitting `result`/`propose`
  changes no progress.
- **C. First real runtime** — tic80 for pypong, end to end. **DONE 2026-08-04.**
  `tools/build_runtime.sh tic80` builds the dist for its mount path (fetching the *compiled*
  TIC-80 PRO WASM from the project's GitHub Pages build rather than recompiling it with
  Emscripten — 20-40 min and several GB otherwise), and `adapters/tic80.js` is 60 lines. The
  tic80-web-editor repository was not modified at all. Verified in-browser: the frame mounts on
  demand, TIC-80 boots, Monaco loads, and the cart autosaves to `tic80-web-editor-cart` **on
  CTFd's origin** — the §14.6 persistence claim, confirmed.
- **D. Content migration** — pacman-ghost-ai text into a subject repo. **DONE 2026-08-05.**
  Split in two: `kevin-cazal/pacman-ghost-ai_new` is the subject (convention 2.0 `subject.yaml`,
  `intro.md` + `atelier1.md` + `atelier2.md` + `img/`), `kevin-cazal/pacman-ghost-ai_runtime` is
  the app with history preserved and the instructions pane, markdown loading and images stripped
  — the original repo untouched. Both private for now. This is the first **multi-document**
  subject, and it is what forced the rest: `sync_subject.py` gained asset upload (images were
  imported as broken links otherwise), one closing step per document rather than one per
  subject, and one CTFd Page per document so each part gets a navbar entry and a route
  (`/workshop/<part>`). `/workshop` became the index of those parts — cards for a multi-part
  subject, a redirect for a single-part one. Convention 2.0 itself was published at
  `kevin-cazal/workshop-content-tools`, with `tools/check_parser_sync.py` failing the validation
  suite if the vendored parser drifts from it. Running on :8081 (`compose/pacman.env`).
- **E. v86 family** — shell-1/shell-rpg, and generalize the dynamic-flag plugin.

### 14.6 Resolved with the user (2026-08-04)

**1. Progression — "runtime does not gate progression."**

Clarifying the question that was asked back, there were three possible meanings, and they are
very different:

| Meaning | Who decides a step is solved | Verdict |
|---|---|---|
| *Gate* — the runtime's verdict unlocks the next step | the runtime | **No** (user) |
| *Auto-submit* — the runtime notices the task is done and submits for you | CTFd, but triggered by the runtime | **No** — see below |
| *Advisory* — the runtime says "this looks done", the participant still submits | CTFd, triggered by the participant | **Yes** |

So `result` is **advisory only**: it may light up the submit control, prefill an answer field
with something the runtime legitimately produced (e.g. a flag the guest VM printed), or show a
"looks done" marker. It never solves anything by itself. Validation codes and quizzes stay the
only mechanics that actually solve a step.

This is not just conservatism. In `pacman-ghost-ai` the **student's own Lua runs in the page's
JS context** (Fengari) — so student code can emit a `postMessage`. If the host auto-submitted on
`result`, "write a ghost AI" would collapse into "postMessage a fake result". Advisory-only
closes that path for every runtime at once, without needing to reason about each one's sandbox.

**2. Server-side persistence — "does my runtime need special code? I want to keep the runtime
runtime-only."**

**No runtime code at all.** This falls out of the same-origin decision (§14.2): the iframe and
CTFd share one origin, therefore *one* `localStorage` bucket. The keys `tic80-web-editor` already
autosaves to are directly readable and writable from the workshop page. The host can snapshot
them to a plugin table on a timer and restore them on open, keyed by CTFd user — generic, blind
to the runtime's schema, and invisible to the runtime, which keeps behaving exactly as it does
standalone.

Consequences worth knowing:

- **It fixes shared classroom machines.** Today two students on one PC share the browser's
  storage and see each other's cart. Host-driven restore keyed by CTFd user id makes the pane
  show *your* work regardless of the machine.
- **Editor-scale only.** The origin's `localStorage` quota (~5–10 MB, now shared between CTFd and
  every mounted runtime) fits carts and source files, not a v86 VM state. Shell workshops persist
  through the guest's 9p `/mnt/host` share instead — a different mechanism, out of scope here.
- The optional `snapshot`/`state` protocol messages stay in the contract for runtimes that
  *want* to be explicit, but nothing requires them.

**3. Pane placement — side-by-side is fine for most cases.**

Side-by-side is the default. `pane.placement: side|bottom` stays in the runtime metadata so a
terminal-shaped runtime can ask for a bottom drawer, but nothing has to set it.

## 15. Scoping santa_shooter — `topology: free`, `requires`, and what they miss (2026-08-05)

The real workshop of CLAUDE.md (French lycéens, TIC-80 + Lua, four chapters) is the next
subject to convert. Two convention fields it needs are documented at
`docs/CONTENT_CONVENTION.md:146-147` and implemented **nowhere** — not in `ws_parser.py`, not
in `sync_subject.py`. This section scopes them against the actual repo content, read at
`kevin-cazal/santa_shooter@6350590`.

### 15.1 What the content actually is

31 exercises over four documents. Confirmed by reading the files, not the CLAUDE.md summary:

| Document | Exercises | Heading level | Shape |
|---|---|---|---|
| `starter1.md` | 9 | `##`, scoped by `#` | linear |
| `visual_effects2.md` | 7 | `##` | 3 linear + **4 optional bonus** |
| `code_refactor3.md` | 5 (of 7 `##`) | `##` | linear, gates gameplay |
| `gameplay4.md` | 10 | `###`, grouped by `##` | **free choice, independent** |

`gameplay4.md` states the rule in its own prose: *"Choisir un ou plusieurs défis dans la liste,
dans l'ordre de son choix. Chaque défi est indépendant des autres."* That is the requirement,
in the author's words.

Three corrections to the CLAUDE.md table, which is stale:

- **The reference solutions mostly do not exist.** Only `starter.lua` and `visual_effect/step1..7.lua`
  are in the repo. There is no `code_refactor/` and no `gameplay/` directory. Instructor
  material either way, so it does not block conversion, but do not plan around it.
- **Slug collisions are already solved.** "Sprite" x3 and "Input" x2 in `starter1.md` scope by
  their parent `#` (`slugify(f"{category}-{title}")`), giving `mouvement-joueur-sprite`,
  `bullet-sprite`, `enemy-sprite`. Gotcha #2 needs no new mechanism.
- **Four exercises have no body at all.** `## Bonus SFX` and `## Bonus Music` are bare headings;
  most others are two or three bullets. Converting is *authoring*, not transcription — and
  CLAUDE.md forbids inventing workshop content, so this needs the author.

### 15.2 The two fields are not sufficient — `optional` is the missing third

`topology: free` and `requires` between them express **unlocking**. They cannot express
**counting**, and `visual_effects2.md` needs it.

A bonus exercise must (a) unlock after `Particules`, (b) gate nothing downstream, (c) **not
count towards completion**. `requires` gives (a) and (b): point the bonus at `particules`, and
point the next chapter at `particules` too rather than at the bonuses. Nothing expresses (c).
Without it, a participant who skips all four bonuses tops out at 3/7 in that part and can never
reach 100% — which breaks the §13 invariant that 100% is reachable and is what gets the
end-of-workshop feedback in (`phase2_validate.py`: *"100% is unreachable without feedback"*).

The mechanism already exists on the plugin side: `page.py` `NON_TASK_KINDS` and the `counts`
flag do exactly this for the closing note. It needs a convention field to drive it —
`optional: true` on an exercise — and one line in the sync to carry it through.
**Resolved with the user 2026-08-05: `optional: true` it is.**

### 15.3 Semantics to fix before coding

**`topology: free` — what gates the members.** Every exercise of a free chapter takes the same
single prerequisite: the node preceding the chapter in board order (the chapter's *entry*). No
intra-chapter chain. CTFd's `requirements.prerequisites` is a plain AND-list, so this is native.

**What a free chapter gates in turn.** **Resolved with the user 2026-08-05: the chapter's
closing step — "I'm done here".** Reasoning below.
"Choose one or more" means there is no last node to hang the next chapter off. Three options:

| | Behaviour | Verdict |
|---|---|---|
| next requires **all** members | defeats "choose one or more" | no |
| next requires the chapter's **closing step** | the participant declares the chapter finished; the closing step already exists, generated from trailing prose | **recommended** |
| next requires **N of M** | no OR and no threshold in CTFd — plugin-side gating logic | not for this |

The cost of the recommendation is that the closing step becomes clickable at any time, so a
participant *can* skip a free chapter wholesale. For optional creative content that is
acceptable; for a gating chapter it would not be. It does not arise in santa_shooter, where
`gameplay4.md` is last — decide it before a free chapter ever sits mid-course.

**`requires` needs stable ids.** Ids are derived from headings unless an explicit `id:` is set,
so a cross-chapter `requires` silently breaks when someone rewords a heading. Any `requires`
target should carry an explicit `id:`, and the linter must reject a target that does not
resolve — already promised at `CONTENT_CONVENTION.md:303`, implemented nowhere.

### 15.4 The work, by file — **DONE 2026-08-06**

Items 1, 2, 4, 5, 6, 7 and 8 landed in `25432d0`; item 3 and the content landed on 2026-08-06.
Status per item is noted inline below. The converted subject is `content/santa_shooter`
(31 exercises, 4 parts, 4 closing steps), synced and walked end to end on a disposable
instance: part 1 linear, part 2 with both bonuses skipped reaching 4/4, part 4 opening all ten
challenges at once with only its closing step current, and 3/11 after "I'm done here".

**`tools/ws_parser.py`**
1. **Retain chapters as nodes.** Today a `type: chapter` heading is treated exactly like prose:
   it sets `category` and is discarded. `topology` lives on a chapter, so there is nothing to
   hang it on. Needs a `Chapter` record (slug, title, topology, its exercises).
2. Parse `topology`, `requires`, `optional` into typed fields instead of leaving them in the
   raw `meta` dict, where the sync currently ignores them.
3. **Fix the category hijack while in there.** `if node_type in ("chapter", "prose") and level < 3:
   category = title` means *any* level-2 prose heading silently renames the part. It bit pacman
   (`## Glossaire`) and it will bite `code_refactor3.md` (`## Notion d'objet en Lua`, prose,
   sits before four exercises and would take their category).
   **Resolved 2026-08-06: an explicit `type: prose` marker no longer opens a part.** The first
   idea — "only chapters name parts" — is wrong: pypong carries `type: chapter` on its H1 and
   takes its parts from unmarked `##` headings, so that rule would flatten it into one part.
   An explanatory heading and a part heading are textually identical, so the author has to be
   the one to say which is which; writing the marker is how they say it. Unmarked headings keep
   the old behaviour, which is where every existing subject's parts come from — pypong and
   pacman parse to byte-identical categories after the change.
4. Linter: `requires` targets resolve; `topology` only on chapters; no `requires` cycles;
   `optional` only on exercises.

**`tools/sync_subject.py`**
5. Replace the linear spine (`zip(chain, chain[1:])`) with a resolver: explicit `requires` wins;
   otherwise the enclosing chapter's topology decides (linear → previous sibling, free → chapter
   entry); optional nodes are never anyone's implicit predecessor.
6. Carry `optional` onto the challenge so the page can read it.

**`plugins/workshop/page.py`**
7. `counts = False` for optional steps — same path as `NON_TASK_KINDS`.
8. **CURRENT in a free chapter.** `_steps` marks the first unlocked-and-unsolved step CURRENT
   and auto-opens it. With ten simultaneously available challenges that is arbitrary: it points
   at whichever happens to be first in source order. A free chapter should mark none of them
   CURRENT and present them as a menu — CLAUDE.md is explicit that `gameplay4.md` "is a menu,
   not a chain, and should not be rendered as a linear list". The `/workshop` index cards
   already render this shape correctly (several parts "Available now"); the *step* list inside
   a part does not.

**Content** — `subject.yaml`, an `intro.md`, and `ws:` markers for 31 exercises. The bulk of
the wall-clock, and the part that needs the author. **Converted 2026-08-06**, with the
author's markdown carried over verbatim. Three deliberate deviations, all reversible:

- **`## Bonus SFX` and `## Bonus Music` were bare headings** in the source, so the first pass
  carried them as `type: prose` rather than importing two steps with no instructions. **Closed
  2026-08-06 with the author's approval, and it was transcription after all**: the reference
  code for both exists. `visual_effect/step1..7.lua` maps 1:1 onto the seven headings of that
  document, `step4` being SFX (`sfx(0)` at the shot, `sfx(1)` at both collisions, plus a
  waveform in the cart) and `step5` being music (`music(0, -1, -1, true)` at top level, track 0
  left to compose). So §15.1's "the reference solutions mostly do not exist" is true of the
  subject as a whole but **not** of `visual_effects2.md`, which is complete — the gap was
  documentation, not design. The bodies were written from that code, in the file's own style,
  and the subject is now **31 exercises**.
- **A closing note per part** ("Fin de la partie N"). The convention builds the closing/rating
  step out of trailing prose, and three of the four documents had none. Part 4's is also the
  free chapter's "I'm done here" gate.
- **`gameplay.md` renamed to "la partie Gameplay"** in the part 3 closing text: the file was
  renamed to `gameplay4.md` in `6350590` and the reference was left behind, and a participant
  has no filenames to look at anyway.

Also worth knowing: `starter1.md` gives three steps named "Sprite" and two named "Input". The
slugs scope by parent (`bullet-sprite`) so nothing collides, but the *display* names repeat,
disambiguated only by the part heading above them. Fine as it stands; a fix would be either
authoring (rename the headings) or a page change (show the part next to the step name).

**Not needed:** the tic80 runtime is already built and validated (phase C), so this does not
wait on phase E.

### 15.5 What is already free

- `plugins/workshop/graph.py` reads whatever prerequisites exist and reverses them into edges.
  A DAG with fan-out needs no change; the Parcours page draws it as-is.
- CTFd's `requirements.prerequisites` is an AND-list, natively evaluated, filtered from list
  responses and refused at solve time. Nothing here needs a custom unlock engine.
- Board `position` and the closing-step insertion are order-based and topology-blind.

## 16. Server-side persistence of work in progress (2026-08-06)

§14.6 resolved the *how* with the user and nothing was built: "work in progress lives in the
browser, so two students sharing a machine share a cart". This section is the design, then the
implementation.

### 16.1 What the same-origin decision already gives

The runtime frame is served from CTFd's own origin (§14.2), so the frame and the workshop page
share **one** `localStorage` bucket. The host can therefore read and write the runtime's keys
directly, with no message, no runtime code, and no cooperation from the runtime at all. The
adapter already announces which keys are its own — `ready.storageKeys`, honoured by nobody until
now (`adapters/tic80.js` reports `tic80-web-editor-cart` and `tic80-web-editor-layout`).

So the whole feature is: **read those keys, POST them, and put them back before the frame boots.**

### 16.2 The rule that matters: whose work is this?

Restoring "the server copy" unconditionally would lose work whenever the server copy is older
than what is on the machine. Keeping "the local copy" unconditionally is exactly today's bug: the
next student on that PC inherits the previous one's cart.

So the host stamps the bucket with an **owner**: `ws-workspace-owner = "<user id>:<runtime>"`,
plus `ws-workspace-local` = the epoch ms of the last local change it saw. At mount time:

| Local bucket | Server row | Action |
|---|---|---|
| owner is someone else, or absent | anything | **Server wins.** Restore it, or *clear the runtime's keys* if the server has nothing. This is the shared-machine fix, and clearing on an empty server row is the half that makes it work. |
| owner is me | none | Keep local. First session, or an instance that has never saved. |
| owner is me | older than `ws-workspace-local` | Keep local — unsaved work on this machine outranks a stale snapshot. |
| owner is me | newer | Restore it. They worked somewhere else since. |

Restoring happens **before the iframe is created**, because a runtime reads its storage once at
boot. That falls out of the pane being lazily mounted: the snapshot is fetched at page load, and
`mount()` waits for it.

### 16.3 Saving

A poll, not a message: `localStorage` fires no event in the document that wrote it, and the point
is to need nothing from the runtime.

- Fingerprint the watched keys every 5 s; when it changes, mark dirty and stamp `ws-workspace-local`.
- Flush at most every 15 s, and immediately on `pagehide` / `visibilitychange: hidden` with
  `fetch(..., {keepalive: true})` — `sendBeacon` cannot carry the CSRF header.
- **Never save before the first restore has completed**, or an empty bucket overwrites a good
  snapshot on a slow connection.

### 16.4 Storage

One row per (user, runtime): `workshop_workspace(id, user_id, runtime_id, data JSON, updated)`,
unique on (user_id, runtime_id), created by the plugin's existing `app.db.create_all()`.

- `GET /api/v1/workshop/workspace` → `{keys, updated}` for the current user, `{}` when there is none.
- `POST` the same shape → upsert, returns `updated`.
- Authed only, and **the user id comes from the session, never from the body** — otherwise this
  is a "read another student's work" endpoint.
- Caps: 16 keys, 512 KB serialized. A cart with sprite and map data is tens of KB, so this is
  generous; the limit exists so a stuck loop cannot fill the disk. Over it: 413, and the client
  stops trying rather than retrying forever.

**Editor-scale only** (§14.6): the origin's `localStorage` quota is a few MB, shared with CTFd.
Carts and source files fit. A v86 disk image does not, and shell workshops persist through the
guest's 9p share instead — a different mechanism, out of scope here.

### 16.5 Deliberately not in scope

- **Conflict resolution beyond last-write-wins.** Two tabs of the same user share one bucket, so
  they cannot diverge; two machines at once is not a workshop scenario worth merging carts for.
- **History or snapshots per step.** One current state per runtime. Versioning is a different
  feature and would need a retention policy.
- **Instructor access to a participant's work.** The row is a natural hook for §11's review queue
  (read the cart the student is asking about), but reading someone else's work needs the review
  queue's authorization, not an ad-hoc admin route.

## 17. The feedback report (2026-08-06)

Ratings have been landing in CTFd's own `ratings` table since 2026-08-04 and nothing reads them.
With santa_shooter each participant leaves **five** verdicts (one per part, plus the workshop)
on top of per-step content ratings, so there is now enough there to act on.

### 17.1 Two populations, not one

They answer different questions and must not be averaged together:

| Population | Where it comes from | The question it answers |
|---|---|---|
| **Verdicts** | ratings on `quiz_type = 'rating'` steps — the closing step of each part, plus the subject's final one (`workshop_final_step`, §16 of the fixes) | did this *part* land? did the workshop? |
| **Step quality** | ratings on every other challenge, given inline once a step is done | which exercise is badly written? |

Until 2026-08-06 every part's closing step asked "how was this workshop?", so the first column
was unusable — four answers to the same question, three of them about something else. That is
fixed, which is what makes this report worth building now.

### 17.2 The denominator is the finding

Two 👎 out of three raters is a crisis; two out of forty is noise. Every row therefore carries
**how many people could have rated it** — solvers of that step — beside how many did. A step
nobody rated is not a good step, it is an unmeasured one, and the report says so rather than
leaving a blank.

### 17.3 Anonymous by default

The aggregate and the comments are shown **without names**. Teenagers rate honestly when the
answer is not attributable, and the value here is "which step is weak", not "who complained".
`ratings.user_id` still exists for anyone who needs it later — this is a choice about the
report, not about the data.

### 17.4 Shape

`GET /admin/workshop/feedback`, admins only, registered in the admin menu bar (CTFd's own
`register_admin_plugin_menu_bar`) so it sits where an instructor already looks. Sections:

1. **The workshop verdict** — the final closing step: split, response rate, every comment.
2. **Part by part** — one row per part in reading order, so a part that loses people is visible
   as a shape rather than as a number.
3. **Step quality, worst first** — the working list. Sorted by 👎 count, then by how many rated:
   the point of the page is to find what to rewrite before the next session.
4. **`feedback.csv`** — the same rows, for anyone who would rather work in a spreadsheet.

Not built: per-session comparison (one instance is one session, so the instance *is* the
grouping), and any notion of a trend over time.

### 17.5 Drift between vendored content and its subject repo (built alongside)

`tools/check_content_sync.py` compares `content/<subject>` with the repo it came from, the way
`check_parser_sync.py` does for the parser. The mapping lives in `content/upstreams.yaml`
(platform bookkeeping, deliberately not a `subject.yaml` field: which copy the platform vendors
is not something a subject repo should have to know). The repos are private, so it goes through
`gh api` and reports **skip** without an authenticated CLI — never a pass.

`subject.yaml` is compared as parsed YAML rather than bytes, because the vendored copy carries a
header comment saying it is the vendored copy.

**`content/pypong` is deliberately unmapped.** It is a *conversion* of `pypong_new` — that repo
is still schema 1.5, with `metadata.yaml` and visible `> QUIZ.A1.1` markers, in another org — so
comparing them would report the conversion itself as drift on every run, forever. The check says
so out loud instead of pretending pypong is covered.

## 18. Phase E — the v86 family, scoped (2026-08-06)

`shell-1` (Shell 101/102) and `shell-rpg` are Alpine guests running in the browser under
[v86](https://github.com/copy/v86). Phase E was written as "embed them and generalize the
dynamic-flag plugin". Reading the repos, it is three jobs, and one of them was a real unknown.

### 18.1 What already exists

| Piece | What it is | Reusable as-is? |
|---|---|---|
| `kevin-cazal/v86-runner` | the generic UI: xterm serial console, 9p host-file browser, plugin menu API. Builds with a **relative base** (`./`) so `dist/` can be served under any path prefix | **yes** — that is exactly what `/runtime/<id>/<version>/` needs |
| `shell-1`, `shell-rpg` | product repos: runner + their own VM image + subject content | as runtimes, yes |
| `.v86b` bundle | BIOS + raw disk + a v86 `save_state`, packed by `pack-v86-bundle.mjs`. The save state is why a workshop VM appears instantly instead of cold-booting Alpine | yes |
| `ctfd_shell1_flags` | CTFd plugin: HMAC-per-team dynamic flags + custom Python validators, spec carried in `connection_info` | the *mechanism*, yes; the packaging, no (§18.4) |
| `deploy_challenges` | pushes `challenges/*/challenge.yml` + `private/flag.yml` into CTFd | it is the other content pipeline (§18.4) |

### 18.2 Finding: v86 does **not** need cross-origin isolation

This was the gate. `shell-1` serves `COOP: same-origin` + `COEP: require-corp` and ships
`coi-serviceworker.js`, and if v86 genuinely required cross-origin isolation then **CTFd's whole
origin** would have to become isolated — which would break every cross-origin subresource in
existing content (pypong's images are hotlinked from raw.githubusercontent) unless the workshop
moved to `COEP: credentialless`. That would have shaped the entire deployment.

It is not required. Measured, not assumed:

- Neither `v86.wasm` nor `v86-fallback.wasm` declares a **shared** memory in its wasm memory
  section, and `libv86.js` never references `SharedArrayBuffer`.
- A harness serving v86 + the shell-rpg disk **without** COOP/COEP, with the emulator in an
  iframe inside an ordinary page, boots to an Alpine autologin shell:
  `crossOriginIsolated=false`, `SharedArrayBuffer=undefined`, serial output ending in
  *"Welcome to Alpine!"*.

So embedding v86 is the same shape as tic80: build a dist for its mount path, serve it
same-origin, inject an adapter. The COI headers upstream are inherited caution.

### 18.3 The real constraint is bandwidth, not disk

`v86-runner` boots from `hda: { buffer: diskBuffer }` — the **whole** disk is downloaded into
memory before the VM starts. shell-rpg's image is 256 MB raw (~90 MB on disk, less again
compressed). One student on GitHub Pages: fine. Thirty students on school wifi at 09:05: not
fine, and that is the actual deployment.

**Resolved with the user 2026-08-06, twice.** First: sessions are internet-hosted most of the
time (a standalone session stays possible because the repos are public and clonable). Then, on
seeing the measurements: **keep the whole `.v86b` bundle.** A 45-second cold boot is the
frustration the save state was built to remove, and the 330 MB is a distribution problem, which
is solved by a CDN, mirrors and downloading ahead of time — all of which already exist (§18.7).

That decision is the one that keeps §14's rule intact: **`v86-runner` is not modified at all.**
The measurements below stay on the record because they price the alternative, not because the
alternative was taken.

Original framing:

**Sessions are internet-hosted most of the time** (a
standalone session stays possible because the repos are public and clonable). That removes the
LAN assumption, so option 1 below is not enough on its own and range loading becomes the
requirement rather than the refinement.

Measured on the shell-rpg image, with the harness of §18.2 counting bytes served:

| Path | Transferred to reach a shell | Time to prompt |
|---|---|---|
| **Range-loaded raw disk** (`hda: {url, async: true}`) | **32 MB** — 30 MB of the 256 MB disk, plus wasm and BIOS | 45 s (a real cold boot) |
| Whole-bundle `.v86b` (what `v86-runner` does today) | the entire disk **and** the save state, before the VM starts | ~instant once downloaded |

So the two options trade the same currency in opposite directions: range loading moves 8× less
data but pays a cold boot; a save state skips the boot but makes the download mandatory and
larger. Not measured here: the size of a `.v86b` save state, because no state artifact exists on
this machine to weigh — building one is part of E1.

Three ways out, considered:

1. **Serve the bundle from the session's own CTFd instance** (same-origin, like every other
   runtime). One instance per session already runs on the room's network, so the transfer is
   LAN-speed and the "one reverse proxy" question answers itself. Costs disk on the instance and
   makes `tools/build_runtime.sh` responsible for staging a large artifact.
2. **Range-loaded disk** (`hda: {url, async: true}`), which v86 supports natively and the
   harness above used — only the blocks the guest touches ever transfer. This needs a change in
   `v86-runner`, i.e. touching a runtime repo, which §14 deliberately avoids. Given the answer
   above it looked mandatory — until the bundle decision above, which drops it. Recorded as the
   fallback if bandwidth ever bites: it needs an additive change in `v86-runner` (a disk *URL*
   source beside the existing buffer source) so the product repos keep working standalone.
3. Keep it on GitHub Pages and embed cross-origin — rejected: partitioned storage, no adapter
   injection, and §16 persistence stops working.

### 18.4 Two content pipelines, one platform

shell-1's challenges are `challenge.yml` (name, category, description markdown, value, tags) plus
`private/flag.yml`, deployed by `deploy_challenges` into a **CTFd fork** with the flags plugin
mounted. The platform's are convention-2.0 subject repos read by `sync_subject.py` into stock
CTFd. Both work; they do not compose, and CLAUDE.md is explicit that custom behaviour belongs in
one plugin rather than a fork.

Reconciling means, in order: convert the challenge set to a subject repo (the descriptions are
already markdown, so this is transcription plus `ws:` markers), then bring the two flag
mechanisms into `plugins/workshop/` — CTFd core already handles static and regex flags, so what
has to move is **dynamic HMAC flags** (per participant rather than per team, since these
instances run in `users` mode) and **custom validators**. Validation codes stay the interim
mechanic for everything else.

### 18.5 Suggested order

- **E1. Embed. DONE 2026-08-06.** `tools/build_runtime.sh shell-rpg` builds a 4.5 MB dist for
  `/runtime/shell-rpg/<sha>/` (the repo's own Pages sequence, submodules and all — its
  application code *is* the v86-runner submodule, and `fzstd` lives there, so installing only the
  product repo's dependencies fails to resolve the bundle loader). `adapters/shell-rpg.js`
  announces `ready` and handles `focus`; it reports **no** storage keys, because the runner keeps
  only debug flags in `localStorage` and the participant's work lives in the 9p share. Verified
  in a browser: the pane mounts inside the workshop page, the adapter is injected, the handshake
  fires, and the welcome screen offers its download links and file picker — the manual flow of
  §18.8, unchanged from how the workshop already runs.
- **E2. Distribution. DONE 2026-08-06** — §18.3 (keep the bundle) and §18.8 (a mirroring cache
  in the stack, with the manual flow kept). No 330 MB artifact touches the instance.
- **E3. Convert the content.** **shell-rpg first — it is the starter** (resolved with the user
  2026-08-06); shell-1 is the advanced subject that follows it. Which makes this pair the first
  real test of something specified and never built: see §18.6.
- **E4. Generalize the flags.** Dynamic HMAC + custom validators into `plugins/workshop/`, so the
  CTFd fork can be retired.

**Persistence note (§16):** the 9p host share is where a shell workshop's work lives, and
`v86-runner`'s own README says host VFS state is *not* captured in a v86 memory snapshot. So the
workspace snapshot for this family is the 9p tree, not `localStorage` — the runner already
exposes a tar.gz export of a folder, which is the hook. Same table, different collector.

### 18.6 shell-rpg then shell-1 is the first true *workshop*, and that is unbuilt

Every subject so far has been deployed as an implicit one-subject workshop. `shell-rpg` (starter)
followed by `shell-1` (advanced) is the composition CLAUDE.md calls non-negotiable — "the starter
must ALWAYS be done before doing more advanced subjects" — and `docs/CONTENT_CONVENTION.md` §4.1
specifies it: a workshop repo with `workshop.yaml`, `role: starter | advanced`, and the gating
rule that every advanced subject's entry challenges take the starter's final exercises as
prerequisites.

**Neither `sync_subject.py` nor `ws_parser.py` mentions `workshop.yaml`.** The sync handles one
subject, full stop. pacman looked like a counter-example but is not: its two ateliers are two
*documents* of one subject, chained by the ordinary linear spine.

So phase E carries an extra job that phases A–D never surfaced, and it is not a small one:
multi-subject import, cross-subject prerequisites, and a decision about what a second subject
does to `/workshop` (a second index? a workshop-level index above the part cards?). Worth
scoping on its own before E3, rather than discovering it while converting content.

### 18.7 How the bundle is built and shipped — it is already solved

Read from the repos rather than assumed (`shell-rpg-vm-image/.github/workflows/release.yml`):

1. `alpine-make-vm-image` + the repo's `build.sh`, under `sudo` with `nbd`, produce
   `alpine-bios-256M.img` — 256 MB raw.
2. `v86-runner`'s `build-state` boots that disk headless to a console of a fixed size
   (`V86_STATE_CONSOLE_COLS/ROWS`) and saves `alpine-bios-256M.v86state` — 342 MB.
3. `build-bundle --disk … --state … -o …` packs BIOS + disk + state into
   `shell-rpg-256M.v86b` — **330 MB**, the format defined in `v86-runner/src/bundle/format.js`.
4. CI uploads it to **Cloudflare R2** (`s3://epitech/…`, `cache-control: public, max-age=3600`),
   verifies a ranged GET against the public URL, and attaches all three artifacts to a GitHub
   Release.

And the app already knows where to get it — `shell-rpg/.env.example`:

```
VITE_OFFICIAL_BUNDLE_URL=https://cdn.cazal.eu/shell-rpg-256M.v86b
VITE_MIRROR_BUNDLE_URLS=<github release>,<gitlab generic package>,<lab.epitech.academy>
```

A CDN primary plus three mirrors, picked in the welcome overlay. **So the platform never serves
the bundle**: `tools/build_runtime.sh` stages only the dist (a few MB), and the runtime fetches
its own VM exactly as it does standalone. The GitHub release asset has been downloaded 7,429
times, so this path is not theoretical.

**The one gap.** Neither `cdn.cazal.eu` nor the GitHub release asset returns
`Access-Control-Allow-Origin` (checked with an `Origin` header and a ranged GET: 206, range
honoured, no ACAO). A cross-origin `fetch()` therefore fails, which is why the app offers a
*download link plus a file picker* rather than loading the bundle itself. Two ways forward, and
they are the operator's call, not the platform's:

- **Enable CORS on the CDN** for the workshop origins — one setting in R2/Cloudflare — and the
  pane can load the VM by itself. Worth pairing with a versioned filename and a long
  `max-age`: at `max-age=3600` a participant who comes back tomorrow re-downloads 330 MB.
- **Keep the manual flow**, and have the subject's first step carry the download link and the
  "pick the file" instruction. Costs nothing, works today, and matches how the workshop is
  already run.

Either way E1 is small: a `v86` case in `tools/build_runtime.sh` that builds the product dist
with `VITE_BASE=/runtime/v86/<sha>/`, and an adapter. No 330 MB artifact ever touches the CTFd
instance.

### 18.8 A generic mirroring cache in the compose stack (2026-08-06, the user's design)

**Resolved: the manual flow stays** — the subject's first step carries the download link and the
"pick the file" instruction, which is how the workshop is already run and costs nothing.

Alongside it, the stack gains a service that serves a resource from the **first upstream that
answers**, then a local copy, caching whatever it fetches. It is deliberately generic: nothing in
it knows what it is serving. The first user is a 330 MB `.v86b`, and a runtime dist, a dataset or
an ISO would work the same way.

    GET /<any/path>  ->  upstream 1  ->  upstream 2  ->  3  ->  4  ->  local copy  ->  404

| Link | When it earns its place |
|---|---|
| CDN (Cloudflare R2) | normal case: someone else's bandwidth, already cached at the edge |
| GitHub release asset | the CDN is down, or a school network blocks it |
| GitLab / lab mirror | the ones shell-rpg already lists in its own `.env` |
| local copy in a volume | the venue's uplink is down — the case that actually ruins a session |

It is **nginx configuration and nothing else** (`compose/mirror/default.conf.template`), turned
on with `--profile mirror` so existing instances come up unchanged. Upstreams are four
environment variables; unused slots point at the discard port, which refuses instantly and falls
through. A release's 302 towards a signed URL is followed server-side, so the response stays
cacheable instead of becoming a redirect the browser cannot fetch cross-origin.

Three properties fall out of it, and they are why it is worth a service:

1. **The CORS gap closes by itself.** Neither the CDN nor GitHub sends
   `Access-Control-Allow-Origin` (§18.7); the mirror does. So the pane *can* fetch a bundle
   directly if that is ever wanted, without asking anyone to change a CDN setting.
2. **The venue pays once.** The first participant fills the cache; everyone else is served from
   disk at LAN speed. That is the classroom benefit of hosting locally, without staging 330 MB
   by hand on every instance.
3. **It is the first piece of the single-origin topology** §3.1 always assumed and never needed
   until now, because CTFd was alone on its port.

Deliberately not part of it: choosing a mirror by geography, health checks, or anything that
deserves the name load balancing. Sequential fallback plus a cache is the whole design — the CDN
carries the load, the mirror carries the failures.

Verified by `scripts/mirror_check.sh` against nginx stub upstreams, with `docker stop` for an
outage: first upstream serves and caches, a stopped first upstream falls through to the second
*including its redirect*, a stopped second falls through to the local copy, a missing resource is
a plain 404, and a range request is still served from cache once every upstream is gone.

## 19. Composing subjects into a workshop — the gap E3 walks into (2026-08-06)

`docs/CONTENT_CONVENTION.md` §4.1 specifies `workshop.yaml`: a workshop repo naming several
subject repos with `role: starter | advanced`, an optional `order:` for advanced ones, and the
gating rule from CLAUDE.md — *the starter must always be completed before any advanced subject*.
**Nothing implements it.** `tools/sync_subject.py` imports exactly one subject and writes
instance-wide configuration computed from it. shell-rpg (starter) then shell-1 (advanced) is the
first pair that needs this, so it has to be built before E3, not during it.

### 19.1 Everything that assumes a single subject

Inventoried rather than guessed, because the surprises are in the small parts:

| Place | Today | With two subjects |
|---|---|---|
| Topics `ws:<subject>:<slug>` | already namespaced per subject | **fine as is** — this is why re-sync stays idempotent |
| Assets `ws-<subject>/…` | namespaced | **fine as is** |
| `instructor_codes.<subject>.yaml` | one sheet per subject | **fine as is** |
| Board `position` | numbered 1..N per sync, `offset = 1` for the intro | both subjects start at 1 and interleave — needs a per-subject base |
| `__intro__` step | gates the first exercise so nobody skips the introduction | the *advanced* subject's intro is where the cross-subject gate belongs |
| `workshop_documents` | the parts of this subject, overwritten each sync | must accumulate, and carry which subject each part belongs to |
| `workshop_optional`, `workshop_free` | id lists, overwritten | must be unions across subjects |
| `workshop_final_step` | this subject's closing step | only the **last** subject's may claim to end the workshop |
| CTFd Page `index` | the subject's entrypoint document | two subjects fight over one route |
| `/workshop` index cards | a flat list of parts | needs grouping by subject once there is more than one |
| progress counters | every step on the instance | see D5 |
| **`workshop_runtime`** | **one runtime for the whole instance** | **the blocker — see D3** |

### 19.2 Not a finding after all: it is one runtime, parameterized

The first reading of this was wrong and the author corrected it. shell-rpg and shell-1 are **the
same runtime**: both are thin products over `v86-runner`, and the images are interchangeable —
shell-rpg's bundle boots on shell-1's app and the other way round. Comparing the two repos, a
product adds only:

- `integrations/main.js`: `import "@runner/app.js"` plus two small plugins (the bundle download
  link, wheel routing) and a label;
- `integrations/subject.css` and its own welcome HTML;
- a default bundle URL and filename.

Of which, embedded in CTFd, one is redundant already: shell-1's `#subject-panel` renders the
workshop text inside the app, and the platform's whole point is that CTFd renders it.

So the per-subject variation is **data, not a dist**: which bundle to download, what to call it,
and some welcome copy. That has a home already — the adapter runs *inside* the frame with DOM
access, and `init` is where the host tells it about the world. Extending that message with the
subject's runtime `params` lets one dist serve both subjects, with no change to either product
repo.

Two consequences, both good:

- **§14 rule 2 does not bend.** The frame stays mounted across steps *and* across subjects; only
  the parameters change. The teardown-on-crossing worry disappears.
- **A whole slice disappears.** What was going to be "per-subject runtimes" is now a `params`
  field on the declaration and three lines in the adapter, which belong to E3a.

A participant crossing into a subject whose image differs still has to pick a different bundle.
That is inherent to a 330 MB VM, not a platform decision, and it is what the welcome screen is
for.

### 19.3 Decisions to take before writing code

- **D1 — where the manifest lives.** The convention says a workshop repo naming subject repos by
  `repo` + `ref`. Content is currently *vendored* (`content/<subject>`), so the first
  implementation should accept a manifest that names **vendored directories**, with the
  repo+ref form arriving with §10's `provision(repo_url, ref)`. Otherwise E3 is blocked on a
  deployment story it does not need.
- **D2 — what "the starter's final exercises" means. Resolved with the user 2026-08-06: the
  starter's closing step.** Reasoning:
  it already exists, it is one edge rather than N, and acknowledging it is the participant
  saying they are done — the same reasoning as the free-chapter gate in §15.3. The alternative,
  "every leaf exercise of the starter", makes the graph dense for no gain.
- **D3 — per-subject runtime *parameters*, not per-subject dists** (§19.2, corrected by the
  author). The declaration gains `params` (bundle url, filename, whatever a later runtime
  needs); the host sends them in `init`; the adapter applies them to the DOM it already
  controls. One dist, one frame.
- **D4 — one index or two.** Recommend `/workshop` staying the single entry, grouping its cards
  by subject when there is more than one, and each subject keeping its own intro step. A
  workshop-level landing page above the subjects is more UI for a two-subject workshop.
- **D5 — what the progress bar measures. Resolved with the user 2026-08-06: per subject**, plus
  a workshop total on the index. The
  §13 invariant that 100% is reachable and gates the feedback then applies **per subject**: each
  subject keeps its own closing rating, and only the last one asks about the workshop (already
  true since the §17 fix). A single instance-wide bar would tell a participant who finished the
  starter that they are at 40%, which is discouraging and wrong.
- **D6 — advanced ordering.** `order:` present chains them; absent means they all hang off the
  starter and may be done in any order. That is `topology: free` one level up, and the resolver
  already knows the shape.

### 19.4 What this is not

Not multi-tenancy: **one instance per session** stays (§10), and a workshop is still one
instance. Not several *workshops* per instance. Not per-subject accounts, scoreboards or
configuration — those are instance-level and stay that way.

### 19.5 Slices

- **E3a — DONE 2026-08-06.** `tools/sync_workshop.py` reads `workshop.yaml`, lints every subject
  before touching the instance, and syncs them in board order through the same `sync()` — which
  gained `position_base`, `gate_on` and `standalone`, and now returns what the caller needs to
  accumulate. Instance-wide settings are written once by `write_instance_config`. Verified on a
  composed workshop (`content/workshops/tic80-double`: PyPong then Santa Shooter) by
  `scripts/workshop_check.py`: the starter's steps all precede the advanced subject's, no two
  steps share a board position, the advanced subject's intro requires the starter's closing step
  and nothing else, a fresh participant cannot open it, the four configs describe the whole
  workshop, part routes are namespaced per subject, and the index links to all five parts.
  Two bugs it caught on the way: both subjects' intros were pinned to position 1, and a document
  named after its own subject produced `workshop/pypong-pypong`.
- **E3b — DONE 2026-08-06.** `/workshop` groups its cards by subject as soon as there is more
  than one, each group carrying its own progress and a starter/next badge; a single-subject
  workshop keeps the flat grid it had. Parts number within their subject. One thing the grouping
  surfaced: five parts meant five navbar entries, which wrapped CTFd's fixed navbar onto three
  lines and covered the page heading — so in a workshop the part Pages are created `hidden`,
  which drops the menu link and keeps the route. The index is the hub (D4).
- **E3c** — convert the content: shell-rpg first, then shell-1, which is when the whole thing
  gets exercised for real. One `v86` dist serves both.

(The former per-subject-runtime slice folded into E3a as a `params` field once §19.2 was
corrected.)

### 19.6 shell-rpg's content lives in the guest, not in markdown (2026-08-06)

E3c assumed shell-rpg had text to convert. It does not, and that is worth writing down before
somebody goes looking for it again.

Reading `shell-rpg-vm-image`: the guest carries a quest engine
(`/usr/local/share/shell_rpg_engine_mpy/`) with **~25 quests** — `Mkdir`, `HiddenCreate`,
`Treasure`, `ReadFile`, `Move`, `Copy`, `SortBooks`, `Shortcut`, `KillProcess`, a `King`
questline, `Story1`–`Story5` — each teaching a shell command through an NPC. The whole workshop
*is* the game. The repo's only markdown is its README, and the guest→host bridge (`hvc1`) carries
zone-background filenames, **not** quest completion.

Two consequences:

- **The subject is deliberately thin** (`content/shell_rpg`): an entrypoint that gets the VM
  loaded, one step for taking the terminal in hand, and a closing step. Copying the quests into
  CTFd would be maintaining a second, worse copy of a game that already explains itself, and
  CLAUDE.md forbids inventing workshop content anyway.
- **If CTFd should ever track quest progress**, the guest has to say so: emit an event per quest
  over `vm-bridge`, and the adapter forwards it as an advisory `result` (rule 0 — the runtime
  never solves a step). That is a guest change plus ten lines of adapter, and it is a *design*,
  not a conversion. Worth doing only if the room needs it.

It also made the §19.2 `params` field real: `content/shell_rpg/subject.yaml` declares
`runtime.params.bundle_url` / `bundle_name`, the sync carries them into `workshop_runtime`, the
page hands them to the frame in `init`, and `adapters/shell-rpg.js` points the welcome screen's
download link at them. One dist, per-subject data, verified in a browser — the link read
`https://cdn.cazal.eu/shell-rpg-256M.v86b` with the right filename.

**Still open: shell-1.** That one *does* have convertible content — ~100 `challenge.yml` files
with markdown descriptions and `private/flag.yml`, in two chapters (Shell 101, Shell 102) — and
it is the advanced subject of this pair. It is a conversion job of the same shape as
santa_shooter, plus E4's flag work, and it has not started.

## 20. shell-1 and the flags it needs — scoping E3c's second half with E4 (2026-08-06)

shell-1 is the advanced subject of the shell pair, and unlike shell-rpg it *does* have content to
convert. Scoped together with E4 because the two turn out to be one question: **what validates a
step**.

### 20.1 What shell-1 is, counted (corrected 2026-08-06)

**Counted from `git ls-files`, not from the disk** — the first pass counted 47 challenges with
five missing flags, and both numbers were wrong. See §20.6.

`kevin-cazal/shell-1-challenges` @ `5fc0f7f` (the local clone is at the same commit):

| | |
|---|---|
| Challenges | **42** — 19 in `shell_101`, 23 in `shell_102` |
| Shape | one directory each: `challenge.yml` + `private/{flag.txt.gpg,writeup.md}` |
| `challenge.yml` | name, author, category, description (French markdown), value, type, state, tags, requirements, sometimes hints and files |
| Flags | **42 — one per challenge, none missing**, `shell1{…}`, GPG-encrypted in the repo |
| Writeups | **42**, one per challenge, `private/writeup.md` — instructor material, never imported |
| Hints | 45 |
| Images | 27, declared as `files:` and referenced from the description |
| Prerequisites | a **pure chain**: every challenge but the first takes exactly the previous one |
| Points | 1 to 445, hand-tuned, summing to exactly 9,999 |

The prerequisite chain matters: it is precisely what the parser's implicit linear spine already
produces, so the conversion needs no `requires:` at all.

### 20.2 The finding: the fork's dynamic flags are unused

`ctfd_shell1_flags` implements HMAC-per-team dynamic flags and custom Python validators, and E4
was written as "generalize it into the workshop plugin". But the flag spec it reads lives in
`private/flag.yml`, and **there is no `flag.yml` in the challenge set — not one.** Every live
challenge is a static `shell1{…}` flag, which **CTFd core already handles**.

So E4 is not a port. It is: convert the content, point the instance at stock CTFd, and the fork
plus the plugin become unnecessary. Dynamic flags stay available as a *future* need — per-
participant flags are the answer to copying in a room — but nothing today asks for them, and
building them now would be building for a use that does not exist yet.

### 20.3 The real gap: the platform overwrites authored flags

This is what actually blocks the conversion. `sync_subject.py` sets every exercise's flag to a
generated **validation code** — the interim checkpoint mechanic, where the instructor reads a
code out and the participant types it. shell-1 does not work that way: its flags are the
*answers* (`shell1{exemple}`, `shell1{exemple2}`), discovered by doing the task. The convention has
always allowed for this — `platform.validation_default: checkpoint | tests | flag | review` —
and the sync only implements `checkpoint`.

So the work is:

1. **Authored answers need a home.** Not the markdown: a flag printed next to its exercise is
   not a flag. The natural shape is the one quizzes already use — a sidecar
   `flags.yaml` beside `quiz_answers.yaml`, keyed by exercise id, gitignored or encrypted the
   way shell-1 already encrypts `flag.txt`.
2. **The parser** carries `validation:` per exercise (already a documented field, unimplemented)
   and the linter checks that every `validation: flag` exercise has an entry in the sidecar.
3. **The sync** stops generating a code for those exercises and sets the authored flag instead,
   while `checkpoint` exercises keep today's behaviour. Codes must still never rotate.
4. **Only then** is the content conversion mechanical.

### 20.4 The conversion, mechanically

- Two documents, `shell101.md` and `shell102.md`, one `## ` heading per challenge in directory
  order, each with `<!-- ws: {type: exercise, id: <dir name>, points: N, validation: flag} -->`.
  The chain is implicit, which is what the parser already does — and the four joins that are not
  "the previous one" become explicit `requires:`.
- `hints:` become `<!-- ws: {type: hint} --><details>` blocks; the parser reads them today.
- The 38 images move to `img/` and are referenced relatively — the sync uploads them, and the
  convention forbids hotlinking (§3.8).
- `value:` per exercise, since the points are hand-tuned and a flat default would throw that
  away.
- `writeup.md` stays in the source repo: instructor material, never imported.

### 20.5 Order

- **E4a — DONE 2026-08-06.** `validation: flag` end to end. The parser reads `validation` per
  exercise (falling back to `platform.validation_default`), `load_flags()` reads a `flags.yaml`
  sidecar shaped like `quiz_answers.yaml`, and the linter refuses a flag exercise with no answer,
  an answer matching no exercise, and a mode that is specified but unimplemented. The sync mints
  no code for those exercises, sets the authored answer as the flag, and drops the "ask the
  instructor for a code" note that would otherwise be a lie. Comparison is case-insensitive
  unless the entry says `case_insensitive: false`, which a command or a path needs. Verified
  against a live instance on a fixture carrying both modes: exact answer correct, wrong case
  refused where case matters, right case correct, and the checkpoint exercise still solved by
  its generated code — which is the only one the instructor sheet lists.
- **E4b — DONE 2026-08-06.** `tools/import_shell1.py` converts the challenge directories into
  `content/shell_1`: 42 exercises across two documents, descriptions verbatim, 45 hints as
  `<details>` blocks, 27 images copied into `img/` and re-referenced relatively, points carried
  across, and the answers into `flags.yaml`. No `requires:` anywhere — the chain falls out of
  source order, which is what the upstream graph already was. The importer reads **`git
  ls-files`, not the disk**, so the five stale directories of §20.6 cannot come back; the first
  run imported 47 challenges and five broken ones before that was fixed. Verified on a live
  instance: a wrong answer is refused, the authored answer solves the step, an uppercase
  submission is accepted (case-insensitive by default), a later step stays 403 until its
  predecessor is solved, and **no instructor sheet is written at all** — nothing in this subject
  needs a code.
- **E4c — DONE 2026-08-06.** `content/workshops/discover-linux` composes Shell RPG (starter)
  with Shell 1 (advanced): play the terminal, then use it for real. Both declare
  `runtime.id: v86` — one dist, built from the shell-rpg product because that repo is v86-runner
  plus a welcome screen — and `runtime.params` says which machine to boot. The page hands a
  document *its own subject's* parameters, which is the last piece of D3: Shell RPG's pages
  offer the RPG bundle, Shell 1's offer `shell-1-256M.v86b`, from the same frame and the same
  dist.

  `scripts/workshop_check.py` was generalized while proving it: it names no subject any more,
  reading the expected order from the instance instead, and the same run passes on both
  `discover-linux` and `tic80-double`.
- **Deferred** — dynamic HMAC flags and custom validators, until a session actually needs
  per-participant answers. The fork retires when E4b lands, not when this does.

### 20.6 The five "missing flags" do not exist (2026-08-06)

Worth recording, because the mistake is easy to repeat. The first reading of the challenge repo
found 47 challenge directories and five without a flag — `c05_mkdir`, `d01_cp`, `e02_micro`,
`e04_archivage`, `livrable_1` — and filed it as a content question for the author.

It was not. Those five are **untracked leftovers in the local clone**: `git ls-files` does not
list them, and each is superseded by a numbered directory from the restructure in `f410165`
(`c05_mkdir` → `011_mkdir`, `e02_micro` → `016_nano` — the editor changed from micro to nano —
`e04_archivage` → `018_archivage`, and so on). They were deleted in the repo and left behind on
disk. Counting `ls` instead of `git ls-files` invented a content problem out of a dirty
checkout.

The tracked repo is exactly consistent: **42 challenges, 42 flags, 42 writeups.** Nothing is
missing and nothing is blocked.

Two more things the same look turned up:

- **The Notion solutions page duplicates the repo's writeups, partially.**
  [Solutions Shell-1 CTF](https://epitech-academy.notion.site/Solutions-Shell-1-CTF-38021bfd6dd7804ba0cbdc7d65d2f45f)
  carries 28 writeups (all of Shell 101, the first 9 of Shell 102) against the repo's 42. The
  repo is the complete set; the page is a second, partial copy of solutions — worth knowing
  before anyone treats it as the source of truth.
- **The Shell RPG solutions page is a walkthrough, not flags.**
  [Solutions Shell-RPG](https://epitech-academy.notion.site/Solutions-Shell-RPG-38021bfd6dd78058b3a1fd995f7efce3)
  walks the zones (`cd village`, `talk Kevin.npc`, `mkdir a b c`, the rewards) and contains **no
  flag at all**, which independently confirms §19.6: shell-rpg has no flag-based progression, and
  the thin subject is the right shape. It is good instructor material to point at, though.

## 21. Porting MiniASM / WDR+E (2026-08-06)

`kevin-cazal/miniasm` — a browser VM and IDE for the WDR paper computer's instruction set,
deployed at `lab.epitech.academy/asm/`. Read before proposing anything.

### 21.1 What it is

| | |
|---|---|
| Shape | vanilla ES modules, **no bundler**: `index.html` + `js/*.js`, like `pacman-ghost-ai_runtime` |
| Externals | Monaco 0.45 (jsdelivr) and Blockly 9.3.3 (unpkg), both hotlinked from `index.html` |
| Exercises | **25** — 7 tutorials, 18 challenges, over three categories (arithmetic, comparisons, swaps) |
| Structure | `js/exercises.js`: per exercise `id`, `category`, `type`, `requires[]`, `available[]` opcodes, `unlocks`, and **`tests: [{inputs, expected}]`** |
| Text | `js/lang.js` / `js/lang-fr.js` under `exercises[id]`: `name`, `title`, `goal`, `description`, `hints[]`, `starterCode` — **already French, already per exercise** |
| Progress | `localStorage['miniasm-progress']` = `{completed: [ids]}`, written by `markCompleted()` when the tests pass; editor source per exercise under its own key |

Two things make this different from every runtime ported so far.

**It grades itself.** Each exercise carries input/expected pairs, and the app runs them. This is
the first runtime that genuinely knows whether a step is done — which is exactly what the
advisory `result` message was specified for (§14.6 rule 0: it may light up the submit control,
it may never solve the step).

**It already has a progression, and it is a DAG.** `requires[]` per exercise, plus opcodes
*earned* by finishing exercises (`unlocks: 'ADD'`). The platform's own gating would sit on top of
a gating the app already performs.

### 21.2 What the port needs

- **Runtime.** A `static` case in `tools/build_runtime.sh`, the pacman shape. The two CDN
  dependencies are the only wrinkle: they work fine same-origin under CTFd (no COEP, §18.2), but
  a classroom with no internet loses the editor. Vendoring them is the same trick
  `setup-monaco.sh` already does for pacman.
- **Adapter.** `storageKeys` for §16 persistence (`miniasm-progress` and the per-exercise source
  keys), `focus`, and — new — a **watcher on `miniasm-progress`** that emits `result` when an id
  appears in `completed`. The host and the frame share one origin, so this needs *no change to
  miniasm*, the same way §16 reads tic80's cart.
- **Content.** `lang-fr.js` holds a title, a goal, a description and hints per exercise: an
  importer of the `import_shell1.py` shape can produce a subject mechanically, categories
  becoming documents and `requires[]` becoming `requires:` (the convention already has it, and
  here it is a real DAG rather than a chain).

### 21.3 The one open decision: what does a participant submit?

The app knows the answer; CTFd needs something submittable, and a runtime may never solve a step
itself. Three options, and this is the author's call:

| | How it works | Cost |
|---|---|---|
| **Advisory + checkpoint code** | the pane says "tests pass", the instructor gives the code | works today, unchanged; useless for a self-serve session |
| **Advisory + acknowledgement** | the pane says "tests pass", the participant confirms | honest for self-serve, and the app *already* gates the next exercise, so lying only cheats the liar |
| **App reveals a token** | on success the app shows a per-exercise string, submitted as a flag | real proof, but needs a change in miniasm and a `flags.yaml` |

**Resolved with the user 2026-08-06: the app reveals a token**, and the app may be modified for
it — in a **new repo**, leaving `kevin-cazal/miniasm` untouched.

`kevin-cazal/miniasm_runtime` (private, upstream history preserved) adds one thing: on a passing
run it shows `asm{first 12 hex of HMAC-SHA256(secret, "miniasm:<id>")}`, where the secret comes
from `window.MiniASMTokenSecret`. With no secret it reveals nothing and behaves exactly as
upstream, so the app still runs standalone. 220 upstream tests pass; four new ones pin the
derivation, which is a contract with the platform's importer.

**What it is worth, stated plainly.** The secret is per session, so tokens rotate between cohorts
and last year's answers are worthless. But everything runs in the browser: a participant who
reads the source can compute a token without solving anything. It is a deterrent and a record,
not an exam — the same limit that makes `result` advisory in the first place (§14.6). If scoring
ever has to survive an adversary, that needs a server-side check, which is the deferred runner.

### 21.4 Slices

- **F1 — DONE 2026-08-06.** `tools/build_runtime.sh miniasm` stages a 220 KB dist from the port
  (static, the pacman shape), and `adapters/miniasm.js` hands the app the session secret, reports
  its storage keys for §16, and **watches `localStorage['miniasm-progress']`** — which the app
  writes when tests pass — to emit `result`. Same origin, so the watching needs no change in the
  app either. This is the first runtime to use the advisory path at all, and it uses it as
  specified: the host highlights, the participant submits.
- **F2 — DONE 2026-08-06.** `tools/import_miniasm.py` reads the app's own modules **through
  node** rather than parsing JavaScript, selects French, and writes `content/miniasm`: 25
  exercises over three documents, the app's `requires[]` kept as `requires:` so the two gatings
  agree, points following tutorial/challenge, and each marker carrying `token_id`. The port now
  shows only the exercise title (the user's call), so the statement lives in one place.

  `validation: token` is implemented end to end: the sync mints a per-instance secret once
  (`workshop_token_secret`, never rotated — a participant may already hold a token), derives every
  flag from it, and hands the secret to the frame as a runtime param. `TOKEN_SCHEMES` in
  `sync_subject.py` mirrors `js/token.js`; both sides have tests, and the two agree on
  `asm{a87725cdd409}` for the same input. Verified in a browser against a live instance: the
  secret reaches the app, Monaco loads from `vendor/` with no CDN and no failed requests, and the
  token the app derives for exercise 0 is refused when wrong and **accepted when right**.

  Two bugs found on the way, both in the port: hint markup was removed while the code still
  drove it, and `availableLanguages: {'*': 'en'}` made Monaco fetch an NLS bundle it does not
  ship — a 404 per load, which is exactly what vendoring was meant to prevent.

- **F2b (was part of F2)** — the session secret. The secret belongs to the *instance*, not the repo: the sync should mint one on
  first import, keep it (a rotation would invalidate a token a participant already copied), store
  it as a CTFd config, compute every exercise's flag from it, and pass it to the frame as a
  runtime param — the same shape as instructor codes, which are also generated once and never
  rotated.
- **F3** — a workshop manifest if it composes with anything; on its own it deploys as a
  one-subject workshop.

### 21.5 Making it fit the pane (2026-08-06)

A screenshot from the instance showed what the piece-by-piece checks could not: embedded, MiniASM
did not fit. The fixed 50/50 split squeezed the register and memory tables until **the page itself
scrolled sideways**, the app's language picker sat there in English beside French instructions,
and a `(DEBUG) Jump to exercise` button was on show.

Fixed in the port. The first pass used plain CSS; on the author's call the layout then moved to
**dockview** — the same library `tic80-web-editor` uses, so a participant meets one set of manners
across the platform's editors: drag a divider, drag a tab, double click to maximise. The panels
are the app's existing DOM moved into panes, not rebuilt, so `ui.js` needed nothing beyond being
told when its box changed size (Monaco and Blockly both measure once). The layout is saved per
browser under `miniasm-layout`, which §16 already persists because the adapter reports every
`miniasm*` key.

Default: the vertical split the author asked for — code on the left, the machine beside it, the
exercise beneath the machine, measured at 412/412 in a pane of 825. `initialWidth` on `addPanel`
does not survive the split that creates the group, so the sizes are set afterwards through
`panel.api.setSize` — found by measuring the rendered panes rather than trusting the option.

The rest, in its own stack — no new library for these, because the point of porting a working app
is not to rewrite its UI:

- **Below 1100px the two panels stack** rather than splitting, and each table scrolls inside its
  own block. No horizontal overflow at pane width.
- **`window.MiniASMEmbed`**: `enable()` hides what the platform owns (language picker, exercise
  list, dev jump) and sets the language; `selectExercise()` opens an exercise. Standalone, none
  of it runs.
- **The pane follows the step.** The sync writes a challenge id → runtime exercise id map into the
  runtime params — only it knows both — and the adapter calls `selectExercise` on `init` and on
  every `step`. Opening "Copieur" in CTFd loads *Tutoriel : Copieur* in the pane.

One thing that needed the app's own idiom rather than a clever fix: exercise text resolves **once,
at load**, against the language current then, so setting French afterwards left every statement in
English. The app's own selector reloads after switching, so `enable()` does the same — and only
when the language differs, which is what stops it looping, since the adapter re-runs on the
reloaded frame.

---

## 22. First production deploy — five instances over HTTPS (2026-08-18)

The first deployment is a **demonstration that the platform is generic**, not a workshop session:
five instances, one per subject, on the user's own server. That framing sets the priorities —
breadth over depth, so §11's instructor review queue and the full `provision(repo_url, ref)` of
§10 stay out of scope, and what gets built is the layer Phase 0 deliberately skipped
(`docker-compose.yml:4`, "No nginx yet").

| Instance | Subject | What it demonstrates |
|---|---|---|
| pypong | `content/pypong` | the baseline: one subject, TIC-80 beside the instructions |
| pacman | `content/pacman` | a subject bringing its own runtime |
| santa | `content/santa_shooter` | the real workshop: four parts, bonuses, a free-choice chapter |
| discover-linux | `content/workshops/discover-linux` | multi-subject composition, starter gating, v86 |
| miniasm | `content/miniasm` | a self-grading runtime: `validation: token`, prerequisite DAG |

### 22.1 Decisions

- **One subdomain per instance, CTFd at `/`.** No `APPLICATION_ROOT`, no path rewriting, nothing
  in the plugin to make relative. The names come from `ealab.duckdns.org`, which the user already
  points at `<server-ip>`, not from `lab.epitech.academy`, where they have no subdomain rights.
- **That domain resolves as a wildcard**, verified 2026-08-18: every sub-subdomain answers,
  including names nobody configured. So there are no DNS records to add — but a request for any
  unclaimed name still arrives, and nginx would hand it to whichever vhost loaded first, making
  one instance the accidental answer for the whole namespace. `deploy/nginx/default-server.conf`
  is the catch-all that refuses those: `return 444` on port 80, `ssl_reject_handshake` on 443,
  rather than presenting some other instance's certificate.
- **Production publishes on 908x, development on 808x.** The two ranges are deliberately disjoint.
  Both bind to loopback, so a production stack started on a machine that already runs the dev
  instances would otherwise be proxied to whichever got the port first — which is exactly what
  happened while testing the vhosts, and it looks like a working deploy until you read the page.
- **nginx on the host, one vhost per instance, generated.** Templates in `deploy/nginx/`, rendered
  by `tools/provision.py` from `deploy/instances.yaml`. Certificates come from
  `certbot certonly --webroot`, never `--nginx`: certbot editing a generated file and the
  generator overwriting certbot's edits is a loop with no stable end.
- **Instances publish to `127.0.0.1` only** (`WS_BIND`, defaulting to loopback). A published port
  on a public host is a plain-HTTP way past the TLS vhost, and plain HTTP is not a weaker version
  of this application, it is a broken one — see §22.2.
- **User mode, scoreboard kept, one shared registration code.** The registration code is the only
  thing standing between an instance on the open internet and strangers filling the database, so
  `provision.py setup` applies it as a step of its own rather than trusting the wizard.
- **Per-instance database and Redis, not shared.** Five stacks cost about 1.2 GB of RAM, which the
  server has; sharing would save containers and buy a single point of failure across all five, in
  a deploy whose entire purpose is to look like five independent deployments.
- **No `docker-compose.prod.yml`.** Production and development differ only in values, so the
  existing file is parameterized instead. A Compose overlay could not have done the main job
  anyway: overlaid `ports` entries are appended, not replaced, so the `0.0.0.0` publish would have
  survived alongside the loopback one.

### 22.2 HTTPS is a functional requirement, and this is now measured

MiniASM derives its completion tokens with `window.crypto.subtle`, and v86 registers a service
worker; neither exists outside a secure browsing context. Development never noticed because
`localhost` *is* a secure context. On a public plain-HTTP hostname, every `validation: token` step
becomes unsolvable — which would read as a content bug, not a transport one.

`scripts/secure_context_check.js` pins this down in a real browser: same dist, same hostname, once
over https and once over http. Over https the token derives to exactly the flag the sync computed
server-side; over http `crypto.subtle` is `undefined` and `tokenFor` returns `null`. The check is
part of the deploy, not an afterthought.

### 22.3 What was fixed to get here

Found by auditing the code against a public deployment rather than a laptop:

- **`SECRET_KEY` was never persisted.** CTFd generates one into `/opt/CTFd/.ctfd_secret_key`
  (`CTFd/CTFd/config.py:67`) — the image layer, not a volume. It survives `docker restart` and not
  a recreate, so any compose edit would have logged out a room mid-session. Now injected per
  instance from `deploy/secrets.yaml`.
- **`REVERSE_PROXY` was unset**, so `ProxyFix` never installed and every participant shared the
  proxy's IP for rate-limiting purposes.
- **The workspace endpoint parsed before it measured.** `workspace.py` checked the 512 KB cap
  after `request.get_json()` had already materialized the body, and Flask sets no
  `MAX_CONTENT_LENGTH`, so one authenticated POST could exhaust a `WORKERS=1` container. It now
  reads the body bounded off the stream, which also covers a client that understates its
  `Content-Length`, and carries a per-user rate limit.
- **Two endpoints were missing the guards their pages have.** `graph.py` and `page.py`'s
  `step_body` had only `@authed_only`, while `/workshop` has `@during_ctf_time_only` and
  `@check_challenge_visibility`. The DAG — every challenge name and edge — was readable before a
  CTF opened and with challenge visibility set to admins only.
- **`build_runtime.sh` could not resolve its own pins on a fresh machine.** Every subject pins its
  runtime by short commit sha, and git accepts a sha in neither `clone --branch` nor
  `fetch <ref>`. It worked only where a checkout was already cached — i.e. never on the server.
  Pinned builds now clone with history; unpinned ones keep the shallow path.
- **The mirror published an open 8 GB cache on `0.0.0.0`.** Loopback by default now. For this
  deploy it stays off entirely: the v86 bundle's default source is already an HTTPS CDN, and an
  HTTPS page cannot fetch the mirror's `http://host:8090` URL anyway — mixed content.

### 22.4 The tool

`tools/provision.py` is the narrow, manual half of §10: no repo URL, no ref, just the content
already vendored under `content/`. `render` → `up` → `setup` → `runtimes` → `sync`, each safe to
re-run and each able to take instance names. What it buys is that five instances stop being five
hand-run wizards and five hand-edited vhosts — the shapes that drift apart and cannot be redone
identically. Secrets are minted once into `deploy/secrets.yaml` and read back forever after,
because rotating a session key or a database password mid-deploy is how an instance breaks.

Titles come from the content (`project.name`, `workshop.name`) rather than being restated in the
manifest, so there is no second copy of a name to drift.

The runbook is `docs/DEPLOY.md`.

### 22.5 It shipped — 2026-08-19

The five instances are live over HTTPS on `*.ealab.duckdns.org`, running from `/srv/workshop` on
`<server-ip>`. 147 challenges in total: pypong 15, pacman 19, santa 36, discover-linux 48,
miniasm 29. The runbook survived contact with the server; three things it did not anticipate are
now folded back into it.

**The box was not empty.** Its nginx already served the bare `ealab.duckdns.org` — legacy static
apps and redirects to `*.epiboost.fr` — and that vhost held the `default_server` claim. Nothing
collided, because everything we add lives on sub-subdomains, so the fix was to take the claim
away from the stock welcome block rather than to disable the file. The legacy routes still work.
An earlier read of the server had suggested no named vhost was configured at all; it was wrong,
and the cost of believing it would have been reloading nginx over someone's working site.

**Nothing was renewing the certificates.** The server's own `ealab.duckdns.org` certificate had
been expired for seven months. certbot was installed under `/opt`, which ships no timer, and no
cron entry existed either — so `certbot renew --dry-run` passing meant only that renewal *would*
work if anything ever invoked it. A `certbot-renew.timer` now does, twice daily, with the nginx
reload hook attached. The old certificate was renewed in passing and its renewal switched from
the nginx authenticator to the same webroot the five use.

**The premise held in production.** Over HTTPS on the real hostname, MiniASM's exercise 7 derives
`asm{66a9c3749b30}` in a real browser, and that string is one of the 25 flags the sync wrote into
the instance. This is the check worth keeping: it is the one that fails silently — no error, no
broken pane, just a token field that never fills — if the certificate is ever out of the path.

Two operational facts worth carrying: the CTFd image builds once and the remaining four stacks
come up in seconds off the layer cache, and `certbot renew` run by hand sleeps up to eight
minutes first unless given `--no-random-sleep-on-renew`, which is indistinguishable from a hang.

### 22.6 The backup rehearsal — 2026-08-19

Done before anyone real touched the instances, which is the only time it is worth anything. Two
things it caught.

**The documented dump command could not fail visibly.** `mysqldump ... | gzip > file` exits 0 when
mysqldump dies, because gzip succeeded on the empty stream it was handed, and the result is a
20-byte archive that `gzip -t` calls valid. A nightly loop would have written five of those and
reported success. `set -o pipefail` turns it into exit 2; the runbook now leads with that line
rather than mentioning it.

**A restored database is invisible until Redis is flushed.** CTFd reads config through the cache,
so restoring into a fresh instance leaves it redirecting everything to `/setup` while the `config`
table says `setup = 1` — a symptom that points at the restore having failed when it did not.
Proven directly rather than inferred: changing `ctf_name` with `mysql -e` changed nothing the
browser saw until `FLUSHALL`, after which it took effect with no restart. The flush alone is
sufficient, and the restart people try first is what makes this look intermittent.

What passed: a re-dump of the restored database was byte-identical to the original, all 27 uploads
compared equal both from the live tree and out of the tar, and a throwaway sixth stack built from
the backup alone accepted the admin password from `deploy/secrets.yaml` and served all 48
challenges plus an uploaded image. `--single-transaction` produces a byte-identical dump on an idle
instance, so it is now unconditional.

Backups write to `/srv/backups`, outside the repo: a dump carries the whole config table, which
means the registration code, the token secret and every password hash.

The procedure is now `tools/backup.sh` rather than a loop in the runbook, driven nightly by
`ctfd-backup.timer`. Writing it as a script is what made the failure handling possible at all: a
dump goes to a `.part` file and is renamed only once it has both exited cleanly and ended with
mysqldump's completion trailer, so a file that exists is whole, and a run with any failure in it
exits non-zero and shows up as a failed unit. Rotation keeps the *N most recent* copies rather
than deleting by age — an age rule throws away the last good backup on exactly the day a fortnight
of quiet failures makes it the only one left. Verified on the server: normal run, rotation from 21
copies down to 14, a stopped container, and a running container whose dump is rejected — the last
one being the case that used to produce a valid empty archive and a success message.

Still missing: nothing tells anyone when a run fails. systemd marks the unit failed and the
journal has the reason, but that is a pull, not a push.


## 23. The answer sheet, and the role tier CTFd does not have (2026-08-24)

The instructor standing in the room cannot see the answers. That is the whole problem, and it is
logistical rather than technical: the answers exist, in three places, none of them reachable from
where they are needed.

| Mode | Where the answer lives | Same on every deployment? |
|---|---|---|
| `flag` | `Flags.content`, authored in `flags.yaml` | **yes** |
| `checkpoint` | `Flags.content`, `secrets.token_hex(3)` minted at sync | no, per instance |
| `token` | `Flags.content`, HMAC of the instance's `workshop_token_secret` | no, per instance |
| quiz | `QuizChallengeModel.quiz_answers` (JSON column) | yes |
| `ack` / `info` / `rating` | nothing — no answer exists | n/a |

So "are the flags fixed or per-deployment" has no single answer, and **the two that vary do so on
purpose**: `tools/sync_subject.py:250` mints the token secret once per instance so a token copied
at last year's session is worthless at this one, and checkpoint codes are read back on re-sync for
the same reason — rotating them would invalidate work that was genuinely done. Making them fixed
across deployments was considered and rejected: it would mean one leaked sheet unlocks every future
session, which matters more now that the subject repos are public (§ public repo split). The
authored `flag` answers already are fixed, because they are the exercise.

The view sidesteps the question entirely by reading the **live instance's database**. It shows what
*this* instance will accept, whichever mode produced it.

### 23.1 Why CTFd cannot help here

CTFd's admin panel has no all-challenges answer listing. Flags appear one challenge at a time on
the edit form (`CTFd/admin/challenges.py:49`) and quiz answers appear nowhere at all, because they
are a plugin column rather than a `Flags` row. Until now the checkpoint sheet existed only as
`instructor_codes.<subject>.yaml` on the maintainer's laptop.

### 23.2 Roles: admin > instructor > attendee does not exist, and cannot without a core edit

`Users.type` is a two-value polymorphic column — `user` and `admin`, nothing else
(`models/__init__.py:434`, `Admins` at `:616`). `is_admin()` is a literal `user.type == "admin"`
(`utils/user/__init__.py:203`), the admin user form hardcodes exactly two choices
(`forms/users.py:186`), and every `/admin` route is `@admins_only`. Brackets are scoreboard
divisions and carry no permission meaning. There is no group system to extend.

A third tier therefore has to live **entirely in the plugin**: `Users.type` stays `user`, a
plugin-side role marks the account, and the plugin ships its own `instructors_only` decorator
guarding its own routes. `/admin` stays admin-only and CTFd core stays pristine. Two consequences
to design around when that lands, neither of them a bug:

- an instructor account is a normal user, so it appears on the scoreboard unless `hidden=True`;
- locked challenges are filtered for non-admins (`api/v1/challenges.py:196`), so an instructor's
  view of the whole subject has to come from the plugin's own pages, not the challenge list.

**Decision: not yet.** The role ships with instructor-led mode (§11), because a login tier is only
worth its cost once there is a console behind it. Until then the answer sheet is `@admins_only`,
and switching it over is a one-line decorator change.

### 23.3 What was built

`plugins/workshop/answers.py` + `templates/workshop_answers.html`, modelled on the feedback report
(§17) down to the admin menu bar entry. Two routes, `/admin/workshop/answers` and `.csv`.

Two tables on one page, because they are used together — you look up where somebody is stuck, then
you look up the answer to that step:

- **The answers**, grouped by part in board order: step, kind, answer, points, solve count. Answers
  can be blurred with one button, for when the screen is projected.
- **Who is where**, *least progress first* — the table exists to find whoever needs a hand. Per
  participant: a progress bar over the counted steps, the first unlocked step they have not solved
  ("stuck on"), and their last solve. Built from one pass over `Solves` rather than a query per
  user. Admins and hidden accounts are excluded, since an instructor account is otherwise
  indistinguishable from a participant.

### 23.4 The mode had to become data

Labelling a row honestly turned out to need a sync change. `checkpoint` and `flag` are both a single
static `Flags` row, and telling them apart by the shape of the stored string is a guess. So the sync
now records `{challenge_id: mode}` into a `workshop_validation` config key — merged, not
overwritten, so a multi-subject workshop accumulates one map across its syncs (verified on
discover-linux: 1 checkpoint from shell_rpg + 42 flags from shell-1 = 43 entries).

An instance synced before that key existed still renders, by inference: token steps are named
exactly in the runtime config, a quiz carries its kind on the row, and only checkpoint-vs-flag falls
back to the six-hex-characters heuristic. Those rows are **marked as inferred** on the page rather
than presented as fact, with a note saying a re-sync replaces the guess. The five production
instances need a re-sync to get authoritative labels; they are correct either way.

### 23.5 Covered by the suite

Ten checks in `scripts/phase2_validate.py` (145 total, up from 135): neither route is readable by a
participant, a live checkpoint code appears under the label that says whose it is, a freshly synced
instance infers nothing, the sync wrote the map the page reads, the quiz answer the API still
refuses to hand out is on the page, the participant is in the progress table and the admin is not,
and the CSV carries the same rows.

One thing the page deliberately does not repeat: `case_insensitive`, which
`tools/sync_subject.py:354` writes on every flag it creates. Stating the default on all forty-three
rows says nothing, so the note marks the exception instead — a flag added by hand that is
case-sensitive, or a freeform quiz whose content asked for it.


## 24. The part introduction was withheld with the step (2026-08-25)

Reported from the room: on the pypong workshop page the section `## Faire bouger le pad` — the
bullet list, the `padx` code block and the sentence after it — did not appear at all. The exercise
that follows it says *"s'inspirer du code précédent (`btn`, `padx`)"*, so the participant was being
pointed at something the page never showed them.

### 24.1 Where it went

Nowhere: the content was in the database the whole time. `tools/sync_subject.py:134` fences a
step's inherited prose in `<!-- ws:context -->` so the board keeps a self-sufficient statement while
the page lifts the prose out and prints it above the steps (§13). The page did the lifting inside
`_body()`, and `_steps()` builds a body only for a step the participant has unlocked:

```python
"body": _body(c, user) if (unlocked or is_solved) else None,
```

The lead is only ever attached to the **first** step of a part, and that step is locked for exactly
as long as the participant has not reached the part. So the whole introduction disappeared, and came
back the moment the step unlocked — which is why it survived every walkthrough that started by
solving things.

### 24.2 The rule it broke

The module already states the policy (`page.py:23`): a locked step hides its **statement**, its
hints and its quiz, and still shows its **title**, because "the workshop syllabus is not a secret".
A part introduction is syllabus, not statement. Tying it to `body` put it on the wrong side of a
line the file had already drawn.

The fix is to compute it separately — `_lead(challenge)`, called for every step regardless of state
— and have `_part_lead()` read it from the step rather than from a body that may not exist. Locked
statements are unaffected.

### 24.3 Audited across every subject, not just the one reported

`scripts/content_visibility_check.py` registers a participant who has solved nothing (the worst
case: nearly everything locked) and checks, per subject, that every authored prose block is on a
page, that every step statement reached CTFd, and that both appear in the order they were written.
All six subject directories pass — pypong, pacman, santa_shooter, shell_rpg, shell_1, miniasm.

Two traps it had to grow, both of which first showed up as false alarms:

- **Bulleted prose.** Most workshop prose is written as `- ` bullets, which render as `<li>`. A
  probe that keeps the marker matches the markdown and never the page, so every bulleted block
  reads as missing.
- **Repeated names.** `starter1.md` has three steps called "Sprite" and two called "Input"
  (CLAUDE.md), and "Collision" is both a part heading and a step title. Keying challenges by name
  drops all but the last of each; checking order by searching for titles finds the first "Sprite"
  three times. The catalogue is keyed by id and the order check reads the `step-<id>` anchors.

Four checks in `scripts/phase2_validate.py` pin the behaviour: a participant with no solves gets the
introduction and the prose the next exercise refers to, while the step it introduces still hands out
no statement.

### 24.4 And then made foldable (2026-08-25)

Asked next: why is the prose not collapsible, when every step is? Because it is not a step — the
accordion earns its keep on steps because there are many siblings in sequence and most are done or
locked, and folding is what keeps the current task findable. A part introduction is one per part
and always relevant.

The sizes argued back. There are only nine prose leads across all six subjects, median 205
characters, but three carry a code block and run 24 to 28 lines — `pacman/a2-etat-patrol` (1851
chars), `pypong/pad-direction` (750, the one reported) and `santa_shooter/refactor-joueur` (719).
Those push the first step below the fold.

So: `<details open>`. Foldable, never folded on arrival — §13's "do not make people click to read"
is intact, and a click to reach the code an exercise says to reuse is the bug in §24 reintroduced
by hand. Shutting one sticks, in `localStorage` keyed by page and part name, so it does not spring
open again on the next visit; storage is wrapped in try/catch, since a private window refuses it
and the fold must still work for that session.

Uniform rather than thresholded: every lead folds, including the six-character one. A rule of
"there is a fold control when the text is long enough" is one more thing for a 15-year-old to
work out, and the constant would have no principled value.
