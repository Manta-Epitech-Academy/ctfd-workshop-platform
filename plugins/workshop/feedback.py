"""The feedback report (PLAN.md §17) — what the participants actually said.

Ratings land in CTFd's own `Ratings` table from two places (plugins/workshop/
quiz.py): the closing step of each part, where the rating *is* the submission,
and the quiet inline control on every completed step. Nothing read them until
now.

Two populations, deliberately kept apart:

  verdicts     ratings on `quiz_type = 'rating'` steps — "did this part land?"
  step quality ratings on everything else — "which exercise is badly written?"

Averaging them would answer neither. Every row also carries how many people
*could* have rated it, because two 👎 out of three raters is a crisis and two
out of forty is noise.

Anonymous by design: the aggregate and the comments carry no names. `user_id`
is still on the row for anyone who needs it later — this is a choice about the
report, not about the data.
"""
import csv
import io

from flask import Blueprint, Response, render_template

from CTFd.models import Challenges, Ratings, Solves, db
from CTFd.utils import get_config
from CTFd.utils.decorators import admins_only

from .page import _documents, _final_step_id

workshop_feedback = Blueprint("workshop_feedback", __name__,
                              template_folder="templates")


def _solver_counts():
    """How many people solved each step — the population that could rate it."""
    rows = (db.session.query(Solves.challenge_id, db.func.count(Solves.user_id))
            .group_by(Solves.challenge_id).all())
    return dict(rows)


def _aggregate(rows):
    """(up, down, comments) for a list of Ratings."""
    up = sum(1 for r in rows if (r.value or 0) > 0)
    down = sum(1 for r in rows if (r.value or 0) < 0)
    comments = [{"value": r.value, "text": r.review.strip(), "date": r.date}
                for r in rows if (r.review or "").strip()]
    comments.sort(key=lambda c: c["date"] or 0, reverse=True)
    return up, down, comments


def _row(challenge, rows, solvers):
    up, down, comments = _aggregate(rows)
    rated = up + down
    return {
        "id": challenge.id,
        "name": challenge.name,
        "category": challenge.category,
        "position": challenge.position or 0,
        "up": up,
        "down": down,
        "rated": rated,
        "solvers": solvers,
        # "3 of 12 rated it" is the number that decides whether a score means
        # anything, so it is computed here rather than left to the reader.
        "coverage": round(100 * rated / solvers) if solvers else None,
        "comments": comments,
    }


def _part_titles():
    """challenge id -> the part it belongs to, from the sync's own document map.

    A closing step is called "Fin de la partie 2", which names the step and not
    the part. The question the report answers is which *part* lost people, so
    the row is titled with the part.
    """
    return {cid: d["title"] for d in _documents() for cid in d["challenge_ids"]}


def collect():
    """Everything the report shows, in one pass over the ratings."""
    challenges = {c.id: c for c in Challenges.query.all()}
    part_of = _part_titles()
    solvers = _solver_counts()
    by_challenge = {}
    for r in Ratings.query.all():
        by_challenge.setdefault(r.challenge_id, []).append(r)

    final_id = _final_step_id()
    verdicts, steps, unrated = [], [], []
    for cid, challenge in challenges.items():
        if challenge.state == "hidden":
            continue
        rows = by_challenge.get(cid, [])
        row = _row(challenge, rows, solvers.get(cid, 0))
        if getattr(challenge, "quiz_type", None) == "rating":
            row["final"] = cid == final_id
            row["scope"] = part_of.get(cid) or challenge.name
            verdicts.append(row)
        elif rows:
            steps.append(row)
        elif solvers.get(cid, 0):
            # Solved by somebody and rated by nobody: unmeasured, not good.
            unrated.append(row)

    verdicts.sort(key=lambda r: r["position"])
    # Worst first: this page is a working list of what to rewrite, so the step
    # with the most thumbs down leads, and a tie is broken by how many people
    # weighed in.
    steps.sort(key=lambda r: (-r["down"], -r["rated"], r["position"]))
    unrated.sort(key=lambda r: r["position"])

    final = next((v for v in verdicts if v.get("final")), None)
    parts = [v for v in verdicts if not v.get("final")]
    return {
        "final": final,
        "parts": parts,
        "steps": steps,
        "unrated": unrated,
        "ratings_disabled": get_config("challenge_ratings", default="public") == "disabled",
        "totals": {
            "ratings": sum(len(v) for v in by_challenge.values()),
            "comments": sum(len(r["comments"]) for r in verdicts + steps),
            "participants": db.session.query(Ratings.user_id).distinct().count(),
        },
    }


@workshop_feedback.route("/admin/workshop/feedback")
@admins_only
def report():
    return render_template("workshop_feedback.html", **collect())


@workshop_feedback.route("/admin/workshop/feedback.csv")
@admins_only
def report_csv():
    data = collect()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["kind", "step", "part", "up", "down", "rated", "solvers",
                "coverage_percent", "comment"])
    groups = ([("workshop", data["final"])] if data["final"] else [])
    groups += [("part", r) for r in data["parts"]]
    groups += [("step", r) for r in data["steps"] + data["unrated"]]
    for kind, row in groups:
        if not row:
            continue
        # A verdict row belongs to a part, not to the closing step's own
        # category — same reasoning as the page.
        base = [kind, row["name"], row.get("scope") or row["category"],
                row["up"], row["down"], row["rated"], row["solvers"],
                row["coverage"]]
        if row["comments"]:
            for c in row["comments"]:
                w.writerow(base + [c["text"]])
        else:
            w.writerow(base + [""])
    return Response(
        out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=workshop-feedback.csv"})


def load_feedback(app):
    app.register_blueprint(workshop_feedback)
