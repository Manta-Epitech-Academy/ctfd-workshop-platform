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
`/challenges`, just no longer the first thing a participant sees — and since §30, no longer in a
participant's navbar at all, nor at the end of a Parcours node); the Parcours graph becomes secondary — the inline steppers cover progress for a linear subject, and
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

**Decision at the time: not yet.** The role was to ship with instructor-led mode (§11), because a
login tier is only worth its cost once there is a console behind it. It shipped on its own instead,
as the supervisor tier of §32, exactly in the shape this section describes: `Users.type` stays
`user`, `hidden=True`, a plugin table names the account, and the plugin's own decorator guards the
plugin's own pages. The decorator swap on the answer sheet was the one-line change predicted here.

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

**A quizset shows its questions one by one (2026-09-21).** `_quiz_answer` had a branch for every
quiz kind except `quizset` — the one the subjects actually use — so every such row read "nothing to
answer", which is the opposite of this page's whole purpose. A quizset is several questions in one
step, each graded by passing its own dict to the same per-kind grader (`quiz.py:_grade_quizset`),
so the renderer takes the kind as an argument and the rows reuse it. Each question is numbered the
way the "look again at question 2" message numbers them, and carries what its letters *say*, read
from `quiz_spec.items`: "B" alone would still send an instructor to the subject to find out what B
was, which is the lookup this page exists to spare them. Those labels blur with the answers under
"Hide answers" — only the correct options are listed, so leaving them readable on a projected
screen would give the answer away just as plainly. The CSV keeps one cell per step
(`1. A, B, D · 2. B`). A step holding a single question is not numbered.

Not covered by `phase2_validate.py`: `content/pypong` has no quizset to assert against. Verified
instead against a live instance carrying four of them, page and CSV.

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

---

## 25. Two usages, one platform: instructor-led and self-serve (2026-08-25)

The platform has always claimed two usages — a room with an instructor, and somebody working alone
at home. The word *mode* appears in three places already: `platform.mode_default` in every
`subject.yaml`, §10 and §11 of this plan, and the `ws:resume` region the parser has extracted since
the first commit. **Nothing downstream reads any of them.** There is no mode; there is a set of
places where one was expected.

### 25.1 The finding: self-serve does not work today

| Subject | Exercises | Validation |
|---|---|---|
| pypong | 9 | `checkpoint` |
| pacman | 16 | `checkpoint` |
| santa_shooter | 31 | `checkpoint` |
| shell_rpg | 1 | `checkpoint` |
| shell_1 | 42 | `flag` |
| miniasm | 25 | `token` |

57 of 124 exercises are validated by a code the instructor reads out (§3.3b). Nobody outside a room
can complete a single step of pypong, pacman or santa_shooter, and on discover-linux the one
`checkpoint` step of shell_rpg is the gate standing in front of all 42 of shell_1 — one code
withholds an entire subject. shell_1 (`flag`) and miniasm (`token`) already work unattended,
because their answers are self-graded by construction.

So four of the five production instances are instructor-only, whatever their URL suggests. That is
the problem; the rest of this section is what falls out of fixing it.

### 25.2 What the mode governs — and what it must not

| Surface | instructor_led | self_serve |
|---|---|---|
| a `checkpoint` step | the code, read out in the room | one button, self-validated |
| the validation note under a step | "ask the instructor for the code" | must not say that |
| registration | a shared code off a slide | open, no code |
| instructor console (§11 queue, §23.2 role) | eventually, yes | nothing to review |

Unchanged by the mode, deliberately: the prerequisite DAG, points, `optional`/`free` semantics,
`flag` and `token` answers, the runtimes, workspace persistence, hints and their costs, the rating
step, the answer sheet. Naming that list is what keeps this one switch instead of two products.

### 25.3 The mode is instance-wide data, flippable at runtime

A CTFd config key **`workshop_mode`** ∈ `{instructor_led, self_serve}`, read at render and at
submit time, never baked into content. Written at provisioning from `deploy/instances.yaml` and
flippable afterwards from `/admin/workshop/settings`, beside Feedback and Answers.

- **Default `instructor_led`. Resolved with the user 2026-08-25.** The workshop is an instructor-led
  thing by nature; an instance that self-validates is a deliberate act, so it is the one that has to
  be asked for. Precedence: `instances.yaml` `mode:` > the subject's `platform.mode_default` >
  `instructor_led`.
- **One instance, one mode. Resolved with the user 2026-08-25.** Not per subject and not per part:
  §10 already puts one instance behind one session, and the mode describes the room, not the
  content. A second mode costs a second instance, which is what §25.9 buys.
- **Flippable rather than sync-baked**, because the operation that actually matters is the one
  *after* a session: the instance stays up and becomes self-serve for whoever finishes at home. As a
  config key that is a toggle and nothing is invalidated — the DAG, the solves and the codes all
  stay where they are. As a sync-time bake it would be a re-import.

### 25.4 What a `checkpoint` step does in each mode

The step keeps its code either way; the mode decides whether the code is *required*.

- **instructor_led** — a text field, compared to the instance's code. Today's behaviour, untouched.
- **self_serve** — a single button, the `ack` control that already exists for the intro step.

§21.3 settled the honesty question for miniasm's advisory case and it applies unchanged: the DAG
still gates what comes next, so lying only cheats the liar. The code stays in the database while
self-serve — never rendered, never sent to the client — which is what makes a flip back to
instructor-led possible. Revealing it instead would make the switch one-way.

### 25.5 Mechanically: a new quiz kind, and a migration

**Resolved with the user 2026-08-25: option A, change the challenge type.**

A checkpoint step is a `standard` challenge whose answer is a `Flags` row (`4b279a` and friends,
minted per instance by the sync). `Challenges.type` decides which class handles a submission, and
CTFd's standard class knows nothing about workshop modes. So the step has to be handled by plugin
code — and the plugin already has exactly the right class. `quiz` (§12) is plugin-owned, keeps its
answers in `quiz_answers`, a JSON column excluded from `read()`, and rides the standard
`/challenges/attempt` endpoint, which is what keeps rate limiting, CTF-time, prerequisite checks,
`Submissions`/`Solves` rows and the plugin hooks CTFd's rather than ours.

So: **`quiz_type: "checkpoint"`**, one branch in `attempt()` — accept in self-serve, compare to the
stored code in instructor-led, case-insensitive as the flag was.

The cost is a migration on the four already-deployed subjects, and it is the reason the decision
needed taking rather than assuming:

1. `UPDATE challenges SET type='quiz'` on the step's row,
2. `INSERT INTO quiz_challenge_model (id, quiz_type, quiz_answers)` carrying the flag's content over,
3. drop the now-unused `Flags` row.

The challenge id does not move, and solves, points, progress and prerequisites all reference the
id — **so nobody loses work**. The sync does it, idempotently, as an explicit step. The lazy version
of "change a step's type" is delete-and-re-import, which would take every solve on the instance
with it; that is exactly what this must not be.

Rejected — **option B**, leave the rows `standard` and add a plugin route that writes the solve
directly when the button is pressed (the `_store_rating` precedent). No migration, but it reimplements
rate limiting, CTF-time, the prerequisite check and the attempt record, and every one it forgets is a
hole: solving a locked step, solving twice, solving after the end date. One migration written once
beats a second submit path maintained forever.

Two consequences worth writing down: `answers.py` reads a checkpoint answer from `quiz_answers`
rather than `Flags` after this, and §23.4's checkpoint-vs-flag *inference* fallback disappears — a
migrated row is self-describing.

### 25.6 The note stops being baked into the content

`sync_subject.py:50` appends *"validate the exercise with the code given by the instructor"* into the
stored description at import. A sentence in `Challenges.html` cannot follow a toggle, so today it
becomes a lie the moment the mode changes, and correcting it means a re-sync. It moves to the step
template, chosen per kind and per mode at render time. Same class of mistake as §23.4 ("the mode had
to become data") and §24 (content on the wrong side of a line). `TOKEN_NOTE` moves with it and
becomes English on the way — it is platform copy, and CLAUDE.md puts platform copy in English.

### 25.7 `ws:resume` becomes a summary, not a swap

**Resolved with the user 2026-08-25: option (a).** Convention §3.4 says instructor-led renders *only*
the resume region and self-serve renders the full prose. It was written before §13's single page and
before §24, and it is precisely the shape §24 just fixed: the participant in the room is the one most
likely to be pointed at a sentence the page is not showing them. pypong's own resume says *"s'inspirer
du code précédent (`btn`, `padx`)"*, and the code it means is in the prose the swap would hide.

What the content actually holds: pacman authored one for all 16 steps and they are a real
condensation (2.2 KB against 18.4 KB of statement); pypong wrote 2 for 9 steps, and those are
*longer* than the text they would replace; the other four subjects have none. A mode switch that
visibly changes one subject and a fifth of another is not worth a rule about hiding statements.

So the region is rendered in **both** modes, as a foldable box at the top of the step, `<details
open>` with the fold remembered — the treatment §24.4 settled for part introductions. The author's
bullets become a summary instead of a substitute, and 18 well-written resumes stop being parsed into
nothing. The box label is platform copy, so it is English ("In short") over French content.

### 25.8 Registration follows the mode

**Resolved with the user 2026-08-25: self-serve needs no registration code.** A code exists to keep an
instance on the open internet from filling with strangers, which is the correct goal for a session and
the wrong one for a workshop meant to be found. So: `instructor_led` keeps `registration_code`;
`self_serve` sets registration public with no code, e-mail verification still off (there is no mail
server, §22.1). A self-serve instance holds a display name and a password hash, and nothing else worth
protecting.

### 25.9 A self-serve instance for every workshop — `self.<name>.ealab.duckdns.org`

**Resolved with the user 2026-08-25.** Ten instances, not five: one instance one mode, so this is what
a second mode costs. The five existing names keep their content, their codes and instructor-led mode;
`self.pypong`, `self.pacman`, `self.santa`, `self.discover-linux` and `self.miniasm` are the same
content in self-serve.

Checked before promising any of it (2026-08-25):

- **DNS** — the DuckDNS wildcard answers at any depth, not just one label: `self.pypong` and even
  `nope.deep.ealab.duckdns.org` resolve to the server. No records to add, and `default-server.conf`
  still refuses the names nobody configured.
- **Certificates** — one per name, HTTP-01 webroot, as in §22/DEPLOY.md §4. A wildcard certificate
  would not have covered a two-label-deep name anyway. Five more against Let's Encrypt's 50-per-week
  limit on `ealab.duckdns.org`.
- **Capacity** — 11 GB of RAM with 7.8 free and 22 GB of disk free; five stacks are ~1.2 GB. The
  runtime dists, including the v86 bundles, are served off disk by the host nginx from one shared
  path, so a second instance of discover-linux does not copy them.
- Ports 9085–9089, continuing the 908x range that keeps this deploy clear of the box's other tenants.

The answer sheet stays admin-only on both: in self-serve nobody needs the codes, but the mode can be
flipped back, so the sheet keeps listing them and the page says which mode the instance is in.

### 25.10 Slices

- **M1** — `workshop_mode` config, the admin toggle, `mode:` in the manifest, registration derived
  from it. No participant-visible change yet.
- **M2** — `quiz_type: checkpoint`, the sync migration, the note moved to render time. This is what
  makes self-serve real.
- **M3** — the resume box (§25.7).
- **M4** — the five self-serve instances (§25.9).
- Later, unchanged by this: the instructor role tier (§23.2) and the review queue (§11), both behind
  `mode == instructor_led`, and both still the next real work after this.

---

## 26. Syncing from the subject repo, from the admin panel (2026-08-26)

The workflow this is for, stated by the user: **clone a subject repo, edit the content, push to
GitHub, open the admin panel, press Sync.** No ssh, no `provision.py`, no second copy to keep in
step by hand.

That makes the **subject repo the single source of truth** for an instance's content, which is a
change of direction worth naming: until now the platform imported `content/<subject>` vendored in
this repo, and the subject repos were where the author worked. §26 inverts it — the vendored copy
stays for the CLI, the validation suite and offline provisioning, and the *instance* follows the
repo.

### 26.1 What makes it possible now

The public split of 2026-08-24 did it. All six subjects are public repos with a convention-2.0 root
(`subject.yaml` beside the markdown), so an instance can fetch one with no credentials at all.
Verified 2026-08-26: `api.github.com/repos/kevin-cazal/pypong_subject/tarball/main` → 6.8 KB,
unpacking to `pypong_subject-main/`. `content/upstreams.yaml` still points at the retired private
`*_new` repos and describes the world before that split; it is re-pointed as part of this.

Three facts about the container, all checked rather than assumed:

| | |
|---|---|
| worker class is **gevent** | a long job runs as a greenlet and does not starve the instance, and it may call the instance's own API |
| **no PyYAML, no gpg**, but `requests`, `tar` and `cryptography` are there | the missing pieces are vendored, not installed into the image |
| `PRESET_ADMIN_TOKEN` is in the environment of every production container | the job authenticates to itself with a token, no password handling |

### 26.2 The page

`/admin/workshop/sync`, admin-only, beside Answers and Workshop mode:

- **the source** — `workshop_source` config, `{repo, ref}`, written at provisioning from
  `deploy/instances.yaml` and editable here;
- **where it stands** — the commit last imported against the tip of the ref right now, so the page
  says whether pressing Sync will do anything;
- **Sync** — fetch, lint, import, record the commit. The log is **polled**, not streamed: nginx
  buffers SSE unless told otherwise, and polling has nothing to configure.
- **the summary** — `N created / M updated`, with `created` called out on an already-synced
  instance, because that is the signature of a renamed slug leaving a duplicate behind.

Order matters: **lint the fetched content before touching the instance.** A broken push must fail
on the page with the linter's message, not half-way through an import. (`sync()` calls `sys.exit`
on lint problems, which in-process would take the greenlet down with a `SystemExit` nobody
reports — so the page lints first and only then calls it.)

### 26.3 Answers stay in the repo, and the passphrase never leaves the browser

**Resolved with the user 2026-08-26.** Two subjects ship encrypted answers — `pypong_subject`
(`quiz_answers.yaml.gpg`) and `shell-1_subject` (`flags.yaml.gpg`).

The first idea was to let the instance supply answers it already holds, since every answer it ever
imported is in its own database. The user killed it, correctly: **edit an encrypted flag and push,
and that sync would keep the old answer and say nothing.** Two sources of truth, silently drifting.
So the repo wins, always, and the encrypted file has to be opened somewhere.

Not in the container. It has no `gpg`, and the obvious substitute does not work: PGPy 0.5.4 — the
only pure-Python OpenPGP implementation on PyPI — calls `cryptography.utils.register_interface`,
removed in cryptography 37, and the image ships 45. Vendoring an old cryptography beside it would
shadow a security library for the whole CTFd process, which is not a trade worth making for an
answers file.

**So the admin's browser decrypts it.** The job fetches the tree, notices an unopened `.gpg`, and
parks; the page hands the blob to `openpgp.js` (vendored as a dist, like a runtime), asks for the
passphrase, decrypts locally, and posts back the plaintext the importer needs. Verified 2026-08-26
against a file written by `tools/answers.sh`'s exact command
(`gpg --symmetric --cipher-algo AES256`): OpenPGP.js 6.2.2 decrypts it and rejects a wrong
passphrase with *"Modification detected"*. It needs WebCrypto, which needs a secure context —
already a hard requirement here for other reasons ([[https-is-required-by-two-runtimes]]).

This is stronger than what the user asked for. "Ask rather than store" was about not keeping the
passphrase on the instance; this way **the passphrase never reaches the server at all**, and the
`.gpg` files, `decrypt.sh`, `encrypt.sh` and `tools/answers.sh` are all untouched. What does reach
the server is the plaintext answers — which it is about to store in its own database anyway.

Without them the sync **refuses**: it never imports new content against old answers. The page
remembers *that* a source needs a passphrase (`needs_passphrase` on the last-sync record), not the
value, so the field is there the next time.

### 26.4 A workshop is a repo too, and a subject is a workshop of one

**Resolved with the user 2026-08-26: `kevin-cazal/discover-linux_subjects`**, a public wrapper
holding `shell-rpg_subject` and `shell-1_subject` as **submodules** with `workshop.yaml` at its
root. `git clone --recursive` then gives one working copy with both subjects in it, which is the
workflow this section exists to serve.

Two facts, verified 2026-08-26 on this repo's own tarball:

- a GitHub tarball **does not** carry submodule contents — `CTFd/` arrives as a single empty
  directory;
- the API **does** report the pin: `contents/CTFd` returns `type: submodule`, its `sha`, and its
  git URL.

So the platform follows the pins itself, without git: fetch the wrapper's tarball for
`workshop.yaml`, read each submodule's sha with one API call, fetch each subject's tarball at that
sha. Reproducible, and no git binary in the container.

`workshop.yaml` gains a per-subject `ref:` that overrides the pin — `ref: main` while a subject is
being actively edited (one push, always the tip), the submodule pin when the workshop is frozen
for a session. The footgun is worth stating in the wrapper's README: after editing a subject and
pushing it, the wrapper still points at the old commit until the submodule bump is pushed too.

The shape this buys: **`workshop_source` is one thing.** The plugin tells a subject from a
workshop by what it finds at the root of the fetched tree — `subject.yaml` or `workshop.yaml` —
so a single-subject instance is a workshop of one and there is no second code path and no second
slice. The existing `path:` entries keep working for the CLI; the fetcher materializes a workshop
into the tree layout `sync_workshop.py` already expects, so that tool does not change.

### 26.5 What the container gets, and what it deliberately does not

Two read-only additions: `./tools` mounted at `/opt/workshop/tools`, and the vendored
dependencies the image lacks — PyYAML for the parser, `openpgp.js` for the page — built at deploy
time by `tools/build_vendor.sh` and gitignored, exactly like `plugins/workshop/runtimes/`.

Not given, on purpose: **git, and any write access to a checkout.** The instance is a read-only
consumer of GitHub and of this repo. That is what keeps the design boring, and it is why the
button cannot "pull and import" anything but what a tarball hands it.

Also unchanged: the instance mints its checkpoint codes as before and the sync reads them back
from the instance itself (§25.5), so a page-sync rotates nothing. `--codes` points at a temp file
that is thrown away; the sheet that matters is `/admin/workshop/answers`.

### 26.6 Consequences to accept

- **The vendored `content/` stops being what an instance imports.** After a page-sync the live
  instance is ahead of this repo. The page records the commit it imported, which is how that is
  visible; `content/upstreams.yaml` is re-pointed at the `*_subject` repos so
  `check_content_sync.py` keeps saying so too.
- **Two instances per subject** since §25.9, so a content fix is two clicks — Sync on `santa`, Sync
  on `self.santa`. The page syncs the instance it is served from and nothing else.
- **Unauthenticated GitHub is 60 requests an hour per IP.** A sync costs 2 (subject) to 5
  (workshop with two submodules), so this only matters if something loops; a `github_token` config
  key is the fix if it ever does, not now.
- **A hand-edited answer on the instance is overwritten** by the next sync, which is the point of
  a single source of truth.

### 26.7 Slices

- **N1** — vendor + mounts + `workshop_source` at provisioning.
- **N2** — the fetcher (ref → sha → tarball, submodule pins, sidecar decryption) and the page.
- **N3** — the wrapper repo, and `workshop.yaml` accepting `repo:`/`ref:`.
- **N4** — suite coverage, then the deploy (a compose change, so every instance is recreated once).

## 28. Porting back2epitech — the workshop whose subject is a dataset (2026-08-31)

`kevin-cazal/back2epitech` is the fifth workshop to port and the first that is **not** a
runtime wrapped in instructions. Its pitch is *"développe un outil qui t'aidera à choisir ton
métier en 2033"*: half a day, five phases, of which only one is code. The code phase builds a
mini-Akinator in ~30 lines of Python over ten steps, and it reads **two CSV files the
participant generated themselves, earlier the same day, by arguing with an LLM**.

That last sentence is the whole port. Every other subject on this platform hands the participant
a fixed artefact — a cart, a VM, a paper computer. Here the artefact is the participant's own
data, it does not exist until phase 3, and phase 4 is worthless if it is malformed.

### 28.1 What the source repo holds

| File | Audience | Ports as |
|---|---|---|
| `guide.md` (770 lines) | **encadrants only**, says so on line 5 | not imported; the passages it addresses to participants are transposed into two subjects (§28.2, §28.6) |
| `sujet.md` | participants | the code chapters, near-verbatim |
| `main.py`, `etapes/etape01..10.py` | encadrants | nothing — they are the answer |
| `verification.py` | encadrants ("pas pour les participants") | **nothing** — the `verification` step shows the expected shape instead (§28.3) |
| `Results/*.csv`, `prompt*.txt` | example dataset, one group's output | a fallback dataset inside the runtime, not content |

The five phases, and what each is made of:

| Phase | Duration | What happens | Participant-facing today |
|---|---|---|---|
| Accueil | — | welcome | no |
| Ice-breaking | 20-30 min | groups of 4-6, everyone introduces themselves | no |
| Génération d'un dataset | 60-90 min | pairs prompt an LLM, get métiers → tags → questions → two CSV | **no, deliberately** |
| Comment marchera notre algo | — | whiteboard: tags, écart, valeur absolue | no |
| Mini-akinator | ~60 min | ten coding steps | **yes — `sujet.md`** |
| Debrief | — | run it, talk about it | no |

### 28.2 The rule this port has to respect

`guide.md` states that phase 3 has no participant subject *on purpose*: "cet atelier est avant
tout interactif, il est préférable d'engager la discussion". CLAUDE.md's rule — never invent
workshop content — and that sentence point the same way: **the port does not write the missing
subject.** What it does is give phase 3 the two things the platform needs and the paper does not
have: a place for the participant to see *what they must end up with*, and a step the instructor
can validate.

So the dataset subject is a **transposition, not new pedagogy**. Everything in it is already in
`guide.md` addressed to the participant — the artefacts, the three CSV constraints ("trois
contraintes à faire demander à l'IA par les élèves"), the "1 / -1", the "8 à 12 tags", the
prompt-improvement leads. What stays out is what is written *to the instructor*: the three
prompts to compare as an exercise, and what to do with a group that goes fast.

**Amended 2026-08-31, with the user.** The whiteboard sequence *is* transposed, as one step
(`algo`), and the ice-breaking gets a subject of its own. The first draft of this section kept
both out on the grounds that they are discussion; the correction is that a participant who was
in the room still needs somewhere to read back why the smallest total wins, and the profile
built during the ice-breaking is the raw material the tags are made of. The step does not
replace the discussion — it says so in its own first line — it survives it.

### 28.3 Three subjects, not one — restructured 2026-08-31 with the user

The first draft imported this as **one** subject whose first part was the dataset. That was
wrong in the way §19 already knows about: the three phases have different audiences, different
durations, and different failure modes, and CTFd's own composition model (a starter, then
ordered advanced subjects) says exactly what this workshop's own guide says — *the starter is
always finished first*.

So it is a **workshop of three subjects**, chained strictly:

| # | Subject | Repo | Steps | What it is |
|---|---|---|---|---|
| starter | Faire connaissance | `back2epitech-icebreaking_subject` | 2 | phase 2, 20-30 min, no code |
| advanced, order 1 | Le dataset | `back2epitech-dataset_subject` | 5 | phase 3 + the whiteboard |
| advanced, order 2 | Mini-Akinator | `back2epitech-akinator_subject` | 14 | phase 4, `sujet.md` |

The chain is linear rather than starter-plus-free-choice because each subject consumes what the
previous one produced: the tags are made of the profile, and the code is worthless without the
CSV. `sync_workshop.py` gates each subject on the previous one's **closing step** — the step
where the participant says they are done — which is one edge that means something to the person
clicking it (§19, D2).

**A workshop chain fails open, and it did here.** `sync_workshop` gates each advanced subject on
the previous one's closing step, and `previous_final = result["final_step"] or previous_final`
kept the older value when a subject had none. Neither of the first two subjects had one on the
first sync, so both `Le dataset` and `Mini-Akinator` imported with **no prerequisite at all** and
the whole workshop was open from the first minute. Caught in the database, not in the sync output,
which said nothing. Fixed twice over: the two subjects now end on a closing step, and
`closing_of()` falls back to the subject's last required step and prints why. Nothing else in the
platform assumed a closing step exists — `discover-linux` has always had one, which is why this
never showed.

**Faire connaissance is the starter for a reason that is not chronological.** Its `intro.md` is
also the workshop's landing page (`sync_workshop.py:154` builds the index from the workshop name,
its summary and the starter's entrypoint body), so the day's programme lives there.

| Subject | Part | Steps |
|---|---|---|
| Faire connaissance | Faire connaissance | `presentation`, `indices` |
| Le dataset | Demander à une IA | `premier-prompt`, `meilleur-prompt` |
| | Comment marchera l'algo | `algo` |
| | Les fichiers | `tags-questions`, `verification` |
| Mini-Akinator | Le calcul | étapes 1-4 — **ends on the guide's first sync point** |
| | Le classement | étapes 5-6 — **ends on the second** |
| | Le questionnaire | étapes 7-10, closing on "Ça y est" |
| | Si tu as le temps | 4 optional, `topology: free`, closing on the mémo |

**The dataset's five steps**, fixed with the user 2026-08-31:

| id | What the participant ends with |
|---|---|
| `premier-prompt` | a first list of métiers, and **the prompt that produced it, written down** |
| `meilleur-prompt` | the same list, reworked: real métiers, sourced, spread across domains |
| `algo` | why the tag is the bridge, why 1/0/-1, why the smallest total wins |
| `tags-questions` | 8-12 tags, the métiers table, one question per tag, and the two CSV |
| `verification` | files whose **shape** matches what the code will read |

Two notes on those five. `premier-prompt` is only worth points because of what it keeps: the
comparison between it and `meilleur-prompt` is the exercise, not either list on its own. And
`verification` **does not port `verification.py`** — decided with the user. The upstream script
is instructor material ("les élèves n'ont pas à comprendre comment on valide un CSV pour
commencer à coder"), so the step shows the two files' expected shape, spells out the three
constraints, and has the participant read the two files side by side. The gate stays the
instructor's eyes.

The three reference tables at the end of `sujet.md` — outils, structures, "si ça plante" — are
**not** a document of their own. A document with no exercises gets no route
(`sync_subject.py:826`), so `memo.md` would have imported as nothing at all; they are the trailing
prose of `bonus.md`, which is exactly the closing step a `free` chapter needs anyway (§3.11).

### 28.4 Instructor-led only, and no self-serve twin

Every other subject got a `self.*` twin in §25.9. This one does not get one, and it is not an
oversight: phases 2, 3 and the whiteboard are a room with a whiteboard and someone asking *"et si
on avait pris 1 et 0 au lieu de 1 et -1 ?"*. A participant alone at home with these seven
documents has the code chapter and nothing else — that is a different, smaller workshop, and if
it is ever wanted it should be authored as one rather than fall out of a mode flag.

`platform.mode_default: instructor_led`, one instance, no twin.

### 28.5 The runtime: JupyterLite, self-hosted (decided 2026-08-31)

The code phase needs, in a browser: CPython with `csv`, two files the participant supplies,
`print` to a console, and **a working `input()`** — étapes 8 and 9 are *about* `input()`, and
étape 9's acceptance test is literally "type `x`, the question must be asked again".

Nothing this project already embeds does that:

| Runtime | Verdict |
|---|---|
| `tic80` (PyPong is "Python") | TIC-80's Python is PocketPy inside a fantasy console: no `csv`, no file the participant supplies, no stdin |
| `v86` (shell-1, shell-rpg) | a real Alpine would work, but its package list is `micropython`, not CPython — so it is a new VM image either way — and it means ~330 MB of bundle and a Linux terminal in front of a 15-year-old in their first hour of Python, to run 30 lines |
| `pacman`, `miniasm` | not Python |

So this port needs a new runtime. The decision is **not to write one**: it is to self-host
**JupyterLite** with the Pyodide kernel, at `/runtime/pylab/<sha>/` like every other dist.

#### 28.5a Why: the `input()` problem is already solved, in the open

Blocking stdin in wasm normally means a worker plus `Atomics.wait` on a `SharedArrayBuffer`, and
a `SharedArrayBuffer` needs `crossOriginIsolated`, which needs COOP + COEP on the **top-level**
document — i.e. on CTFd's whole origin. §18.2 measured that v86 does not need that and left the
origin alone on purpose; this must not be the feature that reverses it. That one constraint is
what eliminates most of the field.

`jupyterlite-pyodide-kernel` handles it both ways: `Atomics.wait` when cross-origin isolation is
available, and a **service worker** when it is not — for the filesystem since 0.4.0, and for
**stdin** since 0.6.0a6 (PR #183, May 2025). The mechanism, read in
`packages/pyodide-kernel/src/comlink.worker.ts`, is a *synchronous* `XMLHttpRequest` from the
kernel worker to `<baseUrl>/api/stdin/kernel`, intercepted by the service worker and routed to
the browsing context, which shows the prompt and posts the reply back. That is exactly the trick
this section's first draft listed as "option 3, known to work, fragile, only if 2 fails" —
except written, shipped and maintained by someone else.

Both halves are alive: JupyterLite 0.8.3 and `jupyterlite-pyodide-kernel` 0.8.5, released
20 and 21 August 2026.

Four more things settled it:

- **6 MB, not 330.** `pyodide-core-314.0.6.tar.bz2` is 6 MB: the interpreter and the standard
  library, which is all this workshop needs. The 333 MB full distribution is the scientific
  packages, which are never loaded. `jupyter lite build --pyodide <local tarball>` bakes it in,
  so nothing is fetched from a CDN and the whole runtime is served same-origin like the others.
- **The audience's teachers already deploy it.** Basthon — French, Pyodide, working `input()`,
  CSV loading, no account — was the obvious answer and is **archived**; its author moved to
  Capytale, and Capytale now runs JupyterLite.
- **`trinket.io`, which upstream's `guide.md` recommends, cannot run étape 1.** Its free Python
  is Skulpt, a JS reimplementation whose standard library is math, random, turtle, time, re and
  a few others: there is **no `csv` module**. This is true today, independently of this decision,
  and it is worth telling the workshop's author. It is also the general argument against Skulpt
  and Brython: the mémo's "si ça plante" table quotes real CPython messages
  (`TypeError: unsupported operand type(s) for -: 'str' and 'int'`), and a reimplementation does
  not produce them.
- **PyScript was the near miss.** Same Pyodide underneath, but its interactive terminal — the
  part that makes `input()` work — requires worker mode, which requires COI. Same wall.

#### 28.5b What is actually built

Almost nothing, which is the point:

```
tools/build_runtime.sh pylab      # new case: jupyter lite build --pyodide <pinned tarball>
                                  #           --contents content/ (a starter notebook)
plugins/workshop/assets/runtime/adapters/pylab.js
                                  # announce ready, handle focus. No result, no propose.
back2epitech_akinator/subject.yaml
                                  # a runtime: block, pane side, open: false
```

Rule 0 costs nothing here: nothing in a notebook knows whether étape 5 is done, so the adapter
sends no `result` and the participant validates steps exactly as they do today.

**UI trimming stays on the supported path.** The brief in CLAUDE.md is to hide everything
unnecessary, and JupyterLite answers most of it from the build: pick the Notebook (single
document) app rather than Lab, disable extensions at build time, and ship an `overrides.json`
that turns off the chrome the workshop does not use. That is configuration, not a fork.

#### 28.5c Two caveats to verify before shipping, not after

- **Firefox private browsing registers no service worker**, so no filesystem and no `input()`.
  There is also a known Firefox bug where JupyterLite in an iframe cannot touch the filesystem
  (jupyterlite#1341, open since March 2024) — but it is **cross-origin iframes only**, caused by
  storage partitioning, and §14's rule of serving every runtime same-origin dodges it by
  construction. Verify on the box; do not take the issue thread's word for it.
- **§16 persistence does not work as-is.** JupyterLite keeps the file browser in IndexedDB, via
  `@jupyterlite/localforage`, not in `localStorage` — so the `storageKeys` snapshot mechanism
  has nothing to grab, and a participant who changes machine loses their notebook and their two
  CSV files. Either a collector learns to read a named IndexedDB store, or this subject accepts
  browser-local work in progress and says so. Measure it during the build; it is the one place
  where this runtime is less integrated than tic80.

#### 28.5d The content consequence, and it is small

A notebook has no `main.py`. "Crée un fichier `main.py`" becomes "écris dans la première
cellule", plus one line about dropping the two CSV files in the file panel. That is roughly three
sentences, in `intro.md` and étape 1 of `back2epitech_akinator`. Everything else survives intact,
including `FileNotFoundError` when a CSV is in the wrong place, because the notebook's working
directory is its own folder on the drive.

#### 28.5e The escape hatch, if the notebook framing is wrong: `pylab` as our own shell

Not built, and not costed as a first choice — recorded because it is the only other shape that
clears the `input()` wall, and because the day the notebook framing turns out to be wrong is not
the day to start researching.

The idea: keep the kernel, drop the JupyterLab UI. Our own three-pane app — an editor holding a
literal `main.py`, a Run button, a console, a drop zone for the two CSV files — driving
`@jupyterlite/pyodide-kernel` as an npm dependency. It keeps `main.py` literal, it satisfies the
"hide everything unnecessary" brief completely rather than mostly, and it is the shape the rest
of this platform's runtimes have.

**What it actually costs, read out of the packages rather than guessed.** The kernel is not a
kernel in a box. `comlink.worker.ts` blocks on a synchronous XHR to `<baseUrl>/api/stdin/kernel`,
and the thing that answers is the service worker in **`@jupyterlite/apputils`**
(`service-worker.ts`, `service-worker-manager.ts`), routing by a `browsingContextId` the host has
to mint and thread through. The filesystem is `DriveFS` from **`@jupyterlite/services`**, mounted
at `/drive` and talking to a contents manager over the same intercepted `/api` surface, backed by
**`@jupyterlite/contents`** and **`@jupyterlite/localforage`**. So the dependency is not one
package, it is *JupyterLite minus the JupyterLab UI*: the kernel, the server emulation, the
drive, the service worker, and the wiring between them.

Which reframes the trade honestly. This is not "a small app plus a library" against "a big UI" —
it is **a fork of the shell** against **configuring the shell**, with the same five packages
underneath either way. And none of that surface is documented as a public embedding API: it is
internal to JupyterLite and free to move between minor versions, which is a maintenance bill on
a component nobody here owns.

The order that follows from that: ship the configured JupyterLite, watch a real room use it, and
only then decide whether the notebook framing is a genuine problem or an unfamiliar one. If it
is genuine, the intermediate step is more `overrides.json` and fewer extensions, not a shell.
The shell is the last resort, and this section is here so it starts from what the packages
actually require.

### 28.6 What the instructor gets, and where the answers live

`guide.md` is the trame, and it also contains **the finished code of all ten steps**. It is not
workshop content and it is not imported. Two consequences:

- The subject repo carries the participant documents only. The guide stays in
  `kevin-cazal/back2epitech`, which is where instructors already look for it.
- **That repo is public, so the answers are public.** Every other subject here keeps its answers
  under GPG with the passphrase in `deploy/secrets.yaml` (the split of 2026-08-24). This one is
  the exception, inherited rather than chosen. Flagged, not fixed by this port: fixing it means
  `guide.md.gpg` + `etapes/` encrypted in the upstream repo, which is that repo's call.

Validation is §23's codes, as on the other five instructor-led instances: `checkpoint` steps get
generated codes in `instructor_codes.back2epitech.yaml`. §27's queue (still on the `instructor-review-queue` branch, P1+P2
of it built) has no button and no console yet, so this instance inherits them the day P3/P4 land — and it is the
subject that will want it most, because of `fichiers`.

### 28.7 Repos

| Repo | Visibility | Contents |
|---|---|---|
| `kevin-cazal/back2epitech_subjects` | public | the wrapper: `workshop.yaml` + the three subjects as submodules |
| `kevin-cazal/back2epitech-icebreaking_subject` | public, fresh history | starter, 2 steps |
| `kevin-cazal/back2epitech-dataset_subject` | public, fresh history | 5 steps |
| `kevin-cazal/back2epitech-akinator_subject` | public, fresh history | 14 steps — `sujet.md` ported |
| `kevin-cazal/back2epitech` | unchanged | the guide, the solutions, the example dataset |

Fresh history is the requirement and also the right call: these are a *rewrite* of `sujet.md` and
of parts of `guide.md` into a marked-up convention-2.0 tree, and carrying an unrelated document's
commits into them buys nothing.

The wrapper follows `discover-linux_subjects` exactly: submodules for the command line's `path`,
`repo` + `ref: submodule` for the admin sync page, which is what an instance actually reads
(§26). `content/back2epitech_*` and `content/workshops/back2epitech/` in this repo are the
vendored copies, listed in `content/upstreams.yaml` so `tools/check_content_sync.py` sees drift.

### 28.8 Deployment

One instance, the eleventh, following §22 exactly:

```yaml
- name: back2epitech
  port: 9090                       # first free above the 9080-9089 block
  content: content/workshops/back2epitech
  source: kevin-cazal/back2epitech_subjects
  workshop: true                   # three subjects, chained (§28.3)
```

The mode is not set: `instructor_led` is the manifest default and every subject's
`platform.mode_default` agrees, so there is nothing to override (§3.5b). No `self.*` twin (§28.4).

`back2epitech.ealab.duckdns.org`, DuckDNS wildcard so no DNS record to add, host nginx vhost +
its own certificate, `provision.py render / up / setup / sync`. Nothing new in the deploy path.

### 28.9 Decided (say so if any is wrong)

- **The dataset phase gets four checkpoint steps, not zero.** Zero would mean the platform has
  nothing to show for the first three hours of the day and the instructor has nothing to
  validate. Four is the minimum that matches the four artefacts the guide already names.
- **The three prompts stay instructor-side.** Showing a participant the "good" prompt deletes the
  exercise, which is comparing three of them. What `meilleur-prompt` gives instead is the guide's
  *directions* for improving a prompt, which is what the instructor hands out anyway.
- **The whiteboard is transcribed, as one step.** Reversed 2026-08-31 (§28.2). It is written as a
  recap of a discussion that has already happened, not as a substitute for it, and its acceptance
  criterion is being able to say it back without looking.
- **`verification.py` is not ported.** The step shows the expected shape instead (§28.3).
- **`sujet.md`'s hints become `ws:hint` at no cost.** They are scaffolding, not a paid clue.
- **No quiz challenges.** The guide's good questions are discussion prompts; auto-grading them
  turns a conversation into a form.
- **Points 25 flat**, bonus steps `optional: true` (they count towards nothing, §3.3).

### 28.10 Slices

- **P1 — content and the instance.** Three subject repos plus the wrapper, vendored copies,
  instance on 9090, no runtime declared. Complete and usable: participants run Python where the guide already
  tells them to (their own install, trinket.io, jupyter). This is what ships first.
- **P2 — `pylab`, a self-hosted JupyterLite (§28.5).** A `build_runtime.sh` case pinning
  JupyterLite, the kernel and a local Pyodide tarball; the trimming config; a starter notebook in
  `--contents`; the WRP adapter; the `runtime:` block in the akinator's `subject.yaml`; the three
  sentences of §28.5d. Then measure the two caveats of §28.5c before calling it done.
- **P3 — suite coverage** for the workshop composition and, if P2 lands, the runtime.

---

## 29. Making it feel like somewhere, not something (2026-09-11)

The visual reskin (§ none — it had none, which is half of why this section exists) landed the
palette, the fonts and the app shell from `jump`. The PO's response to it was:

> "Il faudrait ajouter quelques images et illustrations pour rendre l'interface plus vivante"

and, asked whether he meant inside the subjects or around them:

> "L'interface est tres sobre, par exemple la banner dans notion est vraiment bien, tout de suite
> c'est plus sympa, effectivement si deja le sujet est plus vivant ca va changer l'experience
> globale"

`grep` over this file for `hero|cover|banner|celebrat|anim|motion|i18n|french` returned nothing
before today: there was no decision of record on any of it, which is why it drifted. This section
is that record. The *visual* contract stays in `DESIGN.md` — this is the reasoning and the scope.

### 29.1 The diagnosis was not "add pictures"

The audit found the reskin using a subset of its own contract. `.epi-blueprint-grid` was defined
and never called. There was no full-bleed brand surface, no `cardBrand`, no `buttonNeon`, no pixel
squares. There were 8 `transition:` declarations in the whole plugin, **zero `@keyframes`**, and no
`prefers-reduced-motion` anywhere. And half of what `jump` already has had never been ported —
`PageHero`, `LoginBrandPanel`, `EmptyState`, `XpFloat`, `rewardToast`, the confetti action, and the
`.prose` block its own comment calls "Notion-like with Epitech branding".

So the work was mostly **using the contract we already wrote**, not inventing a new one. Four
deliberate deviations exist and are registered in `DESIGN.md`; everything else is a port.

### 29.2 Decision: the band is header + hero, and it is blue

Argued in full in `DESIGN.md` ("The brand band"). In short: `jump` forbids full-bleed brand blue as
*chrome*, on a fatigue argument about a permanent surface; this is ~200px at the top of a scrolling
page on a header that was never sticky, and it carries page identity, so it is a hero that contains
the nav. It is also, concretely, the Notion banner the PO named — and he had already approved a
blue navbar in an earlier mockup, so this is the version to show him and adjust if he disagrees.

Reversible: it is one `background` declaration plus three blue-on-blue corrections.

### 29.3 Decision: a subject shows its face, and the platform owns how

**The subject supplies a sentence and a file. It never supplies a layout.** That is the whole of
it, and it is what makes four subjects arrive looking like one platform instead of four products.
Convention in `docs/CONTENT_CONVENTION.md` §3.2b, authoring guide in `docs/CONTRIBUER.md`.

Three levels: derived, declared per subject, declared per document. **The derived level is the
point**, not a fallback — `project.summary` was already mandatory in practice and displayed
nowhere, and almost every subject opens with a screenshot. So no subject can be blank, and
declaring a cover is an improvement rather than a prerequisite. Pac-Man gained a band with its own
accroche and its own screenshot without its repo being touched.

**The linter gained a warning tier for this** (`lint_all`, beside an unchanged `lint`). "Your cover
has no accroche" must never be able to stop a workshop importing ten minutes before a session.

### 29.4 Decision: the participant path is French

Reversing `CLAUDE.md`'s "all platform strings English until a later i18n phase". This is that
phase, scoped to the participant path; the admin panel stays English. The audience is French
lycéens arriving from Jump, which speaks to them in French with emoji. English chrome around French
content was the loudest remaining sign that they had left.

It costs almost nothing because CTFd already ships flask-babel and a full French catalogue, and
`get_translations` **merges** catalogue directories — so the plugin adds its own and inherits
upstream's for every string it already has.

### 29.5 Decision: 33 solved steps deserve 33 moments

Three intensities so the last one still counts (step / part / workshop). Ported from `jump`'s own
celebration primitives rather than invented. The one thing with no precedent is the subject's
mascot in the reward float, and it is cheap: the sprite already ships in every subject repo.

### 29.6 What is deliberately still open

- **The step-level reward has no sound and no haptics.** Not rejected, just not tried.
- **A completion/ceremony screen** is where `.epi-blueprint-grid` is finally meant to be used, and
  it does not exist yet. The workshop-complete moment is currently confetti plus the inline block.
- **The four profile pages** (`users/private`, `teams/public`, …) were the awkward case for the
  band and are handled by restyling their badges. They are not a surface this audience uses; if
  they ever become one, they deserve their own pass rather than more selectors.
- **No enforcement**, unchanged from `DESIGN.md`'s own position. The regression suite checks that
  markup and copy still match, not that a colour is on-palette.

---

## 30. Reaching the runtime, and fitting it on a small laptop (2026-09-14)

Three things came back from a read of the reskin (PR #2), plus one bug reported from the page.

### 30.1 The launcher was not removed, and it had never been designed

First, the fact, because the review started from the opposite premise: **nothing removed it.**
`git log --all -S'ws-runtime-handle'` returns the initial import and nothing else,
`assets/runtime.js` has not been touched since that commit, and the reskin's diff does not enter
the `{% if runtime %}` block. The button, its CSS and its wiring were all still there.

Two other things were true, and together they are almost certainly what was seen:

- **A missing dist removes the runtime silently.** `declared_runtime()` answers `None` when
  `plugins/workshop/runtimes/<id>/<version>/` is absent, which is the right answer for a page —
  it must never point a frame at a 404 — but it drops the launcher, the pane and the script with
  no log line and nothing on the admin sync page. The dist is gitignored and built per instance
  (`tools/build_runtime.sh`), so an instance can look correctly deployed and have no runtime at
  all. It now logs once per (id, version) naming the path and the command, and
  `/admin/workshop/sync` says the same thing. Reproduced on the dev instance: the whole block was
  missing, and `tools/build_runtime.sh pacman c0ee1da` brought it back unchanged.
- **It was the one element the reskin did not reach.** A stock `btn btn-primary` with a literal
  `border-radius: 3px` and an `rgba(0,0,0,.25)` shadow, glued to the right edge. Every other
  surface went through `DESIGN.md`'s token contract; this one read as a leftover from a different
  application, which is what "we lost the link to the runtime" describes even when the link is
  there.

### 30.2 Two controls, one action, never both on screen

The edge tab is right for *from anywhere* and wrong for *on arrival*: a participant has to notice
a 100px strip before they can open the thing they came to build in. So the control exists twice:

- **`.ws-runtime-cta`** — a labelled button in the page's hero, where somebody arriving is
  already looking. It sits on the brand band, where `.btn-primary` is painted in the band's own
  colour and loses its edges, so it takes `jump`'s `buttonNeon` pair. That recipe already existed
  for the signed-out header CTA; it is now `.epi-cta-on-band` and both use it, rather than a
  second copy of nine declarations. It is the same idea both times: the one hopeful action on a
  brand surface.
- **`.ws-runtime-handle`** — the same control fixed to the right edge, revealed by one
  `IntersectionObserver` only once the hero button has left the screen. Exactly one of the two is
  reachable at any moment, which is what that element's own CSS comment claimed from the first
  commit and what nothing implemented, because the in-flow toggle it referred to did not exist.
  It rests tucked a few px into the edge and slides flush on hover or keyboard focus — a drawer
  tab that answers to a pointer says most of what a 100px strip can say about itself — inside the
  `(hover: hover)` guard, so a touch screen just gets it flush.

**The glyph comes from the runtime id, host-side**: a small `ICONS` map beside `declared_runtime()`,
keyed exactly the way the adapter path already is. No new convention field, no asset pipeline, and
nothing to change in a `*_subject` repository to get a mark on the button. Font Awesome, which
`DESIGN.md` already records as a deliberate deviation from `jump`'s Lucide for the header, so this
adds no dependency and no second icon set.

**Rejected: a control in the navbar.** The shell is global and knows nothing about a subject, so
the item would have to appear and disappear per page, and `DESIGN.md` documents that right-hand
cluster as `jump`'s density of three. Page-scoped actions do not belong in the app shell.

### 30.3 The pop-out — §14.2's "optional pop out", built

The pane is a real split, and half of a 13" viewport is not room to work in. `workshop.css` already
conceded this by making the pane take over the page below 900px, which is a worse answer than a
second surface. §14.2 had already resolved the approach ("separate tab/window — keep as an optional
pop out, not the default") and `docs/RUNTIME_PROTOCOL.md` §7 listed it as not built.

**`split` | `window`, stored in `ws-runtime-mode`.** With no stored choice the viewport decides:
below `PANE_MIN_WIDTH` (900, the number the CSS rule already used) the launcher offers the tab and
says so in its label; at or above it offers the pane. **What is automatic is the default, never the
tab.** Opening a window nobody asked for is hostile, and outside a click every browser blocks it
anyway.

**`GET /workshop/<doc_slug>/runtime`** renders the same host in a page of its own, and
`assets/runtime.js` runs there too. Not `window.open(runtime.src)`: the runtime never talks to
CTFd (§14.3 rule 1), a host does, and a runtime in a tab with no host has no `init`, no step, no
snapshot and no restore-before-boot. Per document, because the frame boots with the *subject's*
parameters (§19.2) and a document is what says which subject — which also means a subject synced
before per-document routes existed has no pop-out and keeps the split. The path is
`/workshop/<doc_slug>/runtime` and not a literal under `/workshop/`, because `/workshop/runtime`
would shadow a document whose slug is `runtime`.

**One host, two presentations, two booleans.**

| | owns the frame | owns the page |
|---|---|---|
| subject page, split | yes | yes |
| subject page, window | no | yes |
| the popped-out tab | yes | no |

*Owns the frame* is the protocol, the iframe and the §16 snapshot. Exactly one window has it, and
that is what keeps restore-before-boot true with no cross-window sequencing: whichever window
creates the frame is the one that restored first, and the only one that saves. *Owns the page* is
the step list, the advisory hint and the launcher.

**The transport is a `BroadcastChannel`, not `window.opener`** — it survives a reload on either
side and a subject page re-opened in another tab, with no handle to keep. `step` goes down,
`result`/`propose`/`mode` come up.

**Named `ws-runtime:<document>`, because the host is per document.** The first version of this
reasoned that one instance declares one runtime, so origin scope was enough. It is not: a
`BroadcastChannel` reaches every open page on the origin, and several subject pages at once is the
expected shape here rather than an edge case — Parcours nodes are `<a>` elements specifically so
that ctrl-click opens one in a new tab (§30.4). On one shared channel every such page applies an
advisory `result`, and a page that does not show the step it names falls through to its own current
step and lights up the wrong card; `propose` would fill the wrong answer field. Naming the channel
after the document makes the transport match the route, and the fallback keeps the meaning it has
always had — "the step you are on" — because the only pages left listening are showing the same
part. The tab's *name* stays global on purpose: one runtime tab is the idea, so opening the runtime
from another part moves that tab instead of leaving a row of abandoned editors behind.

**Rule 0 is unaffected, and that was checked rather than assumed.** `result` and `propose` cross
the channel and are still applied by the page exactly as they were in one document. Driven in a
browser: an adapter emitting both from inside the popped-out frame renders the hint and fills the
answer field **on the subject page**, and the solved count does not move.

**The tab is named and reused.** `window.open("", "ws-runtime")` runs synchronously in the click
handler, because an asynchronous one has lost the gesture; the empty URL is what makes an existing
named tab be reused rather than re-navigated, and re-navigating would reboot an editor's unsaved
buffer or a 300 MB VM. Measured: a second press opens no second tab and leaves
`performance.timeOrigin` unchanged. If `open` returns `null` the pane opens instead and the bar
says which of the two happened.

**Each mode carries the way to the other, and only that** — no menu, no split button. The pane bar
gets "Open in a new tab"; the tab's bar gets "Side by side", which broadcasts the mode, lets the
subject page open the pane, and closes itself (with a line instead when the browser refuses, which
it does for a tab a script did not open).

**A handover gives the frame up first, in both directions.** `setMode` says this for the pane, and
the tab now does the same: it drops its frame before it broadcasts `split`, not when `pagehide`
fires. The difference is the refused `close()` above — the subject page mounts its own frame the
instant it hears `split`, so a tab that still held one was the second editor writing one cart, for
as long as the participant left it open. The note asking them to close it is not a substitute for
not being a runtime any more.

**Switching presentation reboots the runtime**, and nothing avoids that: a live iframe cannot be
adopted by another document without reloading. The work is pushed to the server before the frame
goes and restored before the new one boots, which is the whole reason §16 exists. Verified on the
popped-out host: it watches the runtime's key, saves to the server, and puts the work back on the
next visit before the frame is created.

### 30.4 One way to a step, and it is the workshop page

`Challenges` in the navbar is the same steps as the workshop view in CTFd's own presentation — a
grid of point values instead of a subject to read — so for a participant it was a second entry to
one place. It goes behind `is_admin()`: the instructor it was documented as being kept for is an
admin here, and `/challenges` stays served and reachable by URL. The link goes, not the route.

Dropping the link alone would have moved the leak rather than closed it: every Parcours node was an
`<a href="/challenges#<name>-<id>">`, one click from the entry that just left. `graph.py` now
returns a `url` per node and a node opens `/workshop/<document>#step-<id>`, which `openFromHash`
already handles. Only the server knows which document a challenge belongs to, and `page.py` already
built that mapping to point a "finish X first" note at the right page — so it becomes `links.py`
(`documents`, `step_pages`, `step_href`), used by the page and by the graph. `graph.js` keeps
`boardUrl` for the one caller that IS the board: the graph also renders inside the challenge modal,
where a node means "open this one next", in place.

### 30.5 A solve stopped scrolling past the part introduction

Reported from the room: finishing the Pac-Man intro lands on "Étape 0" and the paragraph explaining
what a decision tree is never appears. It is on the page — a part's title and its
`<details class="ws-part-lead">` sit above its first step (§24) — and `refresh()` scrolled to the
step card, so both went by above the viewport. Measured headless at 1280x900 before the fix: the
introduction at `top: -599`, `bottom: -12`. After: `top: 89`.

A solve now targets the part when the next step is the first of that part, and the step anywhere
else, where the only thing above it is the step just finished. That is what the parts stepper and
the `#part-N` anchors already did through the same `scrollTo`, so a solve and the two manual ways
to navigate now agree.

The same miss one case over: completing a document leaves `next` null and used to scroll nothing,
so the forward cue that had just appeared stayed wherever the previous solve left the page —
measured half below the fold, and arbitrarily far down after a long last step. `updateCounters`
already knew the reveal was a transition; it now says so and the caller brings the cue into view.

### 30.6 Two bugs found while checking the above

- **The runtime was told the wrong step.** `activeStep()` preferred the step the participant opened
  last, then document order. `<details>` fires no `toggle` when it was already open, and
  `_open_states` renders a locked step open when its part has nothing else to show — so
  `refresh()`'s `next.open = true` on the freshly unlocked step changed nothing, `lastOpened`
  stayed on the step just *solved*, and document order found that same step first. An advisory
  `result` landed on the solved card, where there is no form to put it in, and `init` carried the
  wrong step id. A solved step is never the answer, and the page's own answer to "where are you" is
  `data-state="current"`, so that is the fallback now. This was live in the split pane too, not
  something the pop-out introduced.
- **`.ws-runtime-ok` had no CSS at all.** Toggled since the first commit, styled nowhere, so half
  of the advisory signal was invisible. It is a small tint dot on the step summary — never the
  solved green, which would say something rule 0 forbids.

### 30.7 Open, and deliberate

- **A part's second introduction is still dropped.** `_part_lead` renders only a part's first lead,
  so prose an author writes further down a part is attached by the parser to the following step and
  then shown nowhere — §24 one layer up. No subject hits it (every `ws:context` in the live database
  is on a first-of-part step), and the fix belongs in the page's shape rather than in that
  function, so meanwhile it logs. Silence is exactly how §24 shipped.
- **`bottom` placement** is still accepted in metadata and still renders as a side pane. The
  pop-out is the answer for the case that actually came up.
- **No dual-screen window**, only a tab. A sized popup is better on a second monitor and worse on
  the laptop this was asked for, and two controls for one idea is a worse default than one.


## 31. The way in from Jump, and the way progress gets back (2026-09-15)

Jump — the Epitech Academy talent platform — becomes the only door into a workshop instance. A
talent clicks the activity on their Jump dashboard, lands on the CTFd already signed in, and the
steps they solve report back so the XP arrive without anybody running a script against the
production database. Closes issue #6; the Jump half is `Manta-Epitech-Academy/jump#352` and its
own plan is `jump/docs/plans/13-ctfd-jump-mvp-plugin.md`, which this section is the CTFd-side
record of.

Nothing under `CTFd/` moves. The whole feature is `plugins/workshop/jump.py` (the ticket, the
account, the route, the settings page), `jumpqueue.py` (the solve hook, the outbox, the drainer),
`progress.py` (the counted population, extracted rather than written a fifth time), a
`migrations/` directory, and `scripts/jump_check.py`.

### 31.1 The ticket is not a JWT, and that is a constraint before it is a choice

    b64url(json(claims)) + "." + b64url(hmac_sha256(ticketKey, part1))

    claims = {kid, sub: talentId, name, aud: "workshop:<slug>", iss: "jump", iat, exp, jti}
    exp = iat + 120

**There is no JWT library in the image and one cannot be added.** The fork's Dockerfile runs its
plugin-requirements loop at build time over the `./CTFd` context, while this plugin arrives at run
time as a bind mount, so a `plugins/workshop/requirements.txt` is never read;
`docker exec <ctfd> python -c "import jwt"` raises `ModuleNotFoundError`. A fixed-algorithm token
verified with stdlib `hmac` is what is left, and it is the better token here: with no `alg` header,
the whole "alg: none" / "RS256 verified as HS256" class does not exist to be defended against.

Two keys are derived from the one shared secret, so that compromising one direction is not a
forging capability in the other:

    ticketKey   = hmac_sha256(secret, "jump/ticket").hexdigest()
    callbackKey = hmac_sha256(secret, "jump/callback").hexdigest()

**The derived key is the lowercase hex digest as an ASCII string** — node's
`createHmac('sha256', secret).update(label).digest('hex')`, and *that string* is the key of the
next HMAC. **b64url is unpadded on the wire**, and the verifier re-pads, so a padded token is
accepted too. Both halves of that are frozen with the Jump side; changing either means changing
both repositories.

The order of verification is the design. The `kid` is resolved against the allowlist **before any
cryptographic decision**, and there is deliberately no "there is only one secret configured"
fallback — that turns a single-origin instance into a verification oracle the day a second origin
is added. Then `compare_digest` and never `==`, then `aud` and `iss`, then the clock. `exp - iat`
is checked against our own 120 s ceiling and not merely against itself: a token claiming a month is
refused while inside its own window, because a bug on the Jump side must not be able to hand out a
bearer this instance has no way to revoke.

### 31.2 The key id is what makes one instance serve two Jumps

`workshop_jump_keys` is a map `{kid: {origin, secret, label}}` from day one rather than a single
entry. It costs the same and it is what lets one instance serve `epiboost.eu` and `epiboost.fr` at
once, which is what a demo needs. **The callback origin comes from the matched key's row, never
from the token**, so nobody can point this instance's reports somewhere else.

`label` namespaces the synthetic email, and that is what keeps a dev talent off a production
scoreboard when one instance serves both.

**A label belongs to the accounts it namespaces, not to the key row that declared it.** The
configuration is the current intent; it is not a history. A key id can be renamed, or removed and
re-added, and `tools/provision.py` rewrites the whole map on every `setup` — so a rule enforced
against `workshop_jump_keys` stops holding the first time somebody edits a key id in
`deploy/instances.yaml` and keeps its label. What happens then is silent and permanent: the next
returning talent resolves to an account that already belongs to the old key id, the link insert
hits the `user_id` unique constraint, and they get a refusal page for ever while new talents keep
working. So the link row records its label (`jump_label`, revision 2, backfilled out of the address
that created it) and ownership is read from the rows: no other key id may take a label that has
accounts, and the key id that has them may not walk away from it. Enforced at both entrances — the
settings page for the form, `resolve_account` for the provisioning path that never sees one.

### 31.3 The accounts hold no personal data of a minor

The email is synthesised, `f"{talentId}@{label}.jump.invalid"`. `.invalid` is reserved by RFC 2606,
matches CTFd's own `EMAIL_REGEX` and can never resolve, so these instances — third-party hosts,
public scoreboards, an audience under 18 — store no address anybody can reach. `Users.name` is not
unique in CTFd, so the display name needs no disambiguation; the email carries the uniqueness.

**A NULL password is load-bearing rather than incidental.** `CTFd/auth.py:476-482` refuses local
sign-in to such an account, so "everybody comes through Jump" is enforced by the data instead of by
a note in a runbook. It is also a trap, and it cost a real bug on the way: `Users` carries
`@validates("password")`, which hashes `str(plaintext)` **unconditionally**, so `Users(password=None)`
stores a real hash of the string `"None"` and every account this route creates shares one guessable
password. The column has to be left out of the constructor entirely, which is what CTFd's own OAuth
path does at `auth.py:608-614` without saying why. Nothing would have noticed by itself — the
second entry resolves through the link row and never looks at the password — so `jump_check.py`
asserts the NULL.

Resolution is in this order, because **the link row and not the email is the identity**, which is
what will let the email scheme change later without orphaning a single account:

1. the link row on `(kid, talentId)` — found, done;
2. otherwise the synthetic email;
3. found with a password set: refuse. Barely reachable behind `.invalid`, but the absence of the
   branch is an account-takeover primitive and the branch is three lines;
4. found and already linked to another key id: refuse — §31.2;
5. not found: create with no password, link it, commit. An `IntegrityError` on the link means two
   first arrivals raced; the loser re-reads the row and carries on with the same account.

A table of our own rather than CTFd's `Fields` / `FieldEntries`, so no talent id leaks into the
admin user form or a public profile.

### 31.4 The route, and the three things that would have failed silently

`GET /jump/enter?t=…` on its own blueprint under `/jump/`, **not** under `/workshop/`, which would
mask a document whose slug was `enter` the day an author names a file that.

- **Not a bare `@ratelimit`.** `get_ratelimit_subject` only extracts an identity for the three auth
  endpoints and falls back to the IP everywhere else, so a class of thirty lycéens behind one
  school NAT would share a single bucket — exactly the outage a per-identity limiter exists to
  avoid. So: a generous per-IP flood brake ahead of verification, and the real limit on the `sub`
  the ticket names, applied once the signature is known good.
- **Single use is `cache.add`, which is SETNX and therefore atomic.** The get-then-set in
  `CTFd/utils/decorators/__init__.py:209-217` has a window between the read and the write, and two
  requests carrying one `jti` would both succeed. `jump_check.py` fires two concurrent requests with
  one `jti` and asserts `[302, 403]`; a get-then-set gives `[302, 302]`.
- **`user.banned` is checked in the route.** CTFd's own guard only fires on the *next* request, so
  without this a banned talent is logged in and then meets a 403 they cannot interpret.

Then `session.regenerate()` and `login_user()`, copied from the local sign-in path
(`auth.py:485-487`) and not the OAuth one at `:663`, which skips the regeneration and leaves the
pre-login session id valid.

Two instance states make the landing a raw error rather than a page — `challenge_visibility ==
"admins"`, and a CTF window closed or not yet open. The plan said redirect to `/` with an
`info_for`; the implementation renders a notice page instead, because `get_infos()` filters flashes
by `request.endpoint` and `views.static_html` never calls it, so a flash aimed at the front page
renders nowhere at all. A full page is the better answer anyway for somebody just bounced out of an
activity. Every refusal renders **one** page, in French, with the reason only in the log: naming the
failing check to an unauthenticated caller is how a token gets ground down a field at a time, and
the talent could do nothing with the answer but start again from Jump.

This route deliberately bypasses the instance's registration path. That is the design — an
instructor-led instance is registration-gated or shut outright — so everything that gate would have
decided is decided here instead: the user cap, the team mode, the ban flag.

### 31.5 Enqueue in the request, send outside it

    solve()   ->  one guarded INSERT, return          (inside the request)
    drainer   ->  recount, sign, POST, mark sent      (outside it)

**The synchronous POST is an availability risk, not merely a slow path.** Under the gevent worker
`requests` does not block the process, but the greenlet keeps its SQLAlchemy connection for the
whole call. With `WORKERS=1` and a pool of 5 plus 20 overflow (`CTFd/config.py:284`), thirty
students solving while Jump is slow exhaust the pool — and what then starts failing is *unrelated*
requests. The blast radius is "the instance falls over because Jump is slow".

A bare background greenlet fixes the latency and loses events in silence on a restart or a 500. XP
that never arrive, with no trace anywhere, is the worst failure this feature has: the student did
the work, Jump disagrees, and nothing says so. Hence a table (`workshop_jump_event`, unique on
`(user_id, challenge_id)`), a retry ladder, and a page an instructor can look at.

The ladder is 5, 10, 20, 40, 80, 160, 320, 600 seconds and then the row stops moving and shows up
on `/admin/workshop/jump` with a button — roughly twenty minutes, which is chosen to be longer than
a Jump deploy, the outage it most has to survive. The drainer re-reads `workshop_jump_keys` at send
time and **drops** rather than retries an event whose `kid` is no longer configured, so retiring a
dev origin does not leave a queue retrying into nothing for ever.

`WORKERS=1` today, so a module-level drainer is safe — the same assumption `syncpage.py` already
makes for its import job. Raising it would need `SELECT … FOR UPDATE SKIP LOCKED` (MariaDB 10.11 is
what runs) or a Redis lock, and `jumpqueue.py` says so.

The body is serialised once and passed as `data=`, never `json=`, which would let `requests`
re-serialise the dict so that the signature no longer covers the bytes on the wire. Signing happens
at send time from the live configuration, because Jump's `verifyCallbackSignature` refuses a
timestamp more than 300 s old — which also means rotating the shared secret does not fail the queue.

### 31.6 The counters are computed in the drainer, from one population

`get_solve_ids_for_user_id` is memoized for 60 s and its invalidation runs *after* the challenge
plugin's hook (`api/v1/challenges.py:855-859`, then `:884-885`). A count taken inside `solve()` is
therefore the count from *before* the solve, and may be a minute stale on top of that.

It also has to be the same count the participant is reading. The rule — a step counts unless it is a
pure note or the content marked it optional — was written once in `page.py` and summed four times
there, with a fifth copy in `answers.py` against `Challenges` rows instead of step dicts. A sixth,
in a background drainer nobody watches, is how "Jump says 7, the page says 8" happens. `progress.py`
now holds the predicate and both counters, and the page, the answer sheet and the outbox all call
it. `jump_check.py` asserts the reported numbers against what `/workshop` renders.

### 31.7 The hook is not total, and this says so rather than pretending

`QuizChallenge.solve()` misses steps of the `standard` challenge type and misses an admin marking a
submission correct (`api/v1/submissions.py:206-213`). A SQLAlchemy `after_insert` listener on
`Solves` would catch both, at the cost of a coupling the plugin README does not list and an
unverified interaction with CTFd's end-of-request `db.session.close()`.

Sliced, and verified on the local instance: `SELECT type, COUNT(*) FROM challenges GROUP BY type`
returns `quiz 36` and nothing else, so overriding `solve()` covers 100% of the MVP content. The
listener and a pull reconciler close the gap together, **before Halloween**. Re-run that query
before assuming this holds on an instance whose content differs.

### 31.8 Provisioning, because two keys set by hand on eleven instances is a thing we forget

`workshop_jump_instance` (this instance's slug, compared against the ticket's audience) and
`workshop_jump_keys` both have a provisioning path in the same change: `deploy/instances.yaml`
carries `jump:` as `{kid: {origin, label}}`, `provision.py` validates it at load time and merges in
one secret per key id from `deploy/secrets.yaml`. **The shared secret is one value per Jump
environment, not per instance** — a Jump deployment reads a single `WORKSHOP_TICKET_SECRET`, so a
per-instance secret would be eleven values Jump has no field for. Either key empty means every
ticket is refused, which is how a half-configured instance fails safely.

`str()` before comparing the slug is not decoration: `get_config` turns an all-digit value into an
`int`, so a slug like `2026` would compare unequal to its own string and the instance would
silently stop accepting every ticket.

### 31.9 What is not verified

**The end-to-end callback against a real Jump.** The Jump half does not exist yet, so
`jump_check.py` forges its own tickets and runs its own sink: what is proven is that this side
sends exactly what the contract says, not that Jump accepts it. The two pinned details in §31.1 are
the likeliest place for the two halves to disagree.

The `X-Idempotency-Key` is the outbox row's natural key, `f"{user_id}:{challenge_id}"`, which is
instance-local. That is safe because Jump dedupes by upserting `grantXp` on a `sourceId` that names
the instance, and the payload carries `instanceSlug`; it would not be safe if Jump ever deduped on
the header alone.

### 31.10 Rejected

- **A synchronous POST from `solve()`** — §31.5. The failure is unrelated requests, not slow ones.
- **A bare greenlet with no table** — loses the event silently on a restart, which is the one
  failure this feature cannot have.
- **Adding PyJWT** — cannot be installed from a bind-mounted plugin, and a fixed algorithm is
  better here anyway.
- **One secret per instance** — Jump has one field for it.
- **Falling back to the single configured secret when a `kid` is unknown** — a verification oracle
  the day a second origin is added.
- **The `after_insert` listener now** — the right shape eventually, but it is coupling plus an
  unverified interaction with CTFd's session teardown, bought for content that does not exist on
  any instance today.
- **`Fields` / `FieldEntries` for the talent id** — it would surface in the admin user form and in
  public profiles.
- **`@ratelimit` as it stands** — it buckets a whole classroom behind one NAT together.

## 32. The supervisor tier: the people running the room, without the admin panel (2026-09-21)

The beta sessions had one account that could read the answer sheet, and it was the admin's. A
teacher walking the room needed the answers, who is where, what was just typed at a step and the
feedback — and the only way to give them that was the password to an account that can also delete
the instance. The role §23.2 had deferred became necessary before the review queue (§11) did, so it
ships alone.

### 32.1 Shape

Exactly what §23.2 laid out, because the constraints have not moved:

- **No third `Users.type`.** The admin user form hardcodes two choices, and every schema's
  `views[view]` lookup (`schemas/users.py`, `teams.py`, `tokens.py`) raises `KeyError` on an
  unknown value — a supervisor typed `supervisor` would 500 `/api/v1/users`. So a supervisor is a
  `user` row that a plugin table, `workshop_staff (user_id, role, created, granted_by)`, names.
  Revision `7d2f5a9c41be` creates it.
- **`hidden=True`, `verified=True`.** Hidden is load-bearing: it is what keeps a supervisor off the
  scoreboard, out of the answer sheet's "who is where" (`answers.py` already filtered on it), out of
  every count in the new pages, and out of `num_users`. A supervisor who walks the subject to see
  what a step looks like costs the room's numbers nothing.
- **`staff_only`** (`staff.py`) is `admins_only` widened to supervisors, with the same two refusals.
  It guards the plugin's read-only pages and nothing else. `is_admin()` stays false for a
  supervisor, which is the safety property the whole design rests on: **a route nobody thought
  about is closed, never open**, and no write route exists behind the wider guard.
- **The way in is a code, not the instance's registration.** `/supervisor/join` deliberately ignores
  `registration_visibility` — the same call `jump.py` makes for the same reason (§31): a production
  instance keeps `/register` a 404, and the people running the room still have to get in. The code
  is `workshop_supervisor_code`, **empty by default, and empty means closed**, so no instance changes
  until an admin fills it in. Constant-time, case-insensitive comparison; rate-limited at 20 per
  five minutes per subject, wide enough for a staff room behind one NAT.

### 32.2 What a supervisor opens

| Page | Where from |
|---|---|
| `/admin/workshop/stats` | new, `stats.py` — participants, started, finished; a histogram of how many steps each person has done; per step the solve count, the share of the room, the share of wrong attempts, and how many people are parked on it right now |
| `/admin/workshop/answers` (+ `.csv`) | the §23 sheet, decorator swapped |
| `/admin/workshop/submissions` | new, `submissions.py` — core's `/admin/submissions` without the admin nav and without the delete controls, participants only, filterable by result, step and name |
| `/admin/workshop/feedback` (+ `.csv`) | the §17 report, decorator swapped |

The two new pages are **not** copies of core's Statistics and Submissions. Core's count a CTF —
points, keys, distinct IPs — and both extend `admin/base.html`, whose nav is inline rather than in a
Jinja block, so a supervisor extending it would see Config, Users and Challenges as links that bounce
to the login page, and the submissions page's delete buttons would 403. Every number on the new
stats page comes from the same readers the answer sheet and the participant's page use
(`progress.py`, `answers._attendees`), so the three cannot disagree.

Which shell a page extends is decided by the view: `staff_base()` hands an admin `admin/base.html`
and a supervisor `workshop_staff_base.html`, a copy of the admin head with the nav reduced to those
four pages, Workshop and Logout. The templates `{% extends base_template %}`. Links into
`/admin/challenges/<id>` and `/admin/users/<id>` on the shared pages render as plain text for a
supervisor. The Epitech shell's navbar gains a "Supervision" entry for staff, since the account
menu's "Admin Panel" is admins only.

### 32.3 Managed from the settings page

`/admin/workshop/settings` (admin only) is where the accounts are managed, so nothing needs
`/admin/users`: set or clear the code, **create** a supervisor account outright (name, email,
password, no code), **grant** the role to an existing account by name or email (the account becomes
hidden, so any progress they made as a participant leaves the reports), **revoke** it (the role goes,
the account stays hidden) or **delete** the account (the way core's `DELETE /api/v1/users` does it,
every table then the user; refused for an account that is not a supervisor, and for oneself).
Clearing the code closes the door and keeps the supervisors already in.

### 32.4 Covered by the suite

`scripts/supervisor_check.py <url> <admin-pass>`, in the shape of the other check scripts. The list
that matters is the refusals: with a supervisor session it asserts every named admin page bounces to
login and every named admin API returns 403 (or 404 for a hidden account), that the code and the
mode cannot be changed, and that the supervisor is absent from the public user list and from the
answer sheet. Then the door: 404 while the code is empty, refused on a wrong code with no account
created, and a login-page link only while it is open. Then management: grant, revoke (and the
revoked account still signs in as a participant), create, a duplicate refused with the reason,
delete refused for a non-supervisor and honoured for one. It restores the code and deletes what it
made.

### 32.5 Not in this

The §11 review queue, supervisors arriving through Jump, provisioning the code per instance in
`deploy/secrets.yaml`, and any ability for a supervisor to validate a checkpoint or edit content.

## 33. The runtime nobody opened (2026-09-21)

In a test round with real participants, people reached **Partie 1, étape 0** without the runtime
open, and read past the line that tells them to open it.

### 33.1 The line was not the problem

« En haut de cette page, clique sur le bouton **Ouvrir Pac-Man** » is unambiguous. What it points
at is the problem: §30 gave the page **two launchers for one action**, and `runtime.js`
(`updateLauncher`) shows exactly one at a time — the labelled hero button while it is in view, the
fixed edge tab once it has scrolled away. That sentence sits in the « 🥸 Mise en application »
section, well below the hero. By the time it is read, the thing it names is off screen and a
different control has taken over. The text was describing a page state that no longer existed.

Which also says where the fix belongs: not in the prose, because no wording can name a control
whose identity depends on scroll position. Only `runtime.js` knows which launcher is live.

### 33.2 A cue, not a tour

The author marks the moment with `<!-- ws:cue runtime -->` (convention §3.4d, same grammar as
`ws:toolbox`), `cue.py` turns it into a 1px anchor, and `runtime.js` observes it with an
`IntersectionObserver` trimmed to the middle fifth of the viewport. Whichever launcher is on
screen pulses three times.

Two tour libraries were considered first and both rejected, for reasons specific to this page:

- the target is `hidden` half the time — `.ws-runtime-handle` ships with the attribute and
  `updateLauncher` clears it — and a tour measures its target's rectangle when the tour starts;
- every step is a `<details>`, so anything running on page load is pointing into collapsed
  accordions;
- a tour is modal, arrives once, and is dismissed. This signal is ambient, recurs at every step
  that needs the runtime, and must not cost a click;
- intro.js is AGPL-3.0 without a commercial licence, on a platform deployed over a network to a
  school.

An `IntersectionObserver` was already in the file (`watchCta`) for the launcher handover, so the
cue cost ~40 lines and no dependency.

**A tour is still the right tool one layer down.** The split settled on: the platform owns the cue,
on a control it owns, whose position only it knows; a runtime owns any tour of its own interface,
inside its frame, where every target is present and stays put. That is a separate piece of work and
nothing here forecloses it — `ws:cue <name>` is a grammar, and a tour is a second name rather than
a second syntax.

### 33.3 The rules it keeps, and the one that changed

Nothing when the runtime is already open, and the mark stays armed, so closing it and coming back
still works.

It then **pulses until a launcher is pressed**. The first version stopped after three pulses, on
the reasoning that a control pulsing forever becomes wallpaper. That reasoning describes somebody
who has seen the control; the participant this exists for is the one who never noticed it, and for
them a signal that gives up after four seconds is aimed at the wrong person. Scrolling past does
not end it either — that is the case the cue exists for. Only the press does, cleared in
`activate` rather than only in `updateLauncher`, because a press that opens a tab is answered
before that tab has said anything back over the channel.

Which puts real weight on `prefers-reduced-motion`, and it carries it: a still ring, held exactly
as long and ended by the same press. An indefinite pulse is precisely what somebody asking for
less movement is asking not to get, and they do not get it. Nothing anywhere waits on
`animationend` — the global rule in `epitech-theme.css` collapses every animation to 0.01ms, so
the version with no animation has no event to end on.

A mark inside a collapsed step waits and fires when the step is opened, for free: an
`IntersectionObserver` says nothing about an element with no box.

### 33.4 What else it touches

`tools/ws_parser.py` had to learn the marker, or the metadata parser would have tried to read
`cue runtime` as YAML. The negative lookahead that already skipped the fenced regions now skips
standalone marks too. That file is **vendored from `workshop-content-tools`**, so
`tools/check_parser_sync.py` fails until the same two lines are pushed there — the divergence is
loud by design (convention §3.9) and this is exactly the case it exists for.

`scripts/phase2_validate.py` asserts no authoring marker leaks into a stored description. `ws:cue`
is the third that legitimately stays, beside `ws:context` and `ws:resume`: stored, consumed at
render time, never shown.

### 33.5 Not in this

The subject's own wording. « En haut de cette page » is still wrong for the same reason the cue
exists, and fixing it is a content decision in the subject repo, not this one. A subject with no
mark behaves exactly as before.

## 34. The image you cannot read (2026-09-21)

Content images are capped at `max-height: 26rem` (§29). The cap earned its place: a 750px
screenshot pushed the first step of a part below the fold, and a page whose first screen is one
picture reads as a gallery rather than a workshop.

### 34.1 But the cap fights the image's job

A subject screenshot exists so a participant can hold it against their own screen: *is this what
I should be seeing?* That is criterion V1, and the whole reason the workshop has images at all.
A console showing six lines of a Lua error, or the interface shot with a yellow focus ring on one
of three panes, does not survive being scaled to 26rem. The cap is right for the page and wrong
for the moment.

Both can be true at once, which is what a click is for. `assets/zoom.js` opens the image in a
`<dialog>` at up to 78vh.

### 34.2 And the caption was never on screen

The authoring convention treats an image's `alt` as its caption — `workshop-capture` says so
outright: *« The alt text is the caption. It is the only place text belongs (never in the image),
so write a sentence saying what the reader should be seeing. »* Authors have been writing those
sentences all along. The page rendered them into an attribute, where a screen reader reached them
and no one else did.

So the two are one feature: enlarging is exactly the moment that sentence is worth showing, and it
arrives with the image. An image with no `alt` gets no caption bar rather than an empty one.

### 34.3 Choices worth keeping written down

A `<dialog>`, not a positioned div: the top layer clears the runtime pane (z-index 1025) without
either of them knowing about the other, and Escape, the backdrop and the focus trap stop being
ours to write. Focus is parked on the dialog rather than left to land on the close button, because
Chromium treats that programmatic focus as `:focus-visible` and the thing opened with a keyboard
ring drawn on it under a mouse click.

Registered globally, like `hints.js` and unlike the page's own bundle: `.challenge-desc` is core's
container, the board at `/challenges` renders subject images too, and this plugin's CSS has always
styled them there. Two kilobytes, no dependency, one behaviour instead of one per surface.

**No `tabindex` on the images.** A screen reader already announces the `alt`, which is all the
dialog adds, so the tab stop buys an assistive-technology user nothing while costing every keyboard
user one stop per image on a page that is mostly images, between them and the answer field. The
trade runs the other way for a sighted keyboard-only reader; `workshop.css` carries the comment
marking where to change it.

### 34.4 Found by looking, not by reasoning

Three defects survived a careful write and died on the first screenshot: the close button sat on
top of the enlarged image (positioned against a dialog that shrinks to its content), the backdrop
at 0.82 left the step text behind it readable enough that the eye kept going back to it, and the
focus ring above. A fourth arrived with the fix — parking focus on the dialog made the UA draw a
ring round the whole thing.
