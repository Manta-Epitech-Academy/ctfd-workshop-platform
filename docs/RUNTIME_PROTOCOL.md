# Workshop Runtime Protocol (WRP v1)

How an existing web app — an editor, an emulator, a game — appears beside the workshop
instructions and talks to the platform. Decision record: `PLAN.md` §14.

The platform side is generic and never changes per runtime. Adding a runtime means writing
**one adapter file** (see `plugins/workshop/assets/runtime/adapters/mock.js`, 45 lines) and
declaring it in the subject.

---

## 1. Shape

```
CTFd workshop page  ──── postMessage ────  runtime in an iframe
      (host)                                     (guest)
```

The runtime is served from **CTFd's own origin** at `/runtime/<id>/<version>/`. That is what
makes `localStorage`, the clipboard and focus behave exactly as they do when the app runs
standalone, and it is what lets the host inject the adapter — so **the runtime's own repository
needs no modification**.

### Three rules

0. **`result` is advisory. A runtime never solves a step.** In some runtimes the student's own
   code runs in the page's JS context (`pacman-ghost-ai` runs Lua through Fengari), so anything
   auto-submitted on the runtime's word is forgeable. The host highlights; the participant
   submits.
1. **The runtime never calls the CTFd API.** It asks the host, which owns the CSRF nonce and the
   endpoints. One place to get credentials right, and the runtime stays fully usable standalone —
   with no host, the protocol is simply silent.
2. **One frame per workshop, not per step.** Mounted lazily on first open and kept alive; the
   active step is *sent* to it. Booting an editor — let alone a 256 MB VM — per accordion step is
   not acceptable.

---

## 2. Messages

Every message carries `wrp: 1`. Anything else on the channel is ignored, by both sides. Both
sides check `event.origin` against their own origin.

### Guest → host

| `type` | Payload | Meaning |
|---|---|---|
| `ready` | `runtime`, `protocol`, `capabilities[]`, `storageKeys[]?` | Handshake. Send it when the app is actually usable, not on `DOMContentLoaded`. `storageKeys` are the `localStorage` keys the host snapshots server-side (§6). |
| `result` | `step?`, `ok`, `detail?` | "This looks done." **Advisory** — shows a note, never solves. |
| `propose` | `step?`, `submission` | The runtime produced something answer-shaped (a flag its guest printed). Fills the answer field; the participant still submits. |
| `state` | `dirty?` | Optional: work in progress changed. |

### Host → guest

| `type` | Payload | Meaning |
|---|---|---|
| `init` | `protocol`, `runtime`, `step` | Sent once, in reply to `ready`. |
| `step` | `step` | The participant moved to another step (opened it, or solved one). |
| `focus` | — | Put the keyboard where it belongs inside the runtime. |

`step` is `{id, name, state}` where `state` is `done | current | todo | locked`, or `null` when
the workshop has no active step.

---

## 3. Adding a runtime

**1. Build a dist for the mount path.** Asset URLs are baked in at build time, so the base must
match where it is served:

```sh
tools/build_runtime.sh tic80          # -> plugins/workshop/runtimes/tic80/<sha>/
```

Add a `case` to that script for a new runtime. Dists are gitignored and rebuilt from a pinned
ref, never vendored.

**2. Write the adapter** at `plugins/workshop/assets/runtime/adapters/<id>.js`. It runs inside
the frame, injected by the host. Minimum viable adapter: announce `ready`. Copy `mock.js`.

**3. Declare it** in the subject's `subject.yaml` — reading the repo is enough to deploy the
pane (PLAN.md §10):

```yaml
runtime:
  id: tic80
  version: 8ad976f        # the dist built above
  title: "TIC-80"
  pane:
    placement: side       # side | bottom
    open: false           # open on arrival, or let them read first
    size: 55              # percent of viewport width
```

`tools/sync_subject.py` writes this to the CTFd config `workshop_runtime`; the workshop page
reads it back. If the dist is missing, no pane is rendered — a page never points at a 404.

A runtime hosted elsewhere can be declared with `external: true` and an explicit `src`. It then
has to ship its own adapter: a cross-origin frame cannot be injected into, and it will hit
partitioned storage (PLAN.md §14.2).

---

## 4. What tic80-web-editor actually needed

Nothing, in its own repository. `adapters/tic80.js` announces `ready`, handles `focus`, and
reports the two `localStorage` keys the editor already autosaves to
(`tic80-web-editor-cart`, `tic80-web-editor-layout`) so the host can snapshot them server-side
later. It sends no `result`: nothing in a code editor knows whether a PyPong exercise is done.

For PyPong the pane is simply *there*: the subject has the participant type the code themselves,
so there is no per-step starter cart to push. When a subject does ship carts, `init`/`step` carry
the step and the adapter loads the matching one.

---

## 5. Testing without a runtime

`plugins/workshop/assets/runtime/mock/index.html` plus `adapters/mock.js` is a runtime that does
nothing but speak the protocol, with buttons to emit `result` and `propose`. Tests point the
config at it and assert the advisory rules hold — in particular that neither message ever changes
a participant's progress — without booting an editor or a VM.

---

## 6. Work in progress is kept server-side

The keys a runtime announces in `ready.storageKeys` are snapshotted to CTFd, per participant, and
put back **before the frame is created** on the next visit. Same origin means the host reads and
writes them directly, so **a runtime needs no code for this** beyond naming its keys — that one
array is the entire contract (PLAN.md §16).

What it buys, in a classroom: a student who moves machines finds their work, and the next student
on a shared PC does *not* inherit the previous one's cart. The rule is an owner stamp in the
browser bucket (`ws-workspace-owner`); a bucket owned by somebody else is wiped and refilled from
the server, and local work newer than the snapshot wins so a reload never clobbers unsaved edits.

Endpoints: `GET`/`POST /api/v1/workshop/workspace`, session-scoped, 16 keys and 512 KB per
participant. Editor-scale only — a v86 disk image does not fit, and shell workshops persist
through the guest's 9p share instead.

## 6b. What shell-rpg needed

Nothing in its own repository either, and less than tic80: `adapters/shell-rpg.js` announces
`ready` and focuses the terminal (everything a participant does here is typing). It reports no
storage keys — the runner keeps only debug flags in `localStorage`, and the work lives in the
guest's 9p host share, which is in-memory and outside a v86 snapshot. Persisting *that* is a
different collector for the same table (§6, PLAN.md §18) and is not built.

The ~330 MB VM bundle is never served by CTFd: the app fetches it from a CDN, its mirrors, or the
mirroring cache in `compose/mirror`, and the participant picks the downloaded file in the welcome
screen. `tools/build_runtime.sh shell-rpg` therefore stages a 4.5 MB dist and nothing else.

## 7. Two presentations, one host

The pane is a real split, and half of a small laptop's viewport is not room to work in. So the same
host also runs on a page of its own:

```
GET /workshop/<document>/runtime
```

`split` or `window`, remembered per browser in `ws-runtime-mode`. With no stored choice the
viewport picks: below 900px the launcher opens the tab and its label says so, at or above it opens
the pane. The **default** is automatic; the tab never is — a window nobody asked for is hostile,
and outside a click a browser blocks it anyway.

**A runtime needs nothing for this either.** The popped-out page is the same `#ws-runtime` element
with the same `data-runtime-*`, and `assets/runtime.js` is the same file: whichever window creates
the frame owns the protocol and the snapshot, and the other owns the step list. They talk over a
`BroadcastChannel` (`step` down, `result` / `propose` / `mode` up), so `result` is still applied by
the page and still solves nothing (§1 rule 0). It is deliberately **not** `window.open(<dist>)`:
that would be a runtime with no host — no `init`, no step, no snapshot, no restore before boot.

The channel is named `ws-runtime:<document>`, not `ws-runtime`: a host page is per document, so the
pairing is too. Origin alone would put every open workshop page on one channel, and a page that
does not show the step a `result` names falls through to its own current step — so the wrong card
would light up in the other tab. Parcours nodes are real links so that ctrl-click works, which
makes two subject pages at once the expected shape rather than an edge case.

Three consequences worth knowing:

- **A subject with no per-document routes has no pop-out.** The route is per document because the
  frame boots with the subject's parameters, and a document is what names the subject. A subject
  synced before `workshop_documents` existed keeps the split pane, and the page renders no control
  for the other mode.
- **Switching presentation reboots the runtime.** A live iframe cannot move between documents
  without reloading. The work is saved before the frame goes and restored before the next one
  boots, which is what §6 is for.
- **One runtime tab, whichever document you are on.** The tab's *name* is not per document, unlike
  its channel: opening the runtime from another part moves that tab rather than leaving a row of
  abandoned editors behind. The tab that moves saves on its way out, like any other handover.

## 8. Not yet built

- **`bottom` placement** is accepted in metadata but currently renders as a side pane.
- **A dual-screen window.** The pop-out is a tab, which is the right answer on one small screen; a
  sized popup would be better on a second monitor and is not built, because two controls for one
  idea is a worse default than one.
