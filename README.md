# Workshop platform

CTFd as the backend for a **workshop** — guided, ordered exercises for beginners, rather than a
capture-the-flag. It is a CTFd **plugin** plus a content pipeline: nothing in CTFd's own source is
modified, so upgrading CTFd stays a `git pull` of the submodule.

Built for and running a real programme: six workshops, 147 exercises, five live instances,
audience French *lycéens* aged 15 to 18.

## What it gives you

- **Progressive unlocking**, free. `Challenges.requirements` is already a JSON column of
  prerequisites, and CTFd already filters locked challenges out of the API and refuses their
  submissions. Zero lines were written for it.
- **One page per subject** instead of a card grid with a modal per challenge — steps as
  accordions, a stepper, the current step open on load.
- **Existing web apps embedded as runtimes.** An editor or emulator that already runs in a browser
  is built once, served same-origin from the instance, and driven by a small adapter. It needs no
  modification to work here.
- **A `quiz` challenge type** — single, multiple, match, freeform regex, plus read-and-acknowledge
  and a rating step that is how end-of-workshop feedback gets collected.
- **A content pipeline.** A folder of Markdown becomes a live workshop; re-running the import
  updates in place and participants keep their solves.
- **Work in progress kept server-side**, per participant, restored before the runtime boots.
- **Two admin pages** the stock panel has no equivalent of: an answer sheet (every step's answer as
  *this* instance will accept it, plus who is where) and a feedback report.

## What is not in this repo

- **The workshop content.** Each subject is its own public repo — see below. `deploy/instances.yaml`
  expects them under `content/`.
- **`deploy/secrets.yaml`.** Generated on the first `provision.py` run; it holds the admin
  passwords, session keys and registration codes, and is gitignored.
- **Built runtime dists** (`plugins/workshop/runtimes/`). `tools/build_runtime.sh` rebuilds them
  from a pinned commit.

This is a single-commit snapshot of the state that was deployed, not a fork with history.

## Layout

```
plugins/workshop/   the plugin — bind-mounted over CTFd/CTFd/plugins/workshop, never a core edit
tools/              ws_parser (parser + linter), sync_subject, sync_workshop, provision,
                    build_runtime, answers.sh
scripts/            phase*_validate — the regression suite, 151 checks against a fresh instance
                    content_visibility_check — every authored block on the page, in order
docs/               CONTENT_CONVENTION.md (the authoring convention), CONTRIBUER.md (French,
                    for content authors), DEPLOY.md (the production runbook)
compose/            one env file per instance: port and data directory
deploy/             instances.yaml + the nginx vhost templates
CTFd/               submodule -> kevin-cazal/CTFd, a fork of Manta-Epitech-Academy/CTFd
PLAN.md             the design of record: 24 numbered decisions and why they went that way
```

## Getting started

```bash
git clone --recurse-submodules https://github.com/kevin-cazal/workshop_platform
cd workshop_platform

# A subject to import. They are separate repos; instances.yaml expects them here.
git clone https://github.com/kevin-cazal/pypong_subject content/pypong

docker compose up -d                       # stock CTFd on :8080, plugin bind-mounted
# run the setup wizard in the browser, then:
python3 tools/sync_subject.py content/pypong \
    --url http://localhost:8080 --admin-user admin --admin-pass '<the one you just set>'
```

`docs/DEPLOY.md` is the runbook for a real deployment: nginx, certbot, one instance per subject,
backups, and how to ship a change to instances that already exist.

## The subjects and runtimes

Content and runtimes live in their own public repos, each a single-commit snapshot of the version
that was deployed. Authored answers are committed only GPG-encrypted.

| Subject | Runtime |
|---|---|
| [pypong_subject](https://github.com/kevin-cazal/pypong_subject) | [tic80-web-editor_runtime](https://github.com/kevin-cazal/tic80-web-editor_runtime) |
| [santa_shooter_subject](https://github.com/kevin-cazal/santa_shooter_subject) | [tic80-web-editor_runtime](https://github.com/kevin-cazal/tic80-web-editor_runtime) |
| [pacman-ghost-ai_subject](https://github.com/kevin-cazal/pacman-ghost-ai_subject) | [pacman-ghost-ai_runtime](https://github.com/kevin-cazal/pacman-ghost-ai_runtime) |
| [shell-rpg_subject](https://github.com/kevin-cazal/shell-rpg_subject) | [shell-rpg_runtime](https://github.com/kevin-cazal/shell-rpg_runtime) (v86) |
| [shell-1_subject](https://github.com/kevin-cazal/shell-1_subject) | [shell-rpg_runtime](https://github.com/kevin-cazal/shell-rpg_runtime) (v86) |
| [miniasm_subject](https://github.com/kevin-cazal/miniasm_subject) | [miniasm_runtime](https://github.com/kevin-cazal/miniasm_runtime) |

## Two rules the code keeps

- **Never edit inside `CTFd/`.** It is a submodule and stays pristine, so upstream updates remain a
  `git pull`. Everything custom is in `plugins/workshop/`.
- **Platform strings are English, content is the audience's language.** Every string the platform
  emits into the CTFd UI is English until real internationalisation lands; the workshops themselves
  are written in French. `plugins/workshop/README.md` lists exactly which CTFd APIs the plugin
  touches, so a future CTFd version can be ported against it.
