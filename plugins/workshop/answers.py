"""The answer sheet (PLAN.md §23) — every step's answer, plus who is where.

The problem this solves is logistical, not technical. Answers live in three
places and none of them is reachable from the room: authored flags are in the
subject repo (now GPG-encrypted), checkpoint codes are minted per instance and
written to `instructor_codes.<subject>.yaml` on the maintainer's laptop, and
quiz answers are a JSON column that no CTFd page renders. An instructor
standing in front of a class has none of it. CTFd's own admin panel cannot
help: flags appear one challenge at a time on the edit form
(CTFd/admin/challenges.py:49) and quiz answers appear nowhere.

So: one page, in the instance, showing what *this* instance will accept.

That last word matters. Only `validation: flag` answers are the same
everywhere — they are authored. Checkpoint codes are `token_hex(3)` minted per
instance, and tokens are an HMAC of the instance's own secret, both on purpose
(tools/sync_subject.py:250) so last session's sheet is worthless in this one.
Reading the live database rather than any file is what makes the page correct
for all three without knowing which it is looking at.

Admin-only for now. CTFd has exactly two user types — `user` and `admin`
(models/__init__.py:434) — and no group system, so a real instructor tier has
to be a plugin-side role. That is deferred with instructor-led mode; when it
lands, the decorator on these two routes is the only thing that changes.
"""
import csv
import io
import json

from flask import Blueprint, Response, render_template

from CTFd.models import Flags, Solves, Users, db
from CTFd.utils import get_config
from CTFd.utils.decorators import admins_only

from flask_babel import lazy_gettext as _l

from .mode import LABELS as MODE_NAMES, current_mode, is_self_serve
from .page import _documents, _validation_modes
from .progress import counts as _counts
from .progress import optional_ids as _optional_ids
from .progress import ordered_challenges as _ordered_challenges

workshop_answers = Blueprint("workshop_answers", __name__,
                             template_folder="templates")

# What each mode is called on the page. The label says where the answer comes
# from, because that is what tells an instructor whether it is theirs to read
# out (a checkpoint code) or the participant's to find (a flag).
MODE_LABELS = {
    "checkpoint": "Instructor code",
    "flag": "Answer",
    "token": "Runtime token",
    "quiz": "Quiz",
    "quizset": "Quiz — several questions",
    "single": "Quiz — one answer",
    "multiple": "Quiz — several answers",
    "match": "Quiz — pairs",
    "freeform": "Quiz — free text",
    "ack": "Acknowledgement",
    "info": "Note",
    "rating": "Feedback",
    "unknown": "No flag",
}

# Modes whose answer is per-instance. Shown as a warning on the page: printing
# this sheet and reusing it at the next session will not work.
PER_INSTANCE = ("checkpoint", "token")

# Steps that are not work at all — no answer to show, and they count for
# nothing in the progress column.
NO_ANSWER = ("info", "rating", "ack")


# `_validation_modes` (challenge id -> the mode the content asked for, as the
# sync recorded it) lives in page.py, which reads the same map to decide what a
# step's answer control is. Empty on an instance synced before that key
# existed, which is why every caller here still falls back to _infer_mode.


def _token_ids():
    """Steps the runtime validates, from the runtime config the sync writes.

    Recoverable even on an old instance: `params.exercises` is keyed by
    challenge id (tools/sync_subject.py:690), so it identifies token steps
    exactly, with no guessing.
    """
    raw = get_config("workshop_runtime")
    if not raw:
        return set()
    try:
        params = (json.loads(raw) or {}).get("params") or {}
    except (TypeError, ValueError):
        return set()
    return {int(k) for k in (params.get("exercises") or {}) if str(k).isdigit()}


def _is_code(content):
    """Does this look like a generated checkpoint code — `token_hex(3)`?"""
    return (len(content) == 6
            and all(c in "0123456789abcdef" for c in content))


def _infer_mode(challenge, content, token_ids):
    """Best effort for an instance synced before the mode was recorded.

    Two of the three are certain: a token step is named in the runtime config,
    and a quiz carries its kind on the row. Only checkpoint-vs-flag is a guess,
    made on the shape of the code, and the page says so rather than presenting
    a guess as a fact.
    """
    if challenge.type == "quiz":
        return getattr(challenge, "quiz_type", None) or "quiz", False
    if challenge.id in token_ids:
        return "token", False
    if content and _is_code(content):
        return "checkpoint", True
    if content:
        return "flag", True
    return "unknown", False


def _one_answer(kind, answers):
    """One question's correct answer, rendered the way the author wrote it.

    Shapes are the tolerant ones quiz.py grades (bare values accepted), so
    each branch handles both the dict form and the shorthand. Taking `kind`
    as an argument rather than reading the challenge is what lets a quizset
    reuse it: inside one, every question is graded by passing its own dict to
    the same grader (quiz.py `_grade_quizset`), so the answer shapes are
    identical to a standalone quiz's.
    """
    if kind == "checkpoint":
        # The instructor's code. It is stored whatever the instance's mode is,
        # and only *required* in instructor-led — see plugins/workshop/mode.py.
        code = answers.get("code") if isinstance(answers, dict) else answers
        return str(code or "")
    if kind == "single":
        value = answers.get("answer") if isinstance(answers, dict) else answers
        return str(value)
    if kind == "multiple":
        values = answers.get("answers") if isinstance(answers, dict) else answers
        return ", ".join(str(v) for v in values or [])
    if kind == "match":
        pairs = (answers.get("pairs") if isinstance(answers, dict) and "pairs" in answers
                 else answers)
        return ", ".join(f"{k} → {v}" for k, v in (pairs or {}).items())
    if kind == "freeform":
        if isinstance(answers, dict):
            patterns = answers.get("patterns") or []
        else:
            patterns = answers if isinstance(answers, list) else [answers]
        return "  |  ".join(str(p) for p in patterns)
    return ""


def _questions(payload):
    """The question list of a quizset, in either shape quiz.py accepts."""
    if isinstance(payload, dict):
        payload = payload.get("questions")
    return payload if isinstance(payload, list) else []


def _labels(spec_question, value):
    """What the letters in `value` actually say, from the step's own spec.

    "1. B" tells an instructor nothing on its own — they would have to open the
    subject to know what B was, which is the lookup this page exists to spare
    them. The spec carries the items, so the answer can read "B — Droite".

    Backticks go: the item text is authored markdown (`` `true` ``) and this is
    a table cell, not a rendered page. Silent when the spec has no items, which
    is the case for a freeform answer and for any step imported before specs
    carried them.
    """
    items = (spec_question or {}).get("items") or []
    texts = {str(i.get("letter", "")).strip().upper(): str(i.get("text", ""))
             for i in items if isinstance(i, dict)}
    out = []
    for letter in (v.strip() for v in value.split(",")):
        text = texts.get(letter.upper())
        if text:
            out.append(text.replace("`", "").strip())
    return out


def _quiz_answer_rows(challenge):
    """The answer as the page shows it: one row per question of the step.

    A quizset is several questions in one step, and the participant submits
    them joined by "|" (quiz.py `_grade_quizset`). Flattening that into one
    string is what made this page say "nothing to answer" for every quizset in
    the subject: `_quiz_answer` had no branch for the kind at all. One row per
    question instead, numbered the way the "look again at question 2" message
    numbers them, so the sheet and the message agree.

    A single-question step returns one unnumbered row, which is every other
    kind of answer this page already showed.
    """
    kind = getattr(challenge, "quiz_type", None)
    answers = getattr(challenge, "quiz_answers", None)
    if kind in NO_ANSWER or answers is None:
        return []
    if kind != "quizset":
        value = _one_answer(kind, answers)
        return [{"n": None, "value": value, "labels": []}] if value else []

    spec = _questions(getattr(challenge, "quiz_spec", None))
    rows = []
    for i, question in enumerate(_questions(answers)):
        if not isinstance(question, dict):
            continue
        value = _one_answer(question.get("kind", "single"), question)
        if not value:
            continue
        rows.append({
            "n": i + 1,
            "value": value,
            "labels": _labels(spec[i] if i < len(spec) else None, value),
        })
    return rows


def _quiz_answer(challenge):
    """The same answer as one string, for the CSV and for anything that wants
    a cell rather than a list."""
    rows = _quiz_answer_rows(challenge)
    if len(rows) == 1:
        return rows[0]["value"]
    return " · ".join(f"{r['n']}. {r['value']}" for r in rows)


def _quiz_notes(challenge):
    """Anything about a quiz answer that changes how it is graded.

    Only freeform has such a knob: it matches case-insensitively unless the
    content asked otherwise (plugins/workshop/quiz.py:93), so the note marks
    the exception rather than restating the default on every row.
    """
    if getattr(challenge, "quiz_type", None) != "freeform":
        return []
    answers = getattr(challenge, "quiz_answers", None)
    if isinstance(answers, dict) and answers.get("case_sensitive"):
        return ["case-sensitive"]
    return []


def _flags_by_challenge():
    """Every flag row, grouped. The sync keeps one static flag per challenge,
    but reading them all means a hand-added second flag is never hidden."""
    grouped = {}
    for flag in Flags.query.all():
        grouped.setdefault(flag.challenge_id, []).append(flag)
    return grouped


def _row(challenge, flags, modes, token_ids, solvers):
    static = [f for f in flags if f.type == "static"]
    other = [f for f in flags if f.type != "static"]
    content = static[0].content if static else ""

    recorded = modes.get(challenge.id)
    if challenge.type == "quiz":
        # The kind is on the row itself and is never a guess, so it wins over
        # anything recorded for the challenge.
        mode = getattr(challenge, "quiz_type", None) or "quiz"
        guessed = False
    elif recorded:
        mode, guessed = recorded, False
    else:
        mode, guessed = _infer_mode(challenge, content, token_ids)

    if challenge.type == "quiz":
        answer_rows = _quiz_answer_rows(challenge)
        answer = _quiz_answer(challenge)
    else:
        answer_rows = []
        answer = "  |  ".join(f.content for f in static)

    notes = _quiz_notes(challenge)
    # The sync writes `case_insensitive` on every flag it creates
    # (tools/sync_subject.py:354), so that is the default and repeating it on
    # every row says nothing. A flag added by hand may not be, and that is
    # worth a word.
    if any((f.data or "") != "case_insensitive" for f in static):
        notes.append("case-sensitive")
    for f in other:
        notes.append(f"{f.type}: {f.content}")

    return {
        "id": challenge.id,
        "name": challenge.name,
        "category": challenge.category,
        "mode": mode,
        "label": MODE_LABELS.get(mode, mode),
        "guessed": guessed,
        "per_instance": mode in PER_INSTANCE,
        "answer": answer,
        # The page shows a quizset question by question, with what each letter
        # says; the CSV keeps the one string above. Empty for every other kind,
        # which the template then renders the way it always did.
        "answer_rows": answer_rows if any(r["n"] for r in answer_rows) else [],
        "notes": notes,
        "points": challenge.value,
        "solvers": solvers.get(challenge.id, 0),
    }


def _solver_counts():
    rows = (db.session.query(Solves.challenge_id, db.func.count(Solves.user_id))
            .group_by(Solves.challenge_id).all())
    return dict(rows)


# Steps that no longer belong to any part of the subject: an older version's
# steps, left behind by a re-sync that no longer names them. They keep their
# solves and their codes, so they are shown — at the end, under their own
# heading, never interleaved with the subject.
ORPHANS = _l("No longer part of the subject")


def group_for_sheet(challenges, documents, intro_first=True):
    """[(title, [challenge])] — the sheet's order, taken from the documents.

    Board `position` is not the order here, deliberately. A re-sync numbers the
    steps it imports, but a step the subject dropped keeps the number it had,
    and two steps then share a position. Walking the board by position puts one
    part's leftovers between another part's steps, and a sheet that starts a
    group whenever the heading changes shows the same part three times. That is
    a real instance, not a hypothesis (ctf-1000, September 2026).

    The documents map is what the participant's own pages are built from, so
    ordering by it makes the sheet read exactly like the workshop.

    Anything outside every document keeps its board order and goes to one end:
    before the first part if it sits before it (the introduction, which belongs
    to no part), otherwise to an `ORPHANS` group at the bottom.
    """
    by_id = {c.id: c for c in challenges}
    place = {cid: (di, i) for di, d in enumerate(documents)
             for i, cid in enumerate(d["challenge_ids"])}

    def pos(c):
        return (c.position is None, c.position or 0, c.id)

    first = min((pos(by_id[cid]) for cid in place if cid in by_id), default=None)
    lead, orphans = [], []
    for c in challenges:
        if c.id in place:
            continue
        if intro_first and first is not None and pos(c) < first:
            lead.append(c)
        else:
            orphans.append(c)

    groups = []
    if lead:
        # Its own heading, from the step itself: on every subject so far this is
        # the introduction, and it is titled after the subject.
        groups.append((lead[0].category or lead[0].name, lead))
    for doc in documents:
        rows = [by_id[cid] for cid in doc["challenge_ids"] if cid in by_id]
        if rows:
            groups.append((doc["title"], rows))
    if orphans:
        groups.append((ORPHANS, orphans))
    return groups


def _attendees(challenges, optional):
    """Everyone doing the workshop, and how far each of them got.

    Built from one pass over the solves table rather than a per-user query:
    a full session is a few thousand rows, and an instructor refreshing this
    page mid-workshop should not cost N round trips.

    Admins and hidden accounts are left out — an instructor account is a
    normal CTFd user (there is no third type), so it would otherwise sit in
    this table as if it were a participant.
    """
    # progress.counts, not a second copy of the rule: this column and the
    # ratio the participant reads on /workshop have to be the same number.
    counted = [c for c in challenges if _counts(c, optional)]
    total = len(counted)
    counted_ids = {c.id for c in counted}
    names = {c.id: c.name for c in challenges}
    # Prerequisites pointing at a hidden challenge can never be satisfied, and
    # would park everybody on "nothing available". page.py:_prerequisites drops
    # them for the same reason.
    prereqs = {c.id: set((c.requirements or {}).get("prerequisites", [])) & names.keys()
               for c in challenges}

    users = (Users.query.filter(Users.type != "admin", Users.hidden == False)  # noqa: E712
             .order_by(Users.name).all())

    solved_by, last = {}, {}
    for user_id, challenge_id, date in db.session.query(
            Solves.user_id, Solves.challenge_id, Solves.date).all():
        solved_by.setdefault(user_id, set()).add(challenge_id)
        previous = last.get(user_id, (None, None))[1]
        # `Solves.date` defaults to utcnow and is never NULL in practice, but a
        # row imported by hand could be, and `date > None` raises.
        if user_id not in last or (date and (previous is None or date > previous)):
            last[user_id] = (challenge_id, date)

    rows = []
    for user in users:
        solved = solved_by.get(user.id, set())
        done = len(solved & counted_ids)
        # Where they are stuck: the first step in board order that is unlocked,
        # counts, and is not solved. That is the step to walk over to, which is
        # the question this column exists to answer.
        current = next((c for c in counted
                        if c.id not in solved
                        and prereqs[c.id].issubset(solved)), None)
        last_id, last_at = last.get(user.id, (None, None))
        rows.append({
            "id": user.id,
            "name": user.name,
            "banned": user.banned,
            "done": done,
            "total": total,
            "percent": round(100 * done / total) if total else 0,
            "current": current.name if current else None,
            "finished": total and done == total,
            "last_step": names.get(last_id),
            "last_at": last_at,
        })
    # Furthest along first is the wrong order for this page: the instructor is
    # looking for whoever is behind, so the least progress leads.
    rows.sort(key=lambda r: (r["percent"], r["done"]))
    return rows, total


def collect():
    challenges = _ordered_challenges()
    flags = _flags_by_challenge()
    modes = _validation_modes()
    token_ids = _token_ids()
    solvers = _solver_counts()
    optional = _optional_ids()

    parts = []
    for title, members in group_for_sheet(challenges, _documents()):
        rows = []
        for challenge in members:
            row = _row(challenge, flags.get(challenge.id, []), modes, token_ids, solvers)
            row["optional"] = challenge.id in optional
            rows.append(row)
        parts.append({"title": title, "rows": rows})

    attendees, required = _attendees(challenges, optional)
    answered = [r for p in parts for r in p["rows"] if r["answer"]]
    return {
        "parts": parts,
        "attendees": attendees,
        "recorded": bool(modes),
        "guessed": any(r["guessed"] for p in parts for r in p["rows"]),
        "per_instance": any(r["per_instance"] for p in parts for r in p["rows"]),
        # Which usage this instance is set to (PLAN.md §25): in self-serve the
        # codes below are stored but not asked for, and saying so here is what
        # stops somebody reading one out to a room that does not need it.
        "instance_mode": current_mode(),
        "instance_mode_label": MODE_NAMES[current_mode()],
        "self_serve": is_self_serve(),
        "totals": {
            "steps": sum(len(p["rows"]) for p in parts),
            "answers": len(answered),
            "required": required,
            "attendees": len(attendees),
            "finished": sum(1 for a in attendees if a["finished"]),
            "started": sum(1 for a in attendees if a["done"]),
        },
    }


@workshop_answers.route("/admin/workshop/answers")
@admins_only
def sheet():
    return render_template("workshop_answers.html", **collect())


@workshop_answers.route("/admin/workshop/answers.csv")
@admins_only
def sheet_csv():
    data = collect()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["part", "step", "kind", "answer", "notes", "points", "optional",
                "solvers"])
    for part in data["parts"]:
        for row in part["rows"]:
            w.writerow([part["title"], row["name"], row["label"], row["answer"],
                        "; ".join(row["notes"]), row["points"],
                        "yes" if row["optional"] else "", row["solvers"]])
    return Response(
        out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=workshop-answers.csv"})


def load_answers(app):
    app.register_blueprint(workshop_answers)
