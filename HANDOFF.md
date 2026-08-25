# Session handoff — updated 2026-08-19

Notes for picking this up on another machine. Read `CLAUDE.md` first (rules), then `PLAN.md`
(design of record, §22 is the most recent decision; §22.5 is what the deploy actually hit), then this.

An earlier version of this file described a planning-only repo with an SPA design that was never
built. That is obsolete: the SPA is on hold and the participant UI now lives inside CTFd. The
archived transcript of the original planning session was removed when this repo was made public
(see the bottom).

## Where we are

Working on **`main`** (renamed from `mockup` on 2026-08-18). Phases 0, 1 and 2 of `PLAN.md` are done, plus §13 (participant
UI), §14 A–D (runtime embedding, including the pacman content migration), §15 (topologies),
§16 (work in progress kept server-side), §17 (the feedback report), §18–§20 (phase E: the v86
family, multi-subject workshops, shell-1's 42 challenges), §21 (phase F: the MiniASM port and
`validation: token`) and **§22 (the production deploy layer)**.
A subject repo goes into a CTFd instance with one command, and a participant works through it
on a single page with the TIC-80 editor beside the instructions.

Six subjects are converted, in five deployable units: **pypong** (single part), **pacman** (two
parts, its own runtime), **santa_shooter** (four parts, optional bonuses and a free-choice
chapter — the real workshop of `CLAUDE.md`, and what §15 was built for), **shell_rpg + shell_1**
composed as the Discover Linux workshop, and **miniasm** (a self-grading runtime, validated by
token).

```
plugins/workshop/   the CTFd plugin, bind-mounted over CTFd/CTFd/plugins/workshop
  quiz.py           `quiz` challenge type: single | multiple | match | freeform (regex)
                    + ack (read-and-acknowledge) + rating (the closing feedback step)
  page.py           GET /workshop — the whole subject as one page, steps as accordions
  graph.py          GET /api/v1/workshop/graph — prerequisite DAG for the current user
  runtime.py        serves a runtime dist same-origin at /runtime/<id>/<version>/
  workspace.py      work in progress per participant, restored before the frame boots
  feedback.py       /admin/workshop/feedback — what the ratings actually say
  answers.py        /admin/workshop/answers — every step's answer and who is where
  landing.py        / and the post-login default redirect to /workshop
  assets/, templates/
deploy/             the production deploy (PLAN.md §22)
  instances.yaml    the five instances: name, port, content. Committed.
  nginx/            vhost templates + the shared websocket-upgrade map
  secrets.yaml      GENERATED, gitignored, and the only copy of the admin
                    passwords. Back it up.
tools/
  provision.py      render -> up -> setup -> runtimes -> sync, per instance
  ws_parser.py      shared parser + linter for the content convention
  check_content_sync.py  vendored content vs. the subject repo it came from
  sync_subject.py   idempotent subject repo -> CTFd import
  sync_workshop.py  several subjects as one workshop, starter gating advanced
  build_runtime.sh  builds a runtime dist for its mount path (tic80, pacman, v86)
  import_shell1.py  shell-1-challenges -> a convention 2.0 subject, re-runnable
  import_miniasm.py miniasm's own data -> a subject, read through node
content/            subjects in convention 2.0: pypong, pacman, santa_shooter,
                    shell_rpg, shell_1, miniasm
  workshops/        workshop.yaml manifests: discover-linux (Shell RPG then
                    Shell 1), tic80-double (PyPong then Santa Shooter)
scripts/            phase0/1/2 validation — phase2 is the live one, 132 checks
  workshop_check.py multi-subject composition, against a composed instance
  secure_context_check.js  the deploy-day check: a MiniASM token derives over
                    https and cannot over plain http (needs Playwright)
docs/               CONTENT_CONVENTION.md, RUNTIME_PROTOCOL.md, DEPLOY.md
.claude/agents/     subagent briefs, committed on purpose (miniasm-ux owns the
                    MiniASM runtime's UI and never touches the CTFd page)
frontend/           the early React mockup — superseded, reference only
```

## Running it

```bash
docker compose up -d                      # stock CTFd on :8080 (8000/8001 are taken)
# then the setup wizard by hand, or let scripts/phase2_validate.py do it
tools/build_runtime.sh tic80              # builds the editor dist (gitignored, ~13 MB)
python3 tools/sync_subject.py content/pypong --url http://localhost:8080 \
    --admin-user admin --admin-pass <pw> --codes instructor_codes.pypong.yaml
```

The pacman subject runs on its own instance (`compose/pacman.env`, :8081). Local admin
credentials live in `.env.local`, which is gitignored — the setup wizard is run by hand, so
nothing else records them. If a password is lost, reset it in the container rather than
rebuilding the instance (`Users.password` hashes on assign, so store the plaintext, never
`hash_password()` output — double-hashing it produces a 403 on the admin API, not a login
failure, which is a confusing way to find out):

```bash
docker exec ctfd-pacman-ctfd-1 sh -c 'cd /opt/CTFd && PYTHONPATH=/opt/CTFd python -c "
from CTFd import create_app
app = create_app()
with app.app_context():
    from CTFd.models import Users, db
    u = Users.query.filter_by(type=\"admin\").first(); u.password = \"newpass\"
    db.session.commit()"'
```

Reset to a fresh instance (`.data/` is root-owned, hence the alpine container):

```bash
docker compose down
docker run --rm -v "$PWD/.data:/data" alpine sh -c 'rm -rf /data/*'
docker compose up -d
```

**`scripts/phase2_validate.py` runs the setup wizard, so it needs a fresh instance and wipes
whatever is there.** That is the main footgun: ask before running it against an instance someone
is looking at. Give it its own, on :8082, and no such question arises:

```bash
docker compose --env-file compose/validate.env -p ctfd-validate up -d
python3 scripts/phase2_validate.py http://localhost:8082
docker compose --env-file compose/validate.env -p ctfd-validate down
docker run --rm -v "$PWD/.data-validate:/data" alpine sh -c 'rm -rf /data/*'
```

The suite takes the base URL as its one argument and defaults to :8080 — which is the pypong
instance, so **never run it bare**.

## Things that will bite you

- **A v86 workshop's VM never touches the instance.** `build_runtime.sh shell-rpg` stages a
  4.5 MB dist; the ~330 MB `.v86b` comes from a CDN, its mirrors, or `compose/mirror`, and the
  participant picks the downloaded file in the runtime's welcome screen. v86 needs **no**
  cross-origin isolation — measured, PLAN.md §18.2 — so CTFd does not have to send COOP/COEP.
- **Runtime dists are gitignored.** `plugins/workshop/runtimes/` is rebuilt by
  `tools/build_runtime.sh`, never committed. Without it the pane silently does not render (by
  design — a page never points at a 404). The TIC-80 WASM is *not* in the tic80-web-editor repo
  either; the script pulls the compiled artifact from that project's GitHub Pages build rather
  than spending 20–40 minutes on an Emscripten compile.
- **Three validation modes.** `token` joins them: a runtime that grades itself reveals a token
  derived from a per-instance secret (`workshop_token_secret`, minted once and never rotated),
  and the sync derives the same value as the flag. `TOKEN_SCHEMES` in `sync_subject.py` must
  match the runtime's own derivation — for MiniASM that is `js/token.js`, and both have tests.
- **Two other validation modes.** `checkpoint` (default) generates a code per exercise for the
  instructor; `validation: flag` takes the answer from a `flags.yaml` sidecar, for a subject
  where the answer *is* the flag. A flag subject's sidecar holds answers — protect it the way
  shell-1 protects `flag.txt.gpg`.
- **Validation codes must survive a re-sync.** `instructor_codes.<subject>.yaml` is the source of
  truth; sync only generates a code for an exercise that has none, so codes already handed to
  attendees stay valid.
- **The answer sheet is a page, not a file.** `/admin/workshop/answers` reads the live database,
  so it is right for all three validation modes at once — authored flags, per-instance checkpoint
  codes, per-instance runtime tokens — and it shows quiz answers, which nothing else in CTFd
  does. The sync records `{challenge_id: mode}` into the `workshop_validation` config key so the
  page can label a row instead of guessing at the shape of the stored flag.
- **Renaming a slug orphans a challenge.** The sync keys on the admin-only Topic
  `ws:<subject>:<slug>`, so a slug that changes shape produces a *second* challenge and leaves
  the first behind with its solves. Migrate by renaming the Topic before syncing, not by
  deleting the challenge after. This bit the closing step, whose slug moved from `__outro__` to
  `__outro__-<document>` when each document gained its own close; both local instances were
  migrated that way on 2026-08-05.
- **A running container holds the plugin in memory.** `plugins/workshop/` is bind-mounted, but
  the module is imported once at boot, so an instance that has not been restarted since a plugin
  change still serves the old routes and the old navbar. `docker restart <ctfd container>` after
  editing the plugin, or you will debug a page that no longer exists.
- **A level-1 or level-2 heading names the part it opens**, unless it carries an explicit
  `<!-- ws: {type: prose} -->` marker. That marker is the only way to write an explanation
  mid-chapter without renaming the part for every exercise below it, and it is what
  `content/santa_shooter/code_refactor3.md` uses. Unmarked headings behave as they always did.
- **Vendored content can drift from its subject repo**, so `tools/check_content_sync.py`
  compares them (via `gh api`, since the repos are private) and the validation suite runs it.
  `content/pypong` is deliberately unmapped: it is a *conversion* of `pypong_new`, which is
  still schema 1.5 in another org, so comparing them would report the conversion as drift
  forever. It reports `skip`, which is not a pass.
- **Every platform string is English** (CLAUDE.md). Workshop *content* stays French.
- **`get_config` returns an int** when the stored value is all digits
  (`CTFd/utils/__init__.py:51`), so a config holding a bare id arrives already parsed while a
  config holding a JSON list arrives as a string. Mixing the two up fails silently: the reader
  swallows the TypeError and the feature just never happens.
- **CTFd caches solve sets.** Deleting solves straight from MariaDB will not show up until the
  cache container is restarted.
- The active theme directory is `core`, not `core-beta`. Upstream promoted `core-beta` to `core`
  (`CTFd/CHANGELOG.md:223`), so any older doc or snippet pointing at `core-beta` is stale — the
  only other directory here is `core-deprecated`.

## The deploy is done

Five instances are live over HTTPS since 2026-08-19, ahead of the 2026-08-21 target:

| | | |
|---|---|---|
| https://pypong.ealab.duckdns.org/ | 15 challenges | TIC-80 |
| https://pacman.ealab.duckdns.org/ | 19 | pacman runtime |
| https://santa.ealab.duckdns.org/ | 36 | TIC-80, four parts |
| https://discover-linux.ealab.duckdns.org/ | 48 | v86, two subjects |
| https://miniasm.ealab.duckdns.org/ | 29 | MiniASM, token validation |

They run from `/srv/workshop` on `<server-ip>` (`debian` user, passwordless sudo), managed with
`tools/provision.py`. Credentials: `python3 tools/provision.py secrets` on the server, backed by
`deploy/secrets.yaml` — gitignored, and the **only** copy of the admin passwords. A second copy
sits in the local `deploy/` directory. Registration is by a shared code, same code on all five.

`PLAN.md` §22.5 records what the server turned out to differ on; `docs/DEPLOY.md` has been
corrected accordingly and is the file to follow for the next one.

## What is next

Ranked, from `PLAN.md`:

1. **§11 — the instructor review queue.** Designed in detail, entirely unbuilt, and the missing
   half of instructor-led mode.
2. Deferred: the checkpoint challenge type (validation codes are the interim mechanic), i18n, and
   §10 `provision(repo_url, ref)` in full — `tools/provision.py` is only its manual half.

Phases 3 (SPA) and 5 (TIC-80 track) stay **on hold** — see the notes in `PLAN.md` §6.

## Still open

Nothing blocking. Both questions that stood here are settled: **user mode**, not teams, is the
default for every instance, and the **scoreboard is kept**.

Resolved since the first handoff: the outline-vs-prose question (both modes exist, and a subject
repo carries the full prose — §9, §13), the runner (deferred indefinitely; browser runtimes plus
checkpoint validation cover the catalog), and **work in progress on shared machines** (§16 —
snapshotted per participant, restored before the frame boots, and a bucket owned by another
student is wiped rather than inherited).

## This repo is public (2026-08-24)

Two things were scrubbed from the whole history with `git-filter-repo` before the switch, so
every commit sha below the rewrite changed. Anyone holding an older clone — including
`/srv/workshop` on the server — must re-clone rather than pull.

- **The archived planning transcript** (`docs/session-2026-08-03.jsonl`, ~458K of raw session
  log) is gone. It carried local absolute paths and a directory listing of other projects under
  `~/Work`. It exists only in the pre-scrub bundle kept outside the repo.
- **The server address** is written `<server-ip>` throughout. This is tidiness, not a control:
  the five hostnames are public and resolve to it.

**Authored answers are encrypted, and must stay that way.** `content/shell_1/flags.yaml` (42
answers) and `content/pypong/quiz_answers.yaml` are committed only as `.gpg`; the plaintext is
gitignored. Two answers that had been quoted as examples in `PLAN.md` and
`docs/CONTENT_CONVENTION.md` were replaced with `shell1{exemple}`.

```bash
GPG_PASSPHRASE=... tools/answers.sh decrypt   # before a sync
GPG_PASSPHRASE=... tools/answers.sh encrypt   # after editing an answer
```

The passphrase lives in `deploy/secrets.yaml`, which is gitignored and still the only copy.

Five `shell1{...}` values remain readable in `content/shell_1/*.md` on purpose: they are
read-and-acknowledge steps whose flag the text hands the participant.

## Content and runtime repos

Each subject and each runtime is now its own public repo under `kevin-cazal`, named
`*_subject` / `*_runtime`, each a single-commit snapshot of the deployed version. Every runtime
repo builds a container image to GHCR on push to `main`. `content/upstreams.yaml` still records
where a vendored subject came from.
