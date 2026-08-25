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

from .page import NON_TASK_KINDS, _documents, _optional_ids, _ordered_challenges

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


def _validation_modes():
    """challenge id -> the mode the content asked for, as the sync recorded it.

    Empty on an instance synced before `workshop_validation` existed, which is
    why every caller falls back to _infer_mode rather than trusting this.
    """
    raw = get_config("workshop_validation")
    if not raw:
        return {}
    try:
        modes = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return {int(k): v for k, v in modes.items() if str(k).isdigit()}


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


def _quiz_answer(challenge):
    """The correct answer, rendered the way the content author wrote it.

    Shapes are the tolerant ones quiz.py grades (bare values accepted), so
    each branch handles both the dict form and the shorthand.
    """
    kind = getattr(challenge, "quiz_type", None)
    answers = getattr(challenge, "quiz_answers", None)
    if kind in NO_ANSWER or answers is None:
        return ""
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
        answer = _quiz_answer(challenge)
    else:
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
        "notes": notes,
        "points": challenge.value,
        "solvers": solvers.get(challenge.id, 0),
    }


def _solver_counts():
    rows = (db.session.query(Solves.challenge_id, db.func.count(Solves.user_id))
            .group_by(Solves.challenge_id).all())
    return dict(rows)


def _part_titles():
    """challenge id -> the part it belongs to, from the sync's document map.

    Same reasoning as the feedback report: `category` is the chapter heading,
    but a closing step belongs to the part it closes.
    """
    return {cid: d["title"] for d in _documents() for cid in d["challenge_ids"]}


def _attendees(challenges, optional):
    """Everyone doing the workshop, and how far each of them got.

    Built from one pass over the solves table rather than a per-user query:
    a full session is a few thousand rows, and an instructor refreshing this
    page mid-workshop should not cost N round trips.

    Admins and hidden accounts are left out — an instructor account is a
    normal CTFd user (there is no third type), so it would otherwise sit in
    this table as if it were a participant.
    """
    counted = [c for c in challenges
               if getattr(c, "quiz_type", None) not in NON_TASK_KINDS
               and c.id not in optional]
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
    part_of = _part_titles()

    parts, seen = [], None
    for challenge in challenges:
        row = _row(challenge, flags.get(challenge.id, []), modes, token_ids, solvers)
        row["optional"] = challenge.id in optional
        title = part_of.get(challenge.id) or challenge.category or "Steps"
        if title != seen:
            parts.append({"title": title, "rows": []})
            seen = title
        parts[-1]["rows"].append(row)

    attendees, required = _attendees(challenges, optional)
    answered = [r for p in parts for r in p["rows"] if r["answer"]]
    return {
        "parts": parts,
        "attendees": attendees,
        "recorded": bool(modes),
        "guessed": any(r["guessed"] for p in parts for r in p["rows"]),
        "per_instance": any(r["per_instance"] for p in parts for r in p["rows"]),
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
