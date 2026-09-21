"""Workshop statistics, for whoever is running the room (PLAN.md §32).

Not a copy of CTFd's /admin/statistics. That page counts a CTF — points,
wrong keys, distinct IPs — and none of those is the question a supervisor
walks over to a screen with. The questions are: how many people are here,
how far has the room got, which step is eating the session, and where is
everybody stuck right now. Every number here comes from the same readers the
answer sheet and the participant's own page use (progress.py, answers.py),
so the three can never disagree.

Read-only, and the first page a supervisor lands on after joining.
"""
from collections import Counter

from flask import Blueprint, render_template

from CTFd.models import Fails, Solves, Users, db

from .answers import _attendees, group_for_sheet
from .links import documents as _documents
from .progress import counts as _counts
from .progress import optional_ids as _optional_ids
from .progress import ordered_challenges as _ordered_challenges
from .staff import staff_base, staff_only

workshop_stats = Blueprint("workshop_stats", __name__, template_folder="templates")


def _per_challenge(model):
    """challenge id -> how many rows of `model` participants produced.

    Joined on the account so a supervisor's own attempts, or an admin's, are
    not in the room's numbers — the same exclusion `_attendees` applies.
    """
    rows = (db.session.query(model.challenge_id, db.func.count(model.id))
            .join(Users, Users.id == model.user_id)
            .filter(Users.type != "admin", Users.hidden == False)  # noqa: E712
            .group_by(model.challenge_id).all())
    return dict(rows)


def collect():
    challenges = _ordered_challenges()
    optional = _optional_ids()
    attendees, required = _attendees(challenges, optional)
    solves = _per_challenge(Solves)
    fails = _per_challenge(Fails)
    # Where people are right now: the step each unfinished participant is
    # parked on, counted. This is the column to read when deciding where to
    # stand.
    stuck = Counter(a["current_id"] for a in attendees
                    if a["current_id"] is not None and not a["finished"])
    people = len(attendees)

    parts = []
    for title, members in group_for_sheet(challenges, _documents()):
        rows = []
        for c in members:
            solved = solves.get(c.id, 0)
            failed = fails.get(c.id, 0)
            rows.append({
                "id": c.id,
                "name": c.name,
                "kind": getattr(c, "quiz_type", None) or c.type,
                "optional": c.id in optional,
                "counts": _counts(c, optional),
                "solved": solved,
                "failed": failed,
                "percent": round(100 * solved / people) if people else 0,
                # Of everything typed at this step, how much was wrong. A
                # step with a high one is the step to rewrite.
                "wrong_rate": (round(100 * failed / (solved + failed))
                               if solved + failed else None),
                "stuck": stuck.get(c.id, 0),
            })
        parts.append({"title": title, "rows": rows})

    # How many people have done exactly n steps — the shape of the room.
    spread = Counter(a["done"] for a in attendees)
    distribution = [{"done": n, "people": spread.get(n, 0)}
                    for n in range(required + 1)]
    return {
        "parts": parts,
        "distribution": distribution,
        "totals": {
            "participants": people,
            "started": sum(1 for a in attendees if a["done"]),
            "finished": sum(1 for a in attendees if a["finished"]),
            "required": required,
            "solves": sum(solves.values()),
            "fails": sum(fails.values()),
        },
    }


@workshop_stats.route("/admin/workshop/stats")
@staff_only
def stats():
    return render_template("workshop_stats.html", base_template=staff_base(),
                           **collect())


def load_stats(app):
    app.register_blueprint(workshop_stats)
