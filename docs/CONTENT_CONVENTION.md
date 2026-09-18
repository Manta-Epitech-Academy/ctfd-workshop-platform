# Workshop content convention — proposal

Status: implemented. **Published**, without the references to this repository's private design
docs, at [kevin-cazal/workshop-content-tools](https://github.com/kevin-cazal/workshop-content-tools),
together with `ws_parser.py` and a reusable GitHub Actions workflow subject repos call.

That published `ws_parser.py` and `tools/ws_parser.py` here must stay identical — it is what makes
"what CI accepts, the platform imports" true rather than aspirational. `tools/check_parser_sync.py`
fails when they drift.

Builds on the existing implementation:
[`pypong_new`](https://github.com/Manta-Epitech-Academy/pypong_new) (example workshop),
[`workshop-metadata-tools`](https://github.com/Manta-Epitech-Academy/workshop-metadata-tools)
(schema 1.5 + validators), [`ref_comp`](https://github.com/Manta-Epitech-Academy/ref_comp)
(competency framework + observables).

## 1. Requirements (from the maintainer)

Must:

- Markdown, human readable and editable.
- Metadata **invisible** when the `.md` is rendered by GitHub or any common renderer.
- The convention must not limit the content writer.
- Optional metadata attachable to a workshop section: rewards (points), skills
  (competency-framework references).

Should:

- Repo update → platform reflects it immediately (webhook) or via one admin button.
- Existing Notion content ported to individual GitHub repos later.

## 2. Assessment of the existing implementation

What it gets right (keep):

- **Markdown stays 100 % clean** — all metadata in a separate `metadata.yaml`. This already
  satisfies the invisibility requirement for section metadata.
- One repo = one workshop, one manifest, schema-validated, reusable CI workflow.
- Competency linkage is designed end to end: `skill_path` → framework → per-project observables.
- `generate_readme.py` gives a good GitHub landing page for free.

What breaks in practice (change):

1. **The `toc` mirrors every heading title verbatim.** Rename a heading and the manifest is
   stale; `check_toc.py` catches it, but the author must now fix the same string in two places.
   The pypong manifest already carries frozen typos ("Limitez les mouvement",
   "Dessinez  la balle") *because* they must match exactly. Title-as-key is inherently fragile.
2. **Attachment by title cannot handle repeated headings.** `santa_shooter/starter1.md` has
   "Sprite" ×3 and "Input" ×2. Position-based matching is the only fallback and it silently
   shifts when a section is inserted.
3. **The quiz marker is visible.** `> QUIZ.A2.3 Lecture de documentation` renders as a
   blockquote line on GitHub — exactly the kind of artifact the requirements forbid.
4. **No platform fields.** Nothing carries points/rewards, workshop mode
   (self-serve / instructor-led), workspace kind, validation kind, or chapter topology
   (linear vs free-choice) — all needed by the platform (PLAN.md §9).
5. **"Which heading is an exercise" is implicit** (title starts with "Exercice…"?). The
   Notion-era gotcha — prose-only headings mixed with real steps — has no explicit answer.

Verdict: the architecture (md + sidecar manifest + shared tooling) is sound. The flaw is
*where section-level metadata lives*. Fix: move it **into the markdown, invisibly**, and derive
the TOC instead of authoring it.

## 3. The convention

### 3.0 Terminology (aligned with CLAUDE.md)

| Term | Definition | Materialized as |
|---|---|---|
| **Subject** | One coherent content unit (PyPong, Santa Shooter, …). What `pypong_new` and earlier drafts called a "workshop". | **One git repo** with `subject.yaml` + md |
| **Workshop** | A composition of subjects: one **starter** (always done first) plus zero or more **advanced** subjects, ordered or free-choice. | A **workshop repo** with `workshop.yaml` (§4.1) — or a single subject repo deployed directly (implicit one-subject workshop) |
| **Session** | A workshop done at some place, at some moment, by identified people (usually instructor-led). A CTFd instance can also be **session-free**: anyone, anytime, self-serve. | **One CTFd instance** (see PLAN.md §10) |

### 3.1 Repo layout (one subject = one repo)

```
my-subject/
├── subject.yaml        # subject-level manifest (identity, runtime, platform config)
├── intro.md             # content, any number of .md files
├── main.md
├── img/…                # images, referenced by relative path — never hotlinked (§3.8)
├── README.md            # GENERATED — never edited by hand
└── .github/workflows/verify-content.yaml   # calls the reusable CI
```

### 3.2 `subject.yaml` — subject-level only

Everything that describes the *subject*, nothing that describes a *section*. No `toc` —
structure is derived from the markdown by the parser.

```yaml
schema_version: "2.0"

project:
  name: "PyPong"
  slug: "pypong"
  summary: "Créez votre propre jeu Pong simplifié en Python avec TIC-80 !"
  entrypoint: "intro.md"

runtime:
  engine: "TIC-80"          # tic80 | python | p5js | external | none → workspace panel
  language: "python"        # Monaco language id

platform:
  mode_default: instructor_led  # self_serve | instructor_led — the instance decides (§3.6)
  validation_default: checkpoint   # checkpoint | tests | flag | review
  points_default: 25        # per exercise, when not overridden

documents:                  # reading order; replaces both documents[] and toc[]
  - intro.md
  - main.md

skills_framework: "https://github.com/Manta-Epitech-Academy/ref_comp"  # pinned ref optional
```

### 3.2b `cover:` — how a subject shows its face

A workshop's front page used to render a title on an empty background. Every
subject already carried a one-line `project.summary` that nothing displayed, and
almost all of them open with a screenshot. So the platform now shows an accroche
and a picture at the top of the workshop — and the point of writing it down here
is that **the subject supplies a sentence and a file, and never a layout**.

```yaml
cover:
  media: img/jeu-demo.gif      # a still or an animation
  poster: img/jeu-demo.png     # optional still, see "reduced motion" below
  tagline: "Ton fantôme ne bouge pas. À toi de lui apprendre à chasser."
  mascot: img/fantome.png      # optional, used by the platform's reward moments
```

No size, no crop, no alignment, no class. The crop, the frame, the type scale,
the breakpoint at which the picture moves under the words, and the rule about
motion are all the platform's, which is what makes four subjects arrive looking
like one platform instead of four.

**Three levels, and only the first costs you anything.**

| Level | What you write | What the participant sees |
|---|---|---|
| Nothing | — | Accroche from `project.summary`, picture = the first image of your `entrypoint` document. |
| `cover:` in `subject.yaml` | three lines | Your sentence, your chosen frame. |
| `cover:` under a document's `#` | three lines | That part gets its own promise and its own picture. |

The derived level is the floor, not a fallback nobody meant: it is why no
subject can be blank, and why declaring a cover is an improvement rather than a
prerequisite. A part with no cover of its own shows the subject's picture but
not the subject's tagline — that sentence is the front door's promise about the
whole subject, and repeated over each part it stops being read.

**A `cover:` path resolves against the subject root, and that is a different
rule from §3.8.** Images inside a document resolve relative to *that document*,
because that is how every markdown renderer resolves them and what you see on
GitHub is what imports. `subject.yaml` has no document to be relative to, so its
paths start at the repo root. Today the two coincide for every converted subject
because their `.md` files sit at the root; they stop coinciding the day one puts
its documents in a subdirectory. A cover declared under a document's heading is
document-relative, like everything else written inside one.

Otherwise `cover` images are ordinary assets: §3.8 applies in full — repo-
relative, never hotlinked, uploaded once and content-hashed by the importer.

**Reduced motion.** An animated GIF cannot be paused, so a participant whose
system asks for reduced motion gets the animation anyway. Declare `poster` and
the platform shows that still to them instead. The linter says so when it sees
a GIF with no poster.

**The linter advises here, it does not refuse.** No block at all, a missing
tagline, an overlong one, a GIF without a still: those print as `warn` and
import anyway. "Your cover has no accroche" must never be the reason a workshop
fails to load ten minutes before a session. You get **one** message per
situation — a subject that declares no cover hears about the block, not also
about the tagline inside the block it does not have.

The one thing that *is* refused is a `cover` path pointing at a file that is not
there. That is §3.8's rule, not a new one: a broken image is a hole in the page,
and the whole point of linting paths is to find it before a session does.

### 3.3 Section metadata — HTML comments anchored to headings

HTML comments are invisible in GitHub, GitLab, VS Code preview, pandoc — every mainstream
renderer. They live *next to the content they describe*, so renaming a heading, inserting a
section, or reordering never desynchronizes anything.

A `ws:` comment placed **immediately after a heading** attaches to that heading. YAML inside:

```markdown
### Exercice 1 : Faites bouger le pad dans l'autre direction
<!-- ws:
type: exercise
points: 50
skills: [PROG/02/A1]
obs: [pypong.2]
-->

En vous inspirant du code précédent, vous devez faire en sorte que le pad
puisse bouger à droite comme à gauche.
```

Fields (all optional — an empty `<!-- ws: type: exercise -->` is a valid minimal marker):

| Field | Meaning | Default |
|---|---|---|
| `type` | `exercise` \| `chapter` \| `prose` | `prose` — **headings without a marker are plain prose**, which solves gotcha #5 explicitly |
| `id` | stable slug for this node | slugified heading, scoped by parent slugs (`bullet/sprite`) — explicit `id` only needed when even the scoped slug collides; the linter enforces uniqueness |
| `points` | reward on validation | `platform.points_default` |
| `validation` | overrides `validation_default`: `checkpoint` \| `flag` \| `token` \| `quiz` (`tests`, `review` are specified, not implemented) | inherited |
| `token_id` | `validation: token` only: the exercise's id **inside its runtime**, which is what the token is derived from | none |
| `skills` | competency refs, slash notation `DOMAIN/SKILL/LEVEL` | none |
| `obs` | observable ids from `ref_comp` (`slug.N`) | none |
| `topology` | on `type: chapter`: `linear` \| `free` | `linear` |
| `requires` | explicit prerequisite ids, for cross-chapter gating | previous sibling (linear) / chapter entry (free) |
| `optional` | on `type: exercise`: bonus work — unlocks with its neighbours, gates nothing, **counts towards nothing** | `false` |
| `review` | instructor-led only: `non_blocking` \| `blocking` — whether an unreviewed submission gates progression (§3.5) | `non_blocking` |
| `rewards` | open object for future extensions (badges, …) | none |

Writer freedom: heading levels are **not** prescribed. A chapter is whatever heading carries
`type: chapter`; an exercise is whatever heading carries `type: exercise`, at any depth.
Everything between two markers belongs to the preceding node. A subject with zero markers is
valid — it imports as a single read-only document (progression tracked per document).

**Which headings name a part.** The exercises of a subject are grouped into *parts* (the
challenge `category`, and the section headers of the workshop page). A part is opened by a
chapter, or by any **unmarked** heading above level 3 — that is where every part of every
existing subject comes from, and it stays that way.

The exception is what `type: prose` is for. Writing it explicitly says *"a heading inside the
current part"*, and such a heading no longer opens one:

```markdown
## Refactor du code            <- opens the part (unmarked)

## Notion d'objet en Lua       <- explains something, mid-chapter
<!-- ws: {type: prose} -->

## Joueur                      <- still in "Refactor du code"
<!-- ws: {type: exercise, id: refactor-joueur} -->
```

Without the marker, "Notion d'objet en Lua" would quietly rename the part for every exercise
after it. That is the answer to a real ambiguity: an explanatory heading and a part heading are
textually identical, so only the author can tell them apart — and now they can, in one line,
without moving the heading or changing its level.

### 3.3b What proves a step is done

Four modes are implemented, and the difference is who knows the answer:

- **`checkpoint`** (default) — somebody has to say the work is done. The platform generates one
  code per exercise and writes the sheet to `instructor_codes.<subject>.yaml`; the instructor
  reads a code out when they have seen the work. Codes never rotate on a re-sync, so one already
  handed out stays valid. **In a self-serve instance there is nobody to ask**, so the same step
  offers a button instead and the participant validates it themselves — the code stays stored,
  unrevealed, so the instance can be switched back (PLAN.md §25.4). The author writes
  `checkpoint` either way: which of the two happens is the instance's setting, not the content's.
- **`flag`** — the answer *is* the flag, and the participant discovers it by doing the task. This
  is how a CTF-shaped subject works (`shell1{exemple}`). The platform must not overwrite it, so
  those exercises get no generated code and no "ask the instructor" note.

- **`token`** — the *runtime* reveals the answer. A runtime that grades its own exercises can
  show a completion token derived from the exercise's `token_id` and a secret the platform mints
  per instance; the importer derives the same value and sets it as the flag. Nothing is authored
  and nothing is stored in the repo: rerunning the sync on another instance produces different
  tokens, so answers do not leak between sessions. What it proves is that somebody got the tests
  to pass in the normal flow — a client-side runtime cannot prove more than that.

- **`quiz`** — the step's own questions are the proof. The `type: quiz` blocks the exercise
  hosts (§3.7) become its control: the participant answers them all, submits once, and the
  step is solved when every answer is right. Answers come from `quiz_answers.yaml`, like any
  quiz. A wrong submission names the questions to look at again (« revois les questions 1
  et 3 »), never the right answer. No instructor is involved, which is what makes it the right
  mode for the first steps of a subject, where the work is reading and the room may have
  nobody to hand out a code. The linter refuses a `validation: quiz` exercise that hosts no
  quiz marker: it would import as a control with nothing in it.

  Weigh it before using it on a step whose answer is worth guessing: naming the wrong
  questions lets a participant solve each one on its own rather than the set as a product,
  and a « 3 réponses sur 4 » question has only four combinations.

Authored answers live in a sidecar, never in the markdown — a flag printed next to its own
exercise is not a flag:

```yaml
# flags.yaml, beside quiz_answers.yaml
flags:
  011_mkdir: "shell1{mkdir ok}"
  003_arguments: {value: "shell1{exemple}", case_insensitive: false}
```

Comparison is case-insensitive unless the entry says otherwise, which is what you want when the
answer is a command or a path. The linter refuses a `validation: flag` exercise with no entry —
it would import as a step nobody can solve — and an entry matching no exercise.

**Protect the sidecar** the way the subject already protects its answers: a public repo should
encrypt it (shell-1 keeps `flag.txt.gpg` and a `decrypt.sh`) or leave it out and supply it at
sync time.

### 3.4 The short version — marked region, shown as a summary

The short "consigne courte" is *content*, not metadata, so it may be visible on GitHub.
Invisible region markers select it:

```markdown
<!-- ws:resume -->
- `btn(2)` gauche, `btn(3)` droite
- limiter `padx` à l'écran
<!-- /ws:resume -->
```

The platform renders it **in both modes**, as a foldable "In short" box above the statement —
a summary, never a replacement. On GitHub the bullets render as a normal part of the doc.

> Changed 2026-08-25 (PLAN.md §25.7). This region used to be specified as a *swap*:
> instructor-led would render only the bullets and self-serve the full prose. That is the
> same shape as the bug fixed in §24 — a step whose statement says "reuse the code above"
> needs the code above to be on the page, and the participant sitting in a room is the one
> most likely to be pointed at it. Nothing is withheld by the mode any more.

### 3.4b Reference material — toolbox and glossary, gathered on one page

Two more invisible regions, same grammar as `ws:resume`. They mark the parts of a step that are
**reference**, not work:

```markdown
<!-- ws:toolbox -->
### Boîte à outils

> 🧰 **Outil #1 : `not` « L'inverse de »**
> ...
<!-- /ws:toolbox -->

<!-- ws:glossary -->
## Ce que le jeu te donne
| Terme | Signification |
| --- | --- |
| `me.X` `me.Y` | Position du fantôme (X,Y), en cases |
<!-- /ws:glossary -->
```

The author keeps writing each one **beside the step that needs it** — that is where it is
understood, and the markers do not change how the subject reads on GitHub. The platform lifts
both out and shows them on `/toolbox`, a page of its own in the navbar, in reading order. The
step keeps a line in the same position naming what was there, and linking to it:

> 🧰 Les outils de cette étape : `if / then / end`, `not`. Ouvrir la boîte à outils

Two things this buys. A step no longer opens with half a screen of reference before the work
starts; and a tool met at step 2 is still reachable at step 7, where scrolling back through five
steps to re-read it is how a participant loses their place.

**Gated like everything else.** A section is open exactly when its step is: the toolbox page
shows the name of a locked step and nothing else, the same contract the workshop page keeps for
locked step titles. The page therefore has the same shape from the first minute, which is what
lets it answer "is there anything more coming".

The tool names on the step line are **derived** from the `🧰`/`🗺️` titles the author already
wrote (`Outil #N :` and the gloss after « are dropped), so there is no second list to maintain.

A subject that marks nothing keeps the old behaviour: every section renders inside its step, and
the toolbox page says it is empty. A platform that does not know these markers renders them as
the invisible comments they are.

### 3.4c The do-it section — a heading that starts with 🥸

A step is read, then done, then checked. On screen those three used to look like
one column of prose with clickable controls at the bottom, and beta testers did the
obvious thing: they answered the questions without doing the step first.

So the platform paints two panels, in two colours: the section you **do**, and the
questions you **answer**. Nothing is hidden and nothing is reordered — a participant
who wants to answer first still can.

The marker is the house heading the blueprint already prescribes:

```markdown
### 🥸 Mise en application

**Ton objectif :** ...
```

**A heading whose text starts with 🥸 opens the do-it section**, and that section runs
to the end of the statement (hints and the answer control are separate structures by
then). The wording after the emoji is free — « Mise en application », « Ta première
réussite », anything — and no closing marker is needed. A statement with no such
heading simply gets no panel.

The questions get the other panel automatically: they are a `type: quiz` block, and
the platform knows where they are.

### 3.5 Instructor-led review flow

In instructor-led mode, marking an exercise done is a **submission for review**, not a
self-validation:

```
participant marks "done" → submission enters the instructor's review queue (pending)
    → instructor approves (exercise validated, observables checked off)
    → or rejects (exercise returns to "to redo", provisional points retracted)
```

**An unreviewed submission never blocks the participant** — progression unlocks immediately
on submission — **unless the subject states otherwise** with `review: blocking` on the
exercise (for gates where continuing on a wrong foundation would waste the participant's
time, e.g. the last exercise of a starter chapter).

The review queue is also where **competency observables get validated**: the instructor's
review UI shows the exercise's `obs` entries as checkboxes, so grading and skill tracking
happen in one gesture.

Auto-checkable validations (`tests`, `flag`, quizzes §3.7) still auto-check in instructor-led
mode; their result is attached to the submission so the instructor reviews outcomes, not
syntax.

### 3.5b The instance's mode — instructor-led or self-serve

The same subject serves a room with an instructor and somebody working alone at home. Which one
an instance is, is **the instance's setting and not the content's**: `workshop_mode` in CTFd's
config, flipped at `/admin/workshop/settings` or set at provisioning from
`deploy/instances.yaml`. One instance, one mode (PLAN.md §25.3).

`platform.mode_default` in `subject.yaml` is what provisioning falls back to when the manifest
says nothing. Precedence: `instances.yaml` `mode:` > `platform.mode_default` > `instructor_led`.

What changes with it, in full:

| | instructor-led | self-serve |
|---|---|---|
| a `checkpoint` step | asks for the code the instructor reads out | one button, self-validated |
| the note under the control | "ask the instructor for the validation code" | "nobody checks this for you" |
| registration (at provisioning) | a shared code | open |
| the review flow of §3.5 | applies | nothing to review |

What does **not**: the order of the steps, what gates what, points, hints, the short version of
§3.4, `flag` and `token` answers (they already prove themselves), and every solve already
recorded. Flipping the mode changes what a step asks for, and nothing else.

### 3.6 Hints — native `<details>`, optional cost

```markdown
<!-- ws: type: hint, cost: 5 -->
<details><summary>Indice</summary>

La fonction `btn` prend en paramètre un nombre (3 : flèche droite).
Table de correspondance : https://github.com/nesbox/TIC-80/wiki/key-map

</details>
```

GitHub renders a collapsible — exactly the Notion toggle the current content loses on export.
The platform converts it into a CTFd paid hint (`cost` points). Without the marker comment, a
`<details>` block is just content and renders collapsed everywhere.

### 3.7 Quizzes — body stays, marker goes invisible

Keep the existing body syntax (`- A.` single / `* A.` multiple / match / freeform — the
`quiz_lib.py` parser is reused as-is), but move the machine marker into a comment so nothing
metadata-ish renders:

```markdown
<!-- ws: type: quiz, id: A2.3, skills: [PROG/02/A1] -->
> Avec TIC-80, si je souhaite avoir l'état des touches haut et bas du
> joueur 1, je dois utiliser dans mon code (2 réponses correctes)

* A. btn(0)
* B. btn(1)
* C. btn("up")
* D. btn("down")
```

On GitHub: a quoted question followed by options — legible content, no `QUIZ.A2.3` token.
Correct answers stay out of the markdown (upstream rule). They live in **`quiz_answers.yaml`**
at the subject repo root, keyed by quiz id, imported server-side at sync — optionally
encrypted (age/sops) with a deploy-time key when lookup-ability matters (same trust model as
`flag_env`).

Each quiz imports as a **CTFd challenge of type `quiz`** (PLAN.md §12): single/multiple/match
auto-grade in `attempt()`; `freeform` auto-grades against a **regular expression** (or list —
any match wins) stored as its `quiz_answers.yaml` entry, case-insensitive by default. CI
compiles every regex so a broken pattern fails the build.

### 3.8 Images — repo-relative, never hotlinked

**Rule: an image lives in the subject repo and is referenced by a path relative to the document
that uses it.** Put them in `img/` (§3.1).

```markdown
![Le repère de la grille](img/origin.png)
```

Not this:

```markdown
![Le repère de la grille](https://raw.githubusercontent.com/org/subject/main/img/origin.png)
```

Both render identically on GitHub, which is exactly why the second one is easy to end up with.
They behave very differently once the content is imported.

A repo-relative path is **part of the content**, so it inherits everything the repo already
guarantees. It is pinned by the same `ref` as the text that surrounds it (§4.1), so re-syncing a
session at a tag gets the illustrations that text was written against. It survives the repo being
renamed, moved or made private. And it needs no network beyond the CTFd instance itself — which
matters, because a room is expected to serve from its own deployment rather than the public
internet (PLAN.md §14.4).

A hotlinked URL has none of that. It points at a branch head, so an image can change under a
pinned session; it breaks when the source repo is renamed or made private; and it makes every
participant's browser reach a third-party host, which is precisely what fails on a locked-down
school network or a LAN-only deployment.

**What the importer does.** `parse_subject` collects every repo-relative image reference,
`sync_subject.py` uploads each one once as a CTFd file of type `standard`, and rewrites the links
in every body it sends — page content, exercise statement and context, hints, trailing prose,
quiz questions.

| Detail | Why |
|---|---|
| Location is `ws-<subject>/<name>-<sha1[:8]>.<ext>` | Deterministic, so a re-sync uploads nothing; the content hash means an *edited* image gets a new URL instead of a cached stale one |
| Type is `standard`, not `challenge` | Challenge files are gated by CTF time and challenge visibility (`CTFd/CTFd/views.py:400`) — an inline illustration would 403 outside the competition window |
| Paths resolve relative to the **document**, not the repo root | That is how every markdown renderer resolves them, so what the author sees on GitHub is what imports |

**What is deliberately left alone:** absolute `http(s)://` and protocol-relative URLs, `data:`
URIs, and site-absolute paths (`/files/…`). Those already resolve, so the importer does not touch
them. That is the escape hatch for an image that genuinely belongs to someone else — an official
documentation diagram, say — where hotlinking is the honest thing to do. It should be the
exception and worth a comment when used.

The linter fails on a referenced image that does not exist, so a typo or a file that was never
committed is caught in CI rather than discovered as a broken image mid-workshop.

> Known debt: `content/pypong` hotlinks three images back at `pypong_new` on `main`, inherited
> from the conversion. It works, and it is exactly the pattern this section argues against —
> vendoring them into `content/pypong/img/` is a small cleanup nobody has done yet.

### 3.9 What is generated (never hand-written)

- `README.md` — from `subject.yaml` + derived outline (adapt `generate_readme.py`).
- The challenge tree the platform imports — parsed directly from the md at sync time.
- There is **no `toc` to check** — `check_toc.py`'s entire drift-error class disappears.

The linter (CI, adapted from `workshop-metadata-tools`) validates instead:
`subject.yaml` against the schema; `ws:` comments parse as YAML with known fields; ids unique;
`skills` paths resolve against `ref_comp`; `obs` ids exist in the project's observables;
`requires` targets exist; quiz bodies parse. **The platform importer and the CI linter share
the same parser library** — what CI accepts, the platform imports, by construction.

## 4. Composition and sync

### 4.1 Workshop repo — `workshop.yaml`

**Design goal: a workshop repo is almost self-sufficient to deploy an instance.** Reading the
repo is enough to run the associated CTFd instance — `provision(repo_url, ref)` is the whole
interface. Only three things stay outside the repo, on purpose:

- **mode** (self-serve / instructor-led) — an instance setting since PLAN.md §25 (§3.5b)
- **secrets** (`flag_env` values) — never in a public repo
- **attendees** — session-specific by nature

```yaml
schema_version: "2.0"

workshop:
  name: "Winter Camp — Game dev"
  slug: "winter-gamedev"
  summary: "Une journée game dev : PyPong pour démarrer, puis Santa Shooter."

subjects:
  - repo: Manta-Epitech-Academy/pypong
    ref: v2026.01          # tag or commit id — never a branch head for a session
    role: starter
  - repo: Manta-Epitech-Academy/santa_shooter
    ref: 8f3c2d1
    role: advanced
    order: 1               # optional; advanced subjects without order = free choice

instance:                  # optional CTFd setup values; provisioner defaults otherwise
  user_mode: users         # users | teams — painful to change after setup
  scoreboard: hidden       # visible | hidden
```

**Vendored form.** A platform that vendors its content (as `content/<subject>` does) names
directories instead of repos, and the importer accepts either:

```yaml
subjects:
  - path: ../../pypong        # relative to workshop.yaml
    role: starter
  - path: ../../santa_shooter
    role: advanced
    order: 1
```

`repo` + `ref` is what the **admin sync page** reads (PLAN.md §26): the instance fetches each
subject from GitHub at that ref. `path` is what the command line reads from an already-vendored
tree. A manifest may carry both, and `kevin-cazal/discover-linux_subjects` does — the subjects are
git submodules, so `path` is the submodule directory a `--recursive` clone gives you and `repo`
is where the instance fetches the same thing from.

**`ref: submodule`** means "the commit this workshop repo pins for that subject", which is what a
clone of the wrapper checks out. The platform reads the pin from GitHub's contents API rather than
from git, because a repository tarball carries submodule directories empty. The alternative is a
branch (`ref: main`, always that subject's tip — one push while a subject is being rewritten) or a
tag or sha (frozen for a session). The trade is worth stating: with `submodule`, pushing to the
subject repo changes nothing until the wrapper's pin is bumped and pushed too.

**Single-subject shortcut:** most workshops are one subject. A subject repo is directly
deployable — the provisioner treats `subject.yaml` as an implicit one-subject workshop
(`role: starter`, instance defaults). A separate workshop repo is only needed to compose
several subjects or override instance settings.

Gating rule (from CLAUDE.md): **the starter must always be completed before any advanced
subject.** At import, every advanced subject's entry challenges get the starter's final
exercise(s) as CTFd prerequisites. Ordered advanced subjects chain the same way; unordered
ones all hang off the starter.

### 4.2 Sync: repo → instance

**Intro and outro are steps.** The entrypoint document becomes the first step of the workshop and
the trailing prose becomes the last one, so both ends of a subject live in the flow rather than on
side pages.

- **Intro — kind `ack`:** the text, then a single button ("I have read this"). Nothing is graded;
  the point is a deliberate action after reading. It is a prerequisite of every otherwise-unlocked
  step, so a participant cannot start without passing through it. Typed `ack` rather than given a
  "dumb" static flag on purpose: a flag would have to be embedded in the page for the button to
  send it, which reads like a leak and invites copy-paste.
- **Outro — kind `rating`:** the text, then *how was this workshop?* (👍/👎 plus an optional
  sentence). Asking for a bare acknowledgement at the end would be busywork; a satisfaction rating
  is the one thing worth collecting there. It is gated by the last exercise. **The rating is the
  submission**: grading it is what stores the feedback, so the step is solved at the moment the
  feedback lands and not a millisecond earlier. Submitting nothing, or something that carries no
  rating, leaves the step unsolved.

Neither is worth points. But both **count towards the progress bar**: a workshop sits at
"14 / 15" until the rating is given, and people do not abandon a bar that close to full — which
is how the feedback actually gets collected. (A `rating` step therefore has to stay completable
even when the `challenge_ratings` config is `disabled`; it falls back to a plain acknowledgement
button, or the bar could never reach 100%.)

**Ratings live in CTFd's own table**, reached two ways. `PUT /api/v1/challenges/<id>/ratings`
stores 👍/👎 (`+1`/`-1`) plus an optional review, one per user per challenge — but it only accepts a
rating for a **solved** challenge, so the closing step cannot use it: going through that endpoint
would mean solving first, i.e. hitting 100% and only then, maybe, leaving feedback. The closing
step therefore writes the same `Ratings` row from its own grading (`plugins/workshop/quiz.py`).
Already-solved steps use the endpoint, which is exactly what it is for. Every solved step also carries a rating
inline, kept deliberately quiet: one muted line — "Rate this step 👍 👎" — inside the completed
note, never a dialog. It rates the *content*, and is what feeds content-quality feedback per
exercise, so the wording stays about the step rather than about the reader's experience. Setting the `challenge_ratings` config to `disabled` removes all of it.


```
provision(workshop_repo_url, ref) / admin "Sync" (or webhook on session-free instances)
    → read workshop.yaml (or subject.yaml → implicit one-subject workshop)
    → pull each subject repo (shallow, at pinned ref)
    → parse subject.yaml + md (shared parser)
    → diff against DB by stable id
    → upsert CTFd challenges (points, requirements/prerequisites, hints, flags)
    → store rendered section content + resume regions in plugin tables
```

- Stable `id` is the reconciliation key: renames of headings don't orphan solves; removing a
  section soft-hides its challenge (solves preserved).
- Session instances sync at provisioning from pinned refs (reproducible); re-sync only via the
  admin button. Session-free instances may follow a branch head via webhook — the "push and
  it's live" mode.
- Content edits (typo fixes) update in place; structural changes go through the same diff.

## 5. Migration path

1. `pypong_new`: mechanical — `metadata.yaml` loses its `toc` (titles already match headings,
   a script emits the `ws:` comments from the existing toc + observables), quiz markers move
   into comments, "- Indice" lists become `<details>` hints.
2. Notion workshops: export to md (or pull via the public API — see PLAN.md §9), then add
   markers. The Étape/Tutoriel/Mise-en-pratique structure maps directly.
3. `ref_comp` unchanged — same slash notation, same observables files.
4. `workshop-metadata-tools`: schema 2.0, drop `toc` + `check_toc.py`, add the comment parser
   (also used by the platform), keep `quiz_lib.py` and the reusable workflow.

## 6. Decisions (reviewed 2026-08-03)

1. **Schema strictness** — warn on unknown `ws:` fields in CI, ignore them at import (newer
   content tolerated by older platforms).
2. **Pinning** — **decided: tags or commit ids** for sessions. Branch-head-following is
   reserved for session-free / staging instances (§4.2).
3. **Flags** — **decided: `flag_env`** field naming a secret the admin sets at sync time;
   flags never live in the (public) content repo.
4. **Per-exercise `resume` mode override** — **deferred** until a real workshop needs it.

### 3.11 Topologies, in practice

A chapter is a scope for a topology, not a challenge — it never becomes one. A document that
marks no chapter behaves as a single linear chapter, so content written before topologies
existed keeps exactly the shape it had.

```markdown
# Gameplay
<!-- ws: {type: chapter, topology: free} -->

Choisir un ou plusieurs défis, dans l'ordre de son choix.

## Sinusoïde
<!-- ws: {type: exercise, id: sinusoide} -->
```

**`linear`** (default) chains the chapter's exercises: each one requires the previous.

**`free`** gives every exercise of the chapter the same single prerequisite — the chapter's
*entry*, meaning whatever preceded it. All of them open at once and may be done in any order.
The workshop page renders them as a menu: none of them is "the current step", because naming one
would impose the reading order the author explicitly refused.

**What a free chapter unlocks in turn** is its own **closing step**. "Choose one or more" leaves
no last exercise to hang the next chapter off, and CTFd's prerequisites are a plain AND-list
with no "N of M". So the closing step gates on the chapter entry too, and acknowledging it is
the participant saying *"I'm done here"*. The cost is that a free chapter can be skipped
wholesale by closing it immediately — acceptable for optional creative work, so do not put a
chapter that must be completed behind `topology: free`.

**`optional: true`** marks bonus work. It unlocks like its neighbours and can be done at any
point after that, but it **gates nothing** — the next step reaches past it — and it **counts
towards nothing**. That second half is the one that matters: without it, a participant who skips
the bonuses can never reach 100%, and 100% is what the closing rating step hangs off. An
optional step is never "current" either, so the page never points at skippable work over the
required step that follows it.

**`requires`** overrides all of the above for one exercise: an explicit list of exercise ids,
evaluated as AND. Use it for cross-chapter gating. Two rules:

- Give every `requires` target an explicit `id:`. Ids are otherwise derived from headings, so a
  reworded heading silently breaks the graph. The linter rejects a target that does not resolve,
  and rejects cycles.
- `requires` sets that exercise's own prerequisites only. It does not remove the exercise from
  the chain, so the *next* exercise still gates on it unless it is `optional`.
