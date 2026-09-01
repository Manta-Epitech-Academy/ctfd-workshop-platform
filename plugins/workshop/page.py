"""Single-page workshop view (`/workshop`) — the participant-facing UI.

Replaces the modal-per-challenge board for workshop use. The whole subject is
one scrollable document:

    <workshop title>
    [ sticky stepper: part 1 › part 2 › part 3 … ]     progress at a glance
    ## <part>                      (challenge category = chapter heading)
      [ stepper: step › step › step ]                  only if the part has >1
      > <step>   accordion: statement + hints + submit
      > <step>

Why server-rendered rather than client-rendered like the Parcours page: the
statement HTML, the hint list and the quiz controls all exist server-side
already (`Challenges.html`, `Hints`, `quiz_spec`), and rendering them here
means the quiz controls have exactly one implementation for this page
(templates/workshop_step_body.html) instead of a JS copy that can drift.

The same partial is served by `/api/v1/workshop/step/<id>` so that a step
which unlocks *while the page is open* can be filled in place, without a
reload and without shipping locked statements to the browser up front.

Visibility: a locked step shows its **title** (the workshop syllabus is not a
secret — a participant should see what is coming) but never its statement,
hints or quiz. Flip REVEAL_LOCKED_TITLES to hide titles too, CTF-style.
"""
import json
import re
from itertools import groupby

from flask import Blueprint, abort, jsonify, redirect, render_template, url_for

from CTFd.models import Challenges, Hints, HintUnlocks, Ratings
from CTFd.utils import get_config
from CTFd.utils.challenges import get_solve_ids_for_user_id
from CTFd.utils.decorators import authed_only, during_ctf_time_only
from CTFd.utils.decorators.visibility import check_challenge_visibility
from CTFd.utils.helpers import markup
from CTFd.utils.user import get_current_user

from .mode import is_self_serve
from .runtime import declared_runtime

workshop_page = Blueprint(
    "workshop_page", __name__, template_folder="templates"
)

# A locked step still shows its name (workshop syllabus, not a CTF secret).
REVEAL_LOCKED_TITLES = True

DONE, CURRENT, TODO, LOCKED = "done", "current", "todo", "locked"
# A closing note: readable, never completable, and counted in nothing. It must
# not become CURRENT either — "in progress" is meaningless for something with
# no control.
INFO = "info"

# A pure note is text rather than work: it stays out of the progress counters
# and never becomes the current step. `rating` is NOT in here on purpose — the
# workshop rating is the last step and it counts, so the bar reads 14/15 until
# it is given. Nobody abandons a workshop at 93%; that is the whole point.
NON_TASK_KINDS = ("info",)


def _ordered_challenges():
    """Board order = the reading order the sync wrote into `position`."""
    return (
        Challenges.query.filter(Challenges.state != "hidden")
        .order_by(Challenges.position.is_(None), Challenges.position, Challenges.id)
        .all()
    )


def _prerequisites(challenge, known_ids):
    reqs = (challenge.requirements or {}).get("prerequisites", [])
    return [r for r in reqs if r in known_ids]


def _hints(challenge, account_id):
    """Hint stubs; content only for hints this account already revealed.

    Revealing goes through CTFd's native POST /api/v1/unlocks (see
    assets/workshop_page.js) so usage stays recorded in `HintUnlocks`.
    """
    unlocked = {
        u.target
        for u in HintUnlocks.query.filter_by(account_id=account_id).all()
    }
    return [
        {
            "id": h.id,
            "cost": h.cost or 0,
            "unlocked": h.id in unlocked,
            "html": h.html if h.id in unlocked else None,
        }
        for h in Hints.query.filter_by(challenge_id=challenge.id)
        .order_by(Hints.id)
        .all()
    ]


def _rating(challenge, user):
    """This user's rating of this step, and whether rating is allowed at all.

    CTFd stores ratings per challenge (`PUT /challenges/<id>/ratings`, value
    +1/-1, solved challenges only) — we reuse it rather than inventing a store.
    """
    if get_config("challenge_ratings", default="public") == "disabled":
        return {"enabled": False, "value": None, "review": ""}
    rating = Ratings.query.filter_by(user_id=user.id, challenge_id=challenge.id).first()
    return {
        "enabled": True,
        "value": rating.value if rating else None,
        "review": (rating.review if rating else "") or "",
    }


# The prose a document opens with is carried into its first exercise so an
# imported exercise is self-sufficient reading on CTFd's own board. On the
# workshop page that makes it look like the first task starts with a lecture,
# so the sync fences it and the page lifts it out — see split_context() in
# tools/sync_subject.py. HTML comments survive CTFd's renderer intact.
CONTEXT_OPEN = "<!-- ws:context -->"
CONTEXT_CLOSE = "<!-- /ws:context -->"
# The author's own short version of a step (convention §3.4), fenced by the
# sync the same way. It is shown *with* the statement and never instead of it
# — PLAN.md §25.7 — so it rides in the body rather than beside it.
RESUME_OPEN = "<!-- ws:resume -->"
RESUME_CLOSE = "<!-- /ws:resume -->"


def _split_fenced(html, open_tag, close_tag):
    """(fenced, rest) — one marked region lifted out of a challenge body."""
    if not html or open_tag not in html or close_tag not in html:
        return "", html
    before, rest = html.split(open_tag, 1)
    inside, after = rest.split(close_tag, 1)
    return inside.strip(), (before + after).strip()


def _split_context(html):
    """(lead, statement) — the fenced opening prose, and the step's own body."""
    return _split_fenced(html, CONTEXT_OPEN, CONTEXT_CLOSE)


def _lead(challenge):
    """The part's opening prose, carried by whichever step it precedes.

    Deliberately *not* part of `_body`: a body is withheld while a step is
    locked, and the lead must not be. It is the part's introduction — the same
    category as a step title, which locked steps already show, and the exercise
    after it routinely says "reuse the code above". Hiding it leaves a heading
    with nothing under it and an exercise referring to something invisible.
    """
    return markup(_split_context(challenge.html)[0])


# What the control under a step is, and what the line beside it says. Both
# depend on the instance's mode for a checkpoint step, so both are decided here
# at request time rather than written into the content at import (PLAN.md
# §25.6). English, like every other platform string (CLAUDE.md).
NOTES = {
    "code": "When your work is finished, ask the instructor for the validation code.",
    "done": "Nobody checks this for you: mark the step done once your work is finished.",
    "token": "When the tests pass, the app shows a token — paste it here to "
             "validate the step.",
}


def _answer_kind(challenge, validation):
    """Which control this step gets, and therefore what the form sends.

    `code` and `done` are the same challenge seen in the two modes: the
    instructor's code is asked for in a room, and in a self-serve instance
    there is nobody to ask, so pressing the button is the completion.
    """
    if challenge.type == "quiz":
        kind = getattr(challenge, "quiz_type", None)
        if kind == "checkpoint":
            return "done" if is_self_serve() else "code"
        return kind or "flag"
    return "token" if validation == "token" else "flag"


def _validation_modes():
    """challenge id -> the validation mode the content asked for.

    Written by the sync (`workshop_validation`), and the only way to tell a
    `token` step from a `flag` one: both are standard challenges with a static
    flag. Empty on an instance synced before that key existed, where every such
    step simply reads as a flag — which is what it looks like anyway.
    """
    raw = get_config("workshop_validation")
    if not raw:
        return {}
    try:
        modes = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return {int(k): v for k, v in modes.items() if str(k).isdigit()}


def _body(challenge, user, validation=None):
    """Everything a participant needs to actually do the step."""
    lead, rest = _split_context(challenge.html)
    summary, statement = _split_fenced(rest, RESUME_OPEN, RESUME_CLOSE)
    kind = _answer_kind(challenge, validation)
    return {
        "description": markup(statement),
        # The author's short version, folded open above the statement. A
        # summary, never a substitute: §24 is what happens when a step's own
        # words are withheld from the person reading it.
        "summary": markup(summary),
        "hints": _hints(challenge, user.account_id),
        "quiz_type": getattr(challenge, "quiz_type", None),
        "quiz_spec": getattr(challenge, "quiz_spec", None) or {},
        "answer_kind": kind,
        "note": NOTES.get(kind, ""),
        "rating": _rating(challenge, user),
    }


def _steps(user):
    solved = set(get_solve_ids_for_user_id(user.id))
    challenges = _ordered_challenges()
    known = {c.id for c in challenges}
    names = {c.id: c.name for c in challenges}
    optional = _optional_ids()
    free = _free_ids()
    final_id = _final_step_id()
    validation = _validation_modes()

    steps, current_taken = [], False
    for c in challenges:
        prereqs = _prerequisites(c, known)
        unlocked = set(prereqs).issubset(solved)
        is_solved = c.id in solved

        informational = getattr(c, "quiz_type", None) in NON_TASK_KINDS
        if not unlocked:
            state = LOCKED
        elif is_solved:
            # A rated outro reads as done; a plain note is never solved at all.
            state = DONE
        elif informational:
            state = INFO
        elif c.id in free or c.id in optional:
            # Available, but not "the" next thing, so it never takes CURRENT and
            # never auto-opens. A free chapter opens several at once and its
            # author said explicitly they may be done in any order — naming one
            # "current" would pick whichever happens to be first in source
            # order, the linear reading they refused. A bonus step is skippable
            # by definition, so pointing the participant at it over the required
            # work that follows gets the priority backwards.
            state = TODO
        elif not current_taken:
            state, current_taken = CURRENT, True
        else:
            state = TODO

        step = {
            "id": c.id,
            "name": c.name if (unlocked or is_solved or REVEAL_LOCKED_TITLES) else "???",
            "category": c.category,
            "points": c.value,
            "type": c.type,
            "state": state,
            "solved": is_solved,
            "counts": not informational and c.id not in optional,
            # Distinct from `counts`, which is also false for a plain note: this
            # one means "bonus work", and it is what the step summary shows. A
            # participant who does not read the chapter intro has no other way
            # to tell four skippable steps from three required ones.
            "optional": c.id in optional,
            # The closing step of the last part ends the workshop; the others
            # end a part, and say so.
            "final": final_id is not None and c.id == final_id,
            "unlocked": unlocked,
            # what the participant must finish first, so a locked step explains
            # itself instead of just refusing. `_resolve_links` adds the href.
            "blocked_by": [{"id": p, "name": names[p]}
                           for p in prereqs if p not in solved],
            # Teaching prose, not a statement: always rendered (see _lead).
            "lead": _lead(c),
            "body": (_body(c, user, validation.get(c.id))
                     if (unlocked or is_solved) else None),
        }
        steps.append(step)
    return steps


def _aggregate(members):
    """State and progress of a group of steps — a category, or a document.

    Exactly one step in the whole workshop is CURRENT, so exactly one group is
    "in progress"; the groups after it are TODO or LOCKED. That is what makes
    the index readable at a glance: one card says "you are here".
    """
    counted = [s for s in members if s["counts"]]
    done = sum(1 for s in counted if s["solved"])
    if not counted:
        # A group of pure reading (the outro): it has no score to show.
        state = LOCKED if all(s["state"] == LOCKED for s in members) else INFO
    elif done == len(counted):
        state = DONE
    elif any(s["state"] == CURRENT for s in members):
        state = CURRENT
    elif all(s["state"] == LOCKED for s in members):
        state = LOCKED
    else:
        state = TODO
    return {"state": state, "done": done, "total": len(counted)}


LEAD_TITLE = re.compile(r"\A\s*<p>\s*<strong>(.*?)</strong>\s*</p>", re.S)


def _part_lead(members, name, page_title):
    """The opening prose of a part, lifted out of its first step.

    The parser prefixes that prose with its own heading, which is usually
    either the part heading or the document's — printing it again above the
    steps would say the same thing three times, so a leading bold line that
    repeats one of them goes.
    """
    lead = next((s["lead"] for s in members if s.get("lead")), "")
    if not lead:
        return ""
    m = LEAD_TITLE.match(lead)
    repeats = {(name or "").strip(), (page_title or "").strip()} - {""}
    if m and m.group(1).strip() in repeats:
        lead = lead[m.end():].lstrip()
    return markup(lead)


def _parts(steps, page_title=None):
    """Group consecutive steps by category — the chapter headings of the subject."""
    parts = []
    for index, (category, group) in enumerate(
        groupby(steps, key=lambda s: s["category"]), start=1
    ):
        members = list(group)
        parts.append({"index": index, "name": category, "steps": members,
                      "lead": _part_lead(members, category, page_title),
                      **_aggregate(members)})
    return parts


def _id_set(config_key):
    """A set of challenge ids the sync wrote to a config, or empty."""
    raw = get_config(config_key)
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except (TypeError, ValueError):
        return set()


def _optional_ids():
    """Steps that are bonus work: they count towards nothing.

    Written by the sync from `optional: true` in the content. Without this a
    subject with bonus steps can never read 100%, and 100% is what gets the
    end-of-workshop feedback in (see the rating step).
    """
    return _id_set("workshop_optional")


def _free_ids():
    """Steps belonging to a `topology: free` chapter — a menu, not a chain.

    Membership comes from the sync rather than being inferred from the graph:
    sharing a prerequisite does not mean two steps are alternatives (every
    hostless quiz shares the intro gate with the first exercise), and guessing
    it wrong costs the page its "you are here" marker.
    """
    return _id_set("workshop_free")


def _final_step_id():
    """The closing step that ends the subject, not just a part.

    Each document gets its own closing step, so a multi-part subject has
    several; only the last one is the end of the workshop, and only it should
    ask how the workshop went. Written by the sync, which is what knows the
    document order.
    """
    raw = get_config("workshop_final_step")
    # `get_config` hands back an int when the stored string is all digits
    # (CTFd/utils/__init__.py:51), so this one arrives already parsed — unlike
    # the id *lists* above, which stay strings and need json.loads.
    if isinstance(raw, int):
        return raw
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _documents():
    """The subject's documents, as written by the sync (`workshop_documents`).

    Each is one *part* of the workshop with its own route and navbar entry.
    Empty for a subject synced before per-document routes existed, in which
    case `/workshop` remains the only view — which is also the right answer for
    a single-document subject.
    """
    raw = get_config("workshop_documents")
    if not raw:
        return []
    try:
        docs = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [d for d in docs if d.get("slug") and d.get("challenge_ids")]


def _resolve_links(steps, documents, visible_ids):
    """Point every "finish X first" at the page where X can actually be done.

    A per-document route shows one slice of the chain, so the step that blocks
    it usually lives on another page. Naming it is not enough: a locked page
    whose only explanation names something not on it reads as broken rather
    than as not-yet.
    """
    where = {cid: d["slug"] for d in documents for cid in d["challenge_ids"]}
    for step in steps:
        for blocker in step["blocked_by"]:
            anchor = f"#step-{blocker['id']}"
            if blocker["id"] in visible_ids:
                blocker["href"] = anchor
            elif blocker["id"] in where:
                blocker["href"] = f"/workshop/{where[blocker['id']]}{anchor}"
            else:
                blocker["href"] = f"/workshop{anchor}"


def _open_states(steps):
    """Which steps start unfolded.

    The current step, normally. But a part can be entirely locked (part 2 before
    part 1 is finished), and a page of collapsed padlocks with nothing open is
    indistinguishable from a read-only page — so unfold the first one, which is
    the one carrying the explanation and the link out.
    """
    for step in steps:
        step["open"] = step["state"] == CURRENT
    if steps and not any(s["open"] for s in steps):
        blocked = next((s for s in steps if s["state"] == LOCKED), None)
        if blocked is not None:
            blocked["open"] = True


def _runtime_for(subject):
    """The runtime declaration, with this subject's parameters applied.

    One dist serves every subject that shares a runtime; what differs is which
    machine to boot, and that travels per subject (PLAN.md §19.2). A page
    belonging to a subject therefore hands the frame *its* parameters, not the
    first subject's.
    """
    runtime = declared_runtime()
    if not runtime:
        return None
    params = (runtime.get("subjects") or {}).get(subject)
    if params:
        runtime = {**runtime, "params": params}
    return runtime


def _render(user, keep_ids=None, title=None, subject=None):
    steps = _steps(user)
    if keep_ids is not None:
        steps = [s for s in steps if s["id"] in keep_ids]
    documents = _documents()
    _resolve_links(steps, documents, {s["id"] for s in steps})
    _open_states(steps)
    return render_template(
        "workshop_page.html",
        parts=_parts(steps, title),
        documents=documents,
        page_title=title,
        runtime=_runtime_for(subject),
        # Both halves of the ratio count the same population, or the outro
        # would push it to "15 / 14".
        solved_count=sum(1 for s in steps if s["counts"] and s["solved"]),
        total_count=sum(1 for s in steps if s["counts"]),
    )


CARD_TEXT = {
    DONE: ("Completed", "Review"),
    CURRENT: ("In progress", "Continue"),
    TODO: ("Not started", "Start"),
    INFO: ("Just something to read", "Read"),
    LOCKED: ("Locked", "Preview"),
}


def _index_cards(user, documents):
    """One card per part: where it stands, and what opens it if it is locked.

    Built from the same `_steps` pass as the part pages, so the two can never
    disagree about what is unlocked.
    """
    steps = _steps(user)
    # Nothing is on this page, so every blocker links to the part page it lives
    # on rather than to a local anchor.
    _resolve_links(steps, documents, visible_ids=set())

    cards = []
    for index, doc in enumerate(documents, start=1):
        ids = set(doc["challenge_ids"])
        members = [s for s in steps if s["id"] in ids]   # board order
        card = {"index": index, "title": doc["title"], "slug": doc["slug"],
                "href": f"/workshop/{doc['slug']}", **_aggregate(members)}
        # A locked part names one thing, not every unmet prerequisite: the first
        # step it cannot start, and what that step waits on. "Finish part 1"
        # is actionable; a list of six ids is not.
        blocked = next((s for s in members if s["state"] == LOCKED), None)
        card["blocked_by"] = (blocked["blocked_by"][:1] if blocked else [])

        card["label"], card["action"] = CARD_TEXT[card["state"]]
        if card["state"] == CURRENT and not card["done"]:
            # Open, and nothing done in it yet: "In progress / Continue" would
            # be a lie about work that has not started.
            card["label"], card["action"] = "Available now", "Start"
        card["subject"] = doc.get("subject") or ""
        card["subject_title"] = doc.get("subject_title") or ""
        cards.append(card)
    return cards, steps


def _subject_groups(cards):
    """Cards grouped by subject, for a workshop that has more than one.

    A workshop is a starter subject and then advanced ones (PLAN.md §19), and a
    flat grid of eight cards hides that shape. Grouping shows it, and each group
    carries its own progress: telling somebody who just finished the whole
    starter that they are at 40% is discouraging and wrong (§19, D5).

    Returns [] for a single-subject workshop, which keeps its flat grid.
    """
    if len({c["subject"] for c in cards}) < 2:
        return []
    groups = []
    for card in cards:
        if not groups or groups[-1]["subject"] != card["subject"]:
            groups.append({"subject": card["subject"],
                           "title": card["subject_title"] or card["subject"],
                           "cards": []})
        groups[-1]["cards"].append(card)
    for index, group in enumerate(groups, start=1):
        group["index"] = index
        # Parts are numbered within their subject: "Part 2" for the first part
        # of the second subject would be counting the wrong thing.
        for number, card in enumerate(group["cards"], start=1):
            card["index"] = number
        group["done"] = sum(c["done"] for c in group["cards"])
        group["total"] = sum(c["total"] for c in group["cards"])
        states = {c["state"] for c in group["cards"]}
        group["state"] = (DONE if states == {DONE}
                          else LOCKED if states == {LOCKED}
                          else CURRENT)
    return groups


@workshop_page.route("/workshop")
@during_ctf_time_only
@check_challenge_visibility
@authed_only
def workshop():
    """The workshop entry point, which depends on how the subject is shaped.

    One part: there is nothing to choose, so go straight to it — an index of a
    single card is a click that teaches nothing. Several parts: an index of
    cards, which is the only page that shows the shape of the whole workshop.
    No parts at all (a subject synced before per-document routes existed): the
    old behaviour, every step in one page.
    """
    user = get_current_user()
    documents = _documents()
    if not documents:
        return _render(user)
    if len(documents) == 1:
        return redirect(url_for("workshop_page.workshop_document",
                                doc_slug=documents[0]["slug"]))

    cards, steps = _index_cards(user, documents)
    return render_template(
        "workshop_index.html",
        cards=cards,
        groups=_subject_groups(cards),
        page_title=get_config("ctf_name"),
        solved_count=sum(1 for s in steps if s["counts"] and s["solved"]),
        total_count=sum(1 for s in steps if s["counts"]),
    )


@workshop_page.route("/workshop/<doc_slug>")
@during_ctf_time_only
@check_challenge_visibility
@authed_only
def workshop_document(doc_slug):
    """One part of the subject. Same page, filtered to that document's steps.

    Progress is counted within the part, so each one reads "3 / 10" on its own
    rather than as a slice of the whole subject.
    """
    doc = next((d for d in _documents() if d["slug"] == doc_slug), None)
    if doc is None:
        abort(404)
    return _render(get_current_user(), keep_ids=set(doc["challenge_ids"]),
                   title=doc["title"], subject=doc.get("subject"))


@workshop_page.route("/api/v1/workshop/step/<int:challenge_id>", methods=["GET"])
@during_ctf_time_only
@check_challenge_visibility
@authed_only
def step_body(challenge_id):
    """Body of a step that just unlocked — same partial the page renders."""
    user = get_current_user()
    step = next((s for s in _steps(user) if s["id"] == challenge_id), None)
    if step is None:
        abort(404)
    if step["body"] is None:
        abort(403)
    return jsonify({
        "success": True,
        "data": {
            "id": step["id"],
            "name": step["name"],
            "html": render_template("workshop_step_body.html", step=step),
        },
    })


def load_page(app):
    app.register_blueprint(workshop_page)
