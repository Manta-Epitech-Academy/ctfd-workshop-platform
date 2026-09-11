#!/usr/bin/env python3
"""Sync a subject repo into a CTFd instance (docs/CONTENT_CONVENTION.md §4.2).

    python3 tools/sync_subject.py content/pypong --url http://localhost:8080 \
        --admin-user admin --admin-pass ...

Mapping (idempotent, keyed by an admin-only Topic `ws:<subject>:<slug>`):
  entrypoint document      -> the CTFd index page (replaced) AND the first
                              step (`__intro__`, an `ack` quiz: read it, then
                              press the button), which gates every root
  trailing prose           -> the closing step (`__outro__`, same mechanic),
                              gated by the last exercise
  exercise                 -> `checkpoint` quiz challenge when the content
                              validates by an instructor code, otherwise a
                              standard challenge with a flag; description =
                              context + the author's short version + body,
                              each fenced for the page to lift out
  exercise hints           -> CTFd hints (cost from the marker)
  quiz                     -> `quiz` challenge (plugins/workshop) with spec +
                              answers from quiz_answers.yaml; requires its
                              host exercise
  linear topology          -> exercise N requires N-1 (anonymize: preview)
  source reading order      -> Challenges.position (CTFd's native board order),
                              so the board is not sorted alphabetically

Validation codes ("the code given by the instructor"): read from --codes YAML
if given, otherwise read back from the checkpoint steps already in the
instance, and only generated for an exercise that has neither. Codes therefore never rotate on a re-sync — once a
code is handed to attendees it stays valid. The resolved sheet is written to
the codes file; hand that file to the instructor.
"""
import argparse
import hashlib
import hmac
import json
import re
import secrets
import sys
from pathlib import Path

import requests
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from ws_parser import (load_flags, load_quiz_answers, lint_all,  # noqa: E402
                       parse_subject, rewrite_asset_refs, rewrite_cover_refs)

# The note under a step ("ask the instructor for the code", "paste the token
# here") is NOT written into the description any more. It depends on the
# instance's mode, which is a toggle (PLAN.md §25.3), and a sentence baked into
# `Challenges.html` cannot follow one: it would become a lie the moment the
# mode changed, and correcting it would mean a re-sync. The workshop page
# renders it at request time instead (templates/workshop_step_body.html). A
# re-sync replaces the whole description, so a note baked in by an older run
# disappears on its own.
TOKEN_SECRET_CONFIG = "workshop_token_secret"
# challenge id -> the validation mode the content asked for. Recorded because
# it cannot be recovered from the instance afterwards: `checkpoint` and `flag`
# are both a single static Flags row, and telling them apart by the shape of
# the string is a guess. The answer sheet (plugins/workshop/answers.py) needs
# to label a row honestly, so the sync — which is what knows — writes it down.
VALIDATION_CONFIG = "workshop_validation"


class CTFdAdmin:
    """Minimal admin API client (session login + CSRF nonce, or a token).

    `token=` is what the in-container sync page uses (PLAN.md §26): the
    instance's preset admin token is in its environment, so the job needs no
    password and no login round-trip. CTFd skips CSRF entirely for a request
    carrying `Authorization`, and explicitly allows the multipart file upload
    with one (utils/initialization/__init__.py:344), which is the only
    non-JSON call this client makes.
    """

    def __init__(self, url, user=None, password=None, token=None):
        self.base = url.rstrip("/")
        self.s = requests.Session()
        if token:
            self.s.headers["Authorization"] = f"Token {token}"
            # Sent anyway by api()/upload(); ignored, since the header above is
            # what CSRF checks for.
            self.nonce = ""
        else:
            self._login(user, password)

    def _nonce(self, path="/"):
        r = self.s.get(self.base + path)
        m = (re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", r.text)
             or re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text))
        if not m:
            raise RuntimeError(f"no CSRF nonce on {path}")
        return m.group(1)

    def _login(self, user, password):
        nonce = self._nonce("/login")
        self.s.post(self.base + "/login", data={
            "name": user, "password": password, "nonce": nonce, "_submit": "Submit"})
        self.nonce = self._nonce()

    def api(self, method, path, **kw):
        r = self.s.request(method, self.base + "/api/v1" + path,
                           headers={"CSRF-Token": self.nonce,
                                    "Content-Type": "application/json"}, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r.json().get("data")

    def upload(self, path, location, file_type="standard"):
        """POST /files as multipart.

        Two differences from `api()`, both forced by the request not being JSON:
        no Content-Type header (requests must set the multipart boundary), and
        the CSRF nonce goes in the form body — CTFd only reads `CSRF-Token` from
        the header when `request.is_json`, and 403s otherwise
        (CTFd/CTFd/utils/initialization/__init__.py:398).
        """
        with open(path, "rb") as fh:
            r = self.s.post(self.base + "/api/v1/files",
                            data={"type": file_type, "location": location,
                                  "nonce": self.nonce},
                            files={"file": (Path(location).name, fh)})
        if r.status_code >= 400:
            raise RuntimeError(f"upload {location} -> {r.status_code}: {r.text[:200]}")
        return r.json()["data"][0]


def slug_topic(subject_slug, slug):
    return f"ws:{subject_slug}:{slug}"


def doc_slug(doc):
    """Stable id for a document, from its filename (`atelier1.md` -> `atelier1`).

    The filename rather than the title: a title is prose and gets reworded, and
    this id ends up in a URL and in challenge topics.
    """
    return re.sub(r"[^a-z0-9]+", "-", Path(doc.path).stem.lower()).strip("-")


CONTEXT_OPEN = "<!-- ws:context -->"
CONTEXT_CLOSE = "<!-- /ws:context -->"
# The author's short version (convention §3.4) rides in the description the
# same way, for the page to lift out and show above the statement. It is a
# summary, never a replacement — PLAN.md §25.7.
RESUME_OPEN = "<!-- ws:resume -->"
RESUME_CLOSE = "<!-- /ws:resume -->"


def fence_context(context_md, body_md, resume_md=""):
    """The exercise statement, with the prose it inherited marked as inherited.

    An exercise carries the prose that precedes it so it reads on its own —
    that is the Phase 0 lesson, and it is what makes CTFd's own board usable.
    On the workshop page, where that prose is the *part's* opening paragraph,
    printing it inside the first step makes the step look like it begins with a
    lecture. Fencing it in HTML comments lets the page lift it out and render it
    above the steps, while the board still shows one self-sufficient statement:
    comments are invisible in every renderer, CTFd's included.

    The author's short version travels the same way, fenced in its own markers
    so the page can fold it above the statement in both modes.
    """
    parts = []
    if context_md:
        parts.append(f"{CONTEXT_OPEN}\n{context_md}\n{CONTEXT_CLOSE}")
    if resume_md:
        parts.append(f"{RESUME_OPEN}\n{resume_md}\n{RESUME_CLOSE}")
    if body_md:
        parts.append(body_md)
    return "\n\n".join(parts)


def outro_slug(doc):
    """One closing step per document, so a multi-document subject gets a close
    (and a rating) at the end of each part rather than only at the very end."""
    return f"__outro__-{doc_slug(doc)}"


def upsert_page(ctfd, route, payload):
    """Create or update a CTFd Page by route (they have no natural upsert)."""
    page = next((p for p in ctfd.api("GET", "/pages") if p["route"] == route), None)
    if page:
        ctfd.api("PATCH", f"/pages/{page['id']}", json=payload)
    else:
        ctfd.api("POST", "/pages", json=payload)


def resolve_prerequisites(subject, intro_id, ex_ids, outro_ids):
    """Walk the subject in reading order and decide what gates what.

    Returns `({challenge_id: [prerequisite ids]}, {optional ids}, {free ids})`.

    Three rules, in order of precedence (CONTENT_CONVENTION §3.3, PLAN.md §15):

    1. An explicit `requires` wins — the author is hand-wiring the graph.
    2. Otherwise the enclosing chapter's topology decides. `linear` gates on the
       running predecessor; `free` gates every member on the chapter's *entry*,
       so all of them open at once and may be done in any order.
    3. `optional: true` never advances the predecessor. A bonus step unlocks
       with its neighbours and gates nothing, so skipping it costs nothing.

    A free chapter has no last step to hang the next thing off, so its closing
    step gates on the chapter entry too: acknowledging it is the participant
    saying "I'm done here", which is what unlocks whatever follows.
    """
    prereqs, optional_ids, free_ids = {}, set(), set()
    prev = intro_id           # the running predecessor, in board order

    for doc in subject.documents:
        topology_of = {c.slug: c.topology for c in doc.chapters}
        # Where each chapter started, so `free` members can gate on it. A
        # document with no chapter markers behaves as one implicit linear
        # chapter, which is what every pre-topology subject relies on.
        entry_of = {}
        for ex in doc.exercises:
            topology = topology_of.get(ex.chapter, "linear")
            if ex.chapter and ex.chapter not in entry_of:
                entry_of[ex.chapter] = prev
            cid = ex_ids[ex.slug]

            if ex.requires:
                gates = [ex_ids[r] for r in ex.requires]
            elif topology == "free":
                gates = [entry_of[ex.chapter]]
            else:
                gates = [prev]
            prereqs[cid] = gates

            if topology == "free":
                free_ids.add(cid)
            if ex.optional:
                optional_ids.add(cid)
            elif topology != "free":
                prev = cid          # only a required, linear step moves the gate

        if doc.path in outro_ids:
            prereqs[outro_ids[doc.path]] = [prev]
            prev = outro_ids[doc.path]
    return prereqs, optional_ids, free_ids


def existing_by_slug(ctfd, subject_slug):
    """Map ws-slug -> challenge id via admin-only Topics."""
    mapping = {}
    prefix = f"ws:{subject_slug}:"
    for ch in ctfd.api("GET", "/challenges?view=admin") or []:
        for t in ctfd.api("GET", f"/challenges/{ch['id']}/topics") or []:
            if t["value"].startswith(prefix):
                mapping[t["value"][len(prefix):]] = ch["id"]
    return mapping


def upsert_challenge(ctfd, subject_slug, slug, payload, existing):
    if slug in existing:
        cid = existing[slug]
        ctfd.api("PATCH", f"/challenges/{cid}", json=payload)
        created = False
    else:
        payload = {**payload, "state": payload.get("state", "visible")}
        cid = ctfd.api("POST", "/challenges", json=payload)["id"]
        ctfd.api("POST", "/topics", json={
            "value": slug_topic(subject_slug, slug),
            "challenge_id": cid, "type": "challenge"})
        created = True
    return cid, created


def migrate_checkpoints(ctfd, challenge_ids):
    """Convert pre-§25 checkpoint steps, then read every live code back.

    Both halves go through plugin routes because CTFd's own API can do neither
    (plugins/workshop/checkpoint.py): a challenge's type is not patchable in
    place, and `quiz_answers` is excluded from every read schema — so the code
    a re-sync must preserve is invisible to the API that would otherwise report
    it.

    Returns {challenge_id: code} for every checkpoint step on the instance,
    which is what stops a re-sync minting a new code over a sheet that has
    already been handed out.
    """
    if challenge_ids:
        result = ctfd.api("POST", "/workshop/checkpoints/migrate",
                          json={"challenge_ids": challenge_ids}) or {}
        migrated = result.get("migrated") or []
        if migrated:
            print(f"checkpoints: {len(migrated)} step(s) converted from a static "
                  f"flag to the checkpoint type, ids and solves untouched")
    codes = ctfd.api("GET", "/workshop/checkpoints") or {}
    return {int(cid): code for cid, code in codes.items()}


def token_secret(ctfd):
    """The instance's token secret, minted once and then left alone.

    Per instance, so a token from last year's session is worthless in this
    one. Never rotated on a re-sync, for the same reason instructor codes are
    not: a participant may already have copied a token, and rotating would
    invalidate work that was genuinely done.
    """
    try:
        current = ctfd.api("GET", f"/configs/{TOKEN_SECRET_CONFIG}")
    except RuntimeError:
        # CTFd 404s a config key that has never been set, which is simply "no
        # secret yet" on a fresh instance.
        current = None
    value = (current or {}).get("value") if isinstance(current, dict) else None
    if value:
        return str(value)
    secret = secrets.token_hex(16)
    ctfd.api("PATCH", f"/configs/{TOKEN_SECRET_CONFIG}", json={"value": secret})
    print("token secret: generated for this instance (kept across re-syncs)")
    return secret


def record_validation(ctfd, mapping):
    """Merge `{challenge_id: mode}` into the instance's validation map.

    Merged rather than overwritten so a workshop of several subjects
    accumulates one map across its syncs (PLAN.md §19.1), exactly like the
    documents list — without sync_workshop.py having to thread it through.
    """
    if not mapping:
        return
    try:
        current = ctfd.api("GET", f"/configs/{VALIDATION_CONFIG}")
    except RuntimeError:
        # Never set on this instance yet — same 404 as the token secret.
        current = None
    raw = (current or {}).get("value") if isinstance(current, dict) else None
    try:
        merged = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        merged = {}
    merged.update({str(k): v for k, v in mapping.items()})
    ctfd.api("PATCH", f"/configs/{VALIDATION_CONFIG}",
             json={"value": json.dumps(merged)})


# How each runtime derives the token it shows. These two strings are a contract
# with the runtime's own code — miniasm's is js/token.js, which has tests
# pinning exactly this — so they are written out rather than derived from the
# runtime id, where a rename would silently break every flag.
TOKEN_SCHEMES = {
    "miniasm": {"message": "miniasm:{id}", "flag": "asm{{{digest}}}"},
}


def compute_token(secret, runtime_id, token_id):
    """The value the runtime will show — and therefore the flag.

    HMAC-SHA256(secret, message), first 12 hex, wrapped in the runtime's own
    format. Both sides compute it; neither transmits it.
    """
    scheme = TOKEN_SCHEMES.get(runtime_id)
    if not scheme:
        raise RuntimeError(
            f"runtime {runtime_id!r} has no token scheme — add one to "
            f"TOKEN_SCHEMES, matching that runtime's own derivation")
    message = scheme["message"].format(id=token_id)
    digest = hmac.new(secret.encode(), message.encode(),
                      hashlib.sha256).hexdigest()[:12]
    return scheme["flag"].format(digest=digest)


def _answer_for(ex, flags, tokens=None):
    """(content, data) — what solves this exercise, and how it is compared.

    Only the flag-backed modes come through here. `checkpoint` does not: its
    code lives on the challenge itself (PLAN.md §25.5), because whether the
    code is required at all depends on the instance's mode.

    `flag`        the answer itself, authored in flags.yaml — the participant
                  discovers it by doing the task, which is how a CTF-shaped
                  subject like shell-1 works. The platform must not overwrite
                  it with a code.
    """
    if ex.validation == "token":
        return tokens[ex.slug], "case_insensitive"
    if ex.validation != "flag":
        raise ValueError(f"{ex.slug}: {ex.validation!r} is not answered by a flag")
    entry = flags[ex.slug]
    if isinstance(entry, dict):
        # `case_insensitive: false` is for an answer where case carries meaning
        # — a command, a filename on a case-sensitive filesystem.
        insensitive = entry.get("case_insensitive", True)
        return str(entry["value"]), "case_insensitive" if insensitive else ""
    return str(entry), "case_insensitive"


def set_flag(ctfd, cid, content, data="case_insensitive"):
    """Make `content` the challenge's only static flag, in place.

    Never delete-then-recreate a flag that is already correct: codes are handed
    out to attendees, so a re-sync must not invalidate them.
    """
    flags = ctfd.api("GET", f"/challenges/{cid}/flags") or []
    keep = next((f for f in flags if f["type"] == "static"), None)
    for f in flags:
        if keep is None or f["id"] != keep["id"]:
            ctfd.api("DELETE", f"/flags/{f['id']}")
    if keep is None:
        ctfd.api("POST", "/flags", json={
            "challenge_id": cid, "type": "static", "content": content,
            "data": data})
    elif keep["content"] != content or (keep.get("data") or "") != data:
        ctfd.api("PATCH", f"/flags/{keep['id']}", json={
            "content": content, "data": data})


def asset_location(subject_slug, asset):
    """Deterministic `<dir>/<filename>` for an image (CTFd requires two parts).

    The content hash is in the filename, so re-syncing unchanged content maps to
    the same location (nothing is re-uploaded) while an edited image lands on a
    new one — no browser cache can serve the old bytes.
    """
    digest = hashlib.sha1(asset.path.read_bytes()).hexdigest()[:8]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", asset.path.stem).strip("-") or "image"
    return f"ws-{subject_slug}/{stem}-{digest}{asset.path.suffix.lower()}"


def upload_assets(ctfd, subject):
    """Upload every referenced image once; return {markdown ref -> /files URL}.

    Type `standard`, not `challenge`: challenge files are gated by CTF time and
    challenge visibility (CTFd/CTFd/views.py:400), so an inline image would 403
    outside the competition window. These are illustrations, not artifacts.
    """
    mapping = {}
    uploaded = 0
    for asset in subject.assets:
        location = asset_location(subject.slug, asset)
        if not ctfd.api("GET", f"/files?location={location}"):
            ctfd.upload(asset.path, location)
            uploaded += 1
        mapping[asset.ref] = f"/files/{location}"
    if subject.assets:
        print(f"images: {len(subject.assets)} referenced, {uploaded} uploaded, "
              f"{len(subject.assets) - uploaded} already present")
    return mapping


def apply_asset_rewrite(subject, mapping):
    """Point every markdown body at the uploaded images, in place.

    Done once over the parsed subject rather than at each call site, so a body
    added to the importer later cannot quietly ship broken image links.
    """
    if not mapping:
        return
    for doc in subject.documents:
        doc.body_md = rewrite_asset_refs(doc.body_md, mapping)
        doc.trailing_md = rewrite_asset_refs(doc.trailing_md, mapping)
    for ex in subject.exercises:
        ex.body_md = rewrite_asset_refs(ex.body_md, mapping)
        ex.context_md = rewrite_asset_refs(ex.context_md, mapping)
        ex.resume_md = rewrite_asset_refs(ex.resume_md, mapping)
        for h in ex.hints:
            h.content_md = rewrite_asset_refs(h.content_md, mapping)
    for q in subject.quizzes:
        q.question = rewrite_asset_refs(q.question, mapping)
    # Not a markdown body — a `cover:` is plain dict values, so it needs a
    # lookup rather than the image-syntax regex. It belongs here anyway: this
    # function exists so that "a body added to the importer later cannot
    # quietly ship broken image links", and the cover is exactly that.
    subject.cover = rewrite_cover_refs(subject.cover, mapping)
    for doc in subject.documents:
        doc.cover = rewrite_cover_refs(doc.cover, mapping, document=doc.path)


def derive_cover(subject):
    """What the platform shows for a subject that declared no `cover:`.

    The floor, not the ceiling. Every subject already carries a one-line
    `project.summary` and almost every one opens with an image, so a subject
    whose author has done nothing still gets a band with a sentence and a
    picture in it — which is the whole point of normalising this: no subject can
    be blank, and declaring a cover is an improvement rather than a prerequisite.

    Only fills what is missing, so a cover that declares a tagline and no image
    still gets the image.
    """
    cover = dict(subject.cover or {})
    cover.setdefault("tagline", (subject.manifest.get("project") or {}).get("summary") or "")

    if not cover.get("media"):
        # The first image of the entrypoint document: the one the author put at
        # the top of the page a participant reads first, which is as close to
        # "the picture of this subject" as anything we can infer.
        entry = (subject.manifest.get("project") or {}).get("entrypoint")
        for asset in subject.assets:
            if entry and entry in asset.documents:
                cover["media"] = asset.ref
                cover["derived"] = True
                break

    return {k: v for k, v in cover.items() if v}


def strip_leading_title(body_md, title):
    """Drop the document's own top heading when it repeats the step name.

    The part heading, the step summary and the statement would otherwise all
    say the same thing three times over.
    """
    lines = body_md.lstrip("\n").split("\n")
    if lines and re.match(r"^#{1,3}\s+", lines[0]):
        heading = re.sub(r"^#{1,3}\s+", "", lines[0]).strip()
        if heading.casefold() == (title or "").strip().casefold():
            return "\n".join(lines[1:]).lstrip("\n")
    return body_md


def upsert_text_step(ctfd, subject_slug, slug, title, body_md, position, existing,
                     kind="ack"):
    """A step whose content is text rather than a task.

    kind="ack"  the text, then a lone Submit button (the subject's intro).

    kind="info"   the text and nothing else: no control at all.
    kind="rating" the text plus "how did it go?" (the subject's outro). Asking
                  for a bare acknowledgement there would be busywork; a
                  satisfaction rating is the one thing worth collecting at the
                  end, and CTFd already stores per-challenge ratings. It counts
                  towards the progress bar, so the workshop sits at 14/15 until
                  it is given — nobody walks away from a bar that close to full.

    `info` does not count; `rating` does.

    Both are typed `quiz` (see plugins/workshop/quiz.py) and worth 0 points.
    """
    return upsert_challenge(ctfd, subject_slug, slug, {
        "name": title, "category": title,
        "description": strip_leading_title(body_md, title),
        "value": 0, "type": "quiz", "quiz_type": kind,
        "quiz_spec": None, "quiz_answers": {}, "position": position,
    }, existing)


def replace_hints(ctfd, cid, hints):
    for h in ctfd.api("GET", f"/challenges/{cid}/hints") or []:
        ctfd.api("DELETE", f"/hints/{h['id']}")
    for h in hints:
        ctfd.api("POST", "/hints", json={
            "challenge_id": cid, "content": h.content_md, "cost": h.cost})


def write_instance_config(ctfd, documents_cfg, optional_ids, free_ids, final_step,
                          *, subjects_cfg=None):
    """The instance-wide settings the workshop page reads back.

    Written in one place because they describe the *instance*, not a subject:
    a workshop of several subjects accumulates them and calls this once
    (PLAN.md §19.1), and a lone subject calls it with its own.

    `subjects_cfg` is keyword-only and defaults to None so the signature stays
    additive for anything that still calls this positionally.
    """
    # Parts, in board order, each naming the subject it belongs to.
    ctfd.api("PATCH", "/configs/workshop_documents",
             json={"value": json.dumps(documents_cfg)})
    # Optional steps, for the page's progress counters. CTFd has no column for
    # "does not count", and inventing one would mean a challenge-model change
    # for a single boolean.
    ctfd.api("PATCH", "/configs/workshop_optional",
             json={"value": json.dumps(sorted(optional_ids))})
    # Free-chapter membership, so the page presents those steps as a menu rather
    # than naming one of them "current" (PLAN.md §15.3).
    ctfd.api("PATCH", "/configs/workshop_free",
             json={"value": json.dumps(sorted(free_ids))})
    # Which closing step ends the whole thing, as opposed to a part or a
    # subject: only that one asks how the workshop went (PLAN.md §17).
    ctfd.api("PATCH", "/configs/workshop_final_step",
             json={"value": json.dumps(final_step)})
    # How each subject presents itself: its name, its accroche and its cover
    # image (§3.2b). Keyed by subject slug rather than folded into
    # `workshop_documents`, whose entries are per *document* — a subject's cover
    # stored once per document would be the same blob N times and would raise
    # "which one wins" the day two disagreed. The documents already carry
    # `subject`, so the join exists.
    if subjects_cfg is not None:
        ctfd.api("PATCH", "/configs/workshop_subjects",
                 json={"value": json.dumps(subjects_cfg)})


def sync(subject_dir, url, admin_user, admin_pass, codes_path=None, *,
         ctfd=None, position_base=0, gate_on=None, standalone=True):
    """Import one subject into an instance.

    `standalone` is what a workshop of several subjects turns off: the
    instance-wide configuration (the document list, the optional/free id sets,
    which closing step ends the workshop) and the public index page then belong
    to the caller, which accumulates them across subjects — see
    tools/sync_workshop.py and PLAN.md §19.

    `position_base` offsets this subject's board positions so a second subject
    lands after the first instead of interleaving with it; `gate_on` is the
    challenge the subject's intro must wait for, which is how an advanced
    subject sits behind the starter.
    """
    problems, advice = lint_all(subject_dir)
    if problems:
        for p in problems:
            print(f"FAIL {p}", file=sys.stderr)
        sys.exit(1)
    # Advice, not a gate. It is printed on the way past because the admin sync
    # page captures this stdout and shows it back — telling an author their
    # subject has no accroche is only useful where they will read it.
    for a in advice:
        print(f"warn {a}")

    subject = parse_subject(subject_dir)
    answers = load_quiz_answers(subject_dir)
    # One admin session for a whole workshop: logging in per subject would work,
    # it would just be rude.
    ctfd = ctfd or CTFdAdmin(url, admin_user, admin_pass)

    existing = existing_by_slug(ctfd, subject.slug)
    stats = {"created": 0, "updated": 0}

    # Images first: every body sent below is rewritten to the uploaded URLs.
    apply_asset_rewrite(subject, upload_assets(ctfd, subject))

    # Validation codes (instructor sheet). A code, once handed out, must survive
    # every later sync, so it is only ever generated for an exercise that has
    # none yet. Precedence: the codes file (admin intent) > the code already
    # live in the instance > a fresh random one.
    codes_file = Path(codes_path) if codes_path else Path(f"instructor_codes.{subject.slug}.yaml")
    codes = {}
    if codes_file.exists():
        # str(): an all-digit code hand-written unquoted parses as an int
        codes = {k: str(v) for k, v in (yaml.safe_load(codes_file.read_text()) or {}).items()}
    flags = load_flags(subject_dir)
    # `validation: token` steps are solved by what the runtime reveals, so the
    # flag is derived here from the instance's secret rather than authored or
    # generated per exercise.
    tokens, secret = {}, None
    if any(ex.validation == "token" for ex in subject.exercises):
        runtime_id = (subject.manifest.get("runtime") or {}).get("id") or ""
        secret = token_secret(ctfd)
        for ex in subject.exercises:
            if ex.validation == "token":
                tokens[ex.slug] = compute_token(secret, runtime_id, ex.token_id)
        print(f"tokens: {len(tokens)} step(s) validated by what the runtime shows")
    # Checkpoint steps of an instance synced before PLAN.md §25 are still
    # `standard` challenges with a Flags row. Convert them before reading any
    # code back — in place, so the ids and every solve on them survive.
    live_codes = migrate_checkpoints(
        ctfd, [existing[ex.slug] for ex in subject.exercises
               if ex.validation == "checkpoint" and ex.slug in existing])
    changed_codes = False
    for ex in subject.exercises:
        if ex.validation != "checkpoint":
            # Only `checkpoint` uses a code. An authored answer would be
            # overwritten by one, and a token is derived from the instance
            # secret — minting codes for either produces an instructor sheet
            # full of strings that solve nothing.
            continue
        if codes.get(ex.slug):
            continue
        codes[ex.slug] = (live_codes.get(existing.get(ex.slug))
                          or secrets.token_hex(3))
        changed_codes = True
    if changed_codes:
        codes_file.write_text(
            "# Validation codes for instructors — one per exercise. NOT public.\n"
            + yaml.safe_dump(codes, allow_unicode=True, sort_keys=True))
        print(f"instructor codes written to {codes_file}")

    # 1. entrypoint document -> index page
    entry = subject.manifest["project"]["entrypoint"]
    entry_doc = next(d for d in subject.documents if d.path == entry)
    pages = ctfd.api("GET", "/pages")
    index = next(p for p in pages if p["route"] == "index")
    # In a workshop of several subjects the public index belongs to the
    # workshop, not to whichever subject synced last (PLAN.md §19).
    if standalone:
        ctfd.api("PATCH", f"/pages/{index['id']}", json={
            "title": entry_doc.title, "route": "index", "content": entry_doc.body_md,
            "format": "markdown", "draft": False, "hidden": False,
            "auth_required": False})
        print(f"page: {entry_doc.title!r} -> index")

    # 1b. The entrypoint document is also a STEP: read it, then acknowledge.
    # It sits first and gates everything that would otherwise start unlocked,
    # so a participant cannot skip straight past the introduction.
    # position_base + 1, not 1: in a workshop the second subject's intro must
    # land after the first subject's last step, or the two interleave on the
    # board and every reading order derived from it is wrong.
    intro_id, created = upsert_text_step(ctfd, subject.slug, "__intro__", entry_doc.title,
                                        entry_doc.body_md, position_base + 1, existing,
                                        kind="ack")
    stats["created" if created else "updated"] += 1
    print(f"step: {entry_doc.title!r} -> intro (acknowledgement)")
    # Everything the parser numbered from 1 moves down one place.
    offset = 1

    # Board positions. The parser numbers every exercise and quiz 1..N in source
    # order; a document that ends in prose also gets a closing step, which has no
    # `order` of its own and has to be slotted in after that document's last
    # numbered node. So walk the numbering once and record where each closing
    # step lands, shifting everything after it down.
    closes_after = {}          # parser order -> the document closing there
    for doc in subject.documents:
        if not doc.trailing_md:
            continue
        orders = ([e.order for e in doc.exercises]
                  + [q.order for q in doc.quizzes]
                  + [q.order for e in doc.exercises for q in e.quizzes])
        if orders:
            closes_after[max(orders)] = doc

    position_of = {}           # parser order -> board position
    outro_position = {}        # document path -> board position
    inserted = 0
    # A second subject starts where the first stopped, or the two interleave on
    # the board and in every "reading order" the platform derives from it.
    offset += position_base
    for order in range(1, max([e.order for e in subject.exercises]
                              + [q.order for q in subject.quizzes] + [0]) + 1):
        position_of[order] = order + offset + inserted
        if order in closes_after:
            inserted += 1
            outro_position[closes_after[order].path] = order + offset + inserted

    # 1c. Parcours path-graph page (platform UI, filled by graph.js). Idempotent.
    parcours = next((p for p in ctfd.api("GET", "/pages") if p["route"] == "parcours"), None)
    parcours_payload = {
        "title": "Parcours", "route": "parcours",
        "content": "# Parcours\n\n<div id=\"ws-parcours\">Loading the path graph…</div>",
        "format": "markdown", "draft": False, "hidden": False, "auth_required": True,
    }
    if parcours:
        ctfd.api("PATCH", f"/pages/{parcours['id']}", json=parcours_payload)
    else:
        ctfd.api("POST", "/pages", json=parcours_payload)
    print("page: 'Parcours' -> /parcours")

    # 1d. runtime declaration -> CTFd config, read back by the workshop page
    # (plugins/workshop/runtime.py). Only the platform-facing keys are sent;
    # `engine`/`language` stay documentation.
    rt = subject.manifest.get("runtime") or {}
    if rt.get("id") and rt.get("version"):
        payload = {k: rt[k] for k in ("id", "version", "title", "src", "external",
                                      "params")
                   if k in rt}
        if secret:
            # The runtime needs the secret to derive the same token; it is
            # per instance and never leaves it.
            payload["params"] = {**(payload.get("params") or {}),
                                 "token_secret": secret}
        payload["pane"] = rt.get("pane") or {}
        ctfd.api("PATCH", "/configs/workshop_runtime",
                 json={"value": json.dumps(payload)})
        print(f"runtime: {rt['id']}@{rt['version']} -> /runtime/{rt['id']}/{rt['version']}/")

    # 2. exercises -> challenges. A `checkpoint` step is a `quiz` challenge of
    # kind checkpoint (the plugin decides whether its code is required, which
    # depends on the instance's mode); everything else stays a `standard`
    # challenge answered by a flag. PLAN.md §25.5.
    ex_ids = {}
    for ex in subject.exercises:
        description = fence_context(ex.context_md, ex.body_md, ex.resume_md)
        payload = {
            "name": ex.title, "category": ex.category,
            "description": description,
            "value": ex.points,
            "position": position_of[ex.order],  # source-reading order → board order
        }
        if ex.validation == "checkpoint":
            payload.update({
                "type": "quiz", "quiz_type": "checkpoint", "quiz_spec": None,
                # The code lives on the challenge itself, in a column no API
                # serializes — never in a Flags row a participant's submission
                # is compared against by CTFd's own code.
                "quiz_answers": {"code": codes[ex.slug]},
            })
        else:
            payload["type"] = "standard"
        cid, created = upsert_challenge(ctfd, subject.slug, ex.slug, payload, existing)
        ex_ids[ex.slug] = cid
        if ex.validation != "checkpoint":
            set_flag(ctfd, cid, *_answer_for(ex, flags, tokens))
        replace_hints(ctfd, cid, ex.hints)
        stats["created" if created else "updated"] += 1

    # How each of those steps is validated, so the answer sheet can say
    # "instructor code" rather than guess from the shape of the string.
    # Quizzes are self-describing (`type`/`quiz_type` on the challenge) and are
    # deliberately not recorded here.
    record_validation(ctfd, {ex_ids[ex.slug]: ex.validation
                             for ex in subject.exercises})

    # 3. quizzes -> quiz challenges
    quiz_ids = {}
    for q in subject.quizzes:
        spec = ({"left": q.left, "right": q.right} if q.kind == "match"
                else {"items": q.items} if q.items else None)
        slug = f"quiz-{q.id}"
        cid, created = upsert_challenge(ctfd, subject.slug, slug, {
            "name": f"Quiz : {q.id}", "category": q.category,
            "description": q.question, "value": q.points, "type": "quiz",
            "quiz_type": q.kind, "quiz_spec": spec,
            "quiz_answers": answers[q.id],
            "position": position_of[q.order],   # source-reading order → board order
        }, existing)
        quiz_ids[q.id] = (cid, q.host_exercise)
        stats["created" if created else "updated"] += 1

    # 3b. trailing prose (bonus / going-further / credits) -> a closing step per
    # document, so no authored content is dropped and each part has an end. The
    # slug carries the document id: a subject with two closing sections would
    # otherwise upsert one challenge twice and lose one of them.
    outro_ids = {}
    for doc in subject.documents:
        if not doc.trailing_md:
            continue
        cid, created = upsert_text_step(ctfd, subject.slug, outro_slug(doc),
                                        doc.trailing_title or doc.title,
                                        doc.trailing_md, outro_position[doc.path],
                                        existing, kind="rating")
        outro_ids[doc.path] = cid
        stats["created" if created else "updated"] += 1
        print(f"step: {(doc.trailing_title or doc.title)!r} -> outro ({doc_slug(doc)} rating)")

    # 3c. Which challenge is which exercise *inside the runtime*. Written after
    # the challenges exist, because it is keyed by their ids: the pane uses it
    # to open the exercise the participant is reading (PLAN.md §21).
    if tokens:
        rt = subject.manifest.get("runtime") or {}
        if rt.get("id") and rt.get("version"):
            payload = {k: rt[k] for k in ("id", "version", "title", "src",
                                          "external", "params") if k in rt}
            payload["pane"] = rt.get("pane") or {}
            params = {**(payload.get("params") or {}), "token_secret": secret,
                      "language": (subject.manifest.get("project", {})
                                   .get("language") or "fr"),
                      "exercises": {str(ex_ids[ex.slug]): ex.token_id
                                    for ex in subject.exercises
                                    if ex.validation == "token"}}
            payload["params"] = params
            ctfd.api("PATCH", "/configs/workshop_runtime",
                     json={"value": json.dumps(payload)})
            print(f"runtime: {len(params['exercises'])} step(s) mapped to their "
                  f"exercise in the pane")

    # 4. prerequisites, walked in board order: intro, then each document's
    # exercises followed by its closing step. Because a document's closing step
    # sits between it and the next document, finishing part N is what unlocks
    # part N+1 — the "starter before advanced" rule (CLAUDE.md), for free.
    prereqs, optional_ids, free_ids = resolve_prerequisites(
        subject, intro_id, ex_ids, outro_ids)
    if gate_on:
        # The whole subject sits behind something in another subject — in
        # practice the starter's closing step, which is the participant saying
        # they are done with it (PLAN.md §19, D2). Gating the intro is enough:
        # everything else already hangs off the intro.
        prereqs[intro_id] = [gate_on]
    for cid, gates in prereqs.items():
        ctfd.api("PATCH", f"/challenges/{cid}", json={
            "requirements": {"prerequisites": gates, "anonymize": True}})
    # Quizzes hang off their host exercise rather than sitting in the spine, so
    # they never block the next step. Hostless ones gate on the intro.
    for cid, host in quiz_ids.values():
        gate = ex_ids[host] if host else intro_id
        ctfd.api("PATCH", f"/challenges/{cid}", json={
            "requirements": {"prerequisites": [gate], "anonymize": True}})

    # 5. One route per document. The Page exists to produce the *route*, not a
    # navbar link — CTFd builds the user menu from Pages plus plugin-registered
    # entries (CTFd/CTFd/plugins/__init__.py:153), and every part Page is
    # `hidden` (below): it stays reachable, it just never shows there. Route
    # itself is served by plugins/workshop/page.py, whose rule beats CTFd's
    # `/<path:route>` catch-all. The Page body is a plain link, so the entry
    # still goes somewhere sane if the plugin is ever unloaded.
    documents_cfg = []
    for doc in subject.documents:
        if not doc.exercises:
            continue           # the entrypoint is the index page, not a part
        slug = doc_slug(doc)
        # Two subjects can both have an `intro.md`-shaped document name; in a
        # workshop the route carries the subject so they cannot collide.
        # `pypong-pypong` helps nobody: a document already named after its
        # subject keeps the one name.
        route_slug = (slug if standalone or slug == subject.slug
                      else f"{subject.slug}-{slug}")
        ids = [ex_ids[e.slug] for e in doc.exercises]
        ids += [quiz_ids[q.id][0] for q in doc.quizzes]
        ids += [quiz_ids[q.id][0] for e in doc.exercises for q in e.quizzes]
        if doc.path in outro_ids:
            ids.append(outro_ids[doc.path])
        route = f"workshop/{route_slug}"
        entry = {"slug": route_slug, "title": doc.title, "route": route,
                 "subject": subject.slug, "subject_title": subject.name,
                 "challenge_ids": ids}
        # Only when the part declares one of its own. The subject's cover lives
        # in `workshop_subjects`, keyed once — storing it again in every
        # document would be the same blob N times and would raise "which one
        # wins" the day two of them disagreed.
        if doc.cover:
            entry["cover"] = doc.cover
        documents_cfg.append(entry)
        upsert_page(ctfd, route, {
            "title": doc.title, "route": route,
            "content": f"[{doc.title}](/{route})",
            "format": "markdown", "draft": False,
            # Always hidden, standalone or not: a navbar entry per part wraps
            # CTFd's fixed navbar onto several lines the moment a subject has
            # more than one or two parts (Pac-Man's "Atelier 1"/"Atelier 2"
            # did exactly this), and it never has to — the navbar-brand logo
            # auto-redirects every signed-in participant through `/workshop`
            # (landing.py) on every page, independent of any Page's `hidden`
            # flag, and `/workshop` already renders a proper card index with
            # per-part progress for 2+ documents (page.py, workshop_index.html)
            # or forwards straight through for exactly one. Nobody is stranded
            # either way, so there is no case left for the standalone
            # exception to protect — dropping it also means a subject that is
            # standalone today and grows a second part later doesn't silently
            # regress into the same wrap. `hidden` keeps the route, it only
            # drops the link; Parcours and Challenges stay in the navbar
            # unconditionally as the two other ways in.
            "hidden": True,
            "auth_required": True,
        })
        print(f"page: {doc.title!r} -> /{route} ({len(ids)} steps)")

    # Every step must appear on at least one part page. The entrypoint step
    # (`__intro__`) belongs to none: intro.md has no exercises, so it gets no
    # route — yet it gates the first exercise of part 1. Left out, part 1 shows
    # nothing but locked steps blocked by a step that is on no part page, and
    # the participant has no way to unlock anything from there. Fold orphans
    # into the first part; `position` still decides where they render.
    if documents_cfg:
        every = ({intro_id} | set(ex_ids.values()) | set(outro_ids.values())
                 | {cid for cid, _ in quiz_ids.values()})
        assigned = {cid for d in documents_cfg for cid in d["challenge_ids"]}
        orphans = sorted(every - assigned)
        if orphans:
            documents_cfg[0]["challenge_ids"] = orphans + documents_cfg[0]["challenge_ids"]
            print(f"part: {len(orphans)} step(s) outside any document folded "
                  f"into {documents_cfg[0]['title']!r}")

    final = next((outro_ids[d.path] for d in reversed(subject.documents)
                  if d.path in outro_ids), None)
    last_position = max([*position_of.values(), *outro_position.values(), 0])

    # In a workshop these four are the caller's to accumulate: each is
    # instance-wide, so a second subject writing them would erase the first
    # (PLAN.md §19.1).
    cover = derive_cover(subject)
    subject_cfg = {
        "title": subject.name,
        "summary": (subject.manifest.get("project") or {}).get("summary") or "",
        **cover,
    }
    if standalone:
        write_instance_config(ctfd, documents_cfg, optional_ids, free_ids, final,
                              subjects_cfg={subject.slug: subject_cfg})
    if optional_ids:
        print(f"optional: {len(optional_ids)} step(s) excluded from the counters")
    if free_ids:
        print(f"free: {len(free_ids)} step(s) in a free chapter, openable in any order")

    print(f"sync done: {stats['created']} created, {stats['updated']} updated, "
          f"{len(subject.exercises)} exercises and {len(outro_ids)} closing steps "
          f"chained, {len(quiz_ids)} quizzes, {len(documents_cfg)} parts")
    return {
        "subject": subject,
        "ex_ids": ex_ids,
        "quiz_ids": quiz_ids,
        "intro_id": intro_id,
        "outro_ids": outro_ids,
        "documents": documents_cfg,
        "optional_ids": optional_ids,
        "free_ids": free_ids,
        # The closing step of this subject's last document: what the next
        # subject waits for, and what ends the workshop if it is the last one.
        "final_step": final,
        "runtime": subject.manifest.get("runtime") or {},
        # Declared or derived, already pointed at uploaded URLs. The workshop
        # caller accumulates these the way it accumulates runtime params.
        "cover": subject_cfg,
        "last_position": last_position,
        "stats": stats,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("subject_dir")
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--admin-pass", required=True)
    ap.add_argument("--codes", help="validation codes YAML (generated if absent)")
    args = ap.parse_args()
    sync(args.subject_dir, args.url, args.admin_user, args.admin_pass, args.codes)



if __name__ == "__main__":
    main()
