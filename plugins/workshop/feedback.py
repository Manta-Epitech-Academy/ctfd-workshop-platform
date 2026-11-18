"""The feedback report (PLAN.md §17, §67): what the participants said.

What a participant says about a step is a pick from the platform's fixed
list, counted in `workshop_step_feedback` (choices.py). This page reads those
counts. It read CTFd's own `Ratings` table, thumbs and written comments,
until the list replaced them; the rows that table still holds on an older
instance are no longer shown here.

Two populations, deliberately kept apart:

  verdicts     the closing step of each part: "did this part land?"
  step quality every other step: "which exercise is badly written?"

Averaging them would answer neither. Every row also carries how many people
*could* have answered, because two "too hard" out of three is a crisis and
two out of forty is noise.

**The mood is derived, not asked.** `great` and `good` are the good signs
(choices.py `POSITIVE`); every other choice names a problem. One answer can
hold both, so the two numbers are picks, not people, and `answered` is the
number of people.

**The scope is a set of cohorts.** A count names no account, so it cannot be
filtered on `Users` the way the other reports are (scope.py). It is filed
under the cohort of whoever answered, so the page keeps the counts of the
cohorts the scoped participants are in. Admins and hidden accounts are not
in the counts at all: choices.py never writes theirs.
"""
import csv
import io

from flask import Blueprint, Response, render_template

from CTFd.models import Challenges, ChallengeTopics, Solves, Topics, Users, db

from . import choices
from .choices import StepFeedback
from .page import _documents, _final_step_id
from .scope import current_scope, participant_criteria, per_challenge
from .staff import staff_base, staff_only

workshop_feedback = Blueprint("workshop_feedback", __name__,
                              template_folder="templates")


def _cohorts(scope):
    """The cohort keys the scope covers, or None for the whole instance."""
    if scope.campus is None:
        return None
    ids = db.session.query(Users.id).filter(*participant_criteria(scope)).all()
    return {choices.cohort_key(uid) for (uid,) in ids}


def _counts(scope):
    """The count rows in scope."""
    rows = StepFeedback.query.all()
    cohorts = _cohorts(scope)
    if cohorts is not None:
        rows = [r for r in rows if r.cohort in cohorts]
    return rows


def _steps():
    """step key -> challenge id, the way choices.py files a step."""
    filed = dict(
        db.session.query(Topics.value, ChallengeTopics.challenge_id)
        .join(ChallengeTopics, ChallengeTopics.topic_id == Topics.id)
        .filter(Topics.value.like(choices.STEP_TOPIC + "%")).all())
    return {value[len(choices.STEP_TOPIC):][:191]: cid
            for value, cid in filed.items()}


def _challenge_of(row, steps):
    if row.step.startswith("#") and row.step[1:].isdigit():
        return int(row.step[1:])
    return steps.get(row.step)


def _row(challenge, picked, solvers):
    answered = picked.get(choices.ANSWERED, 0)
    positive = sum(picked.get(key, 0) for key in choices.POSITIVE)
    negative = sum(n for key, n in picked.items()
                   if key in choices.KEYS and key not in choices.POSITIVE)
    return {
        "id": challenge.id,
        "name": challenge.name,
        "category": challenge.category,
        "position": challenge.position or 0,
        "answered": answered,
        "positive": positive,
        "negative": negative,
        # Every choice somebody picked, in the list's own order.
        "picked": [(key, choices.LABELS[key], picked[key],
                    key in choices.POSITIVE)
                   for key in choices.KEYS if picked.get(key)],
        "counts": {key: picked.get(key, 0) for key in choices.KEYS},
        "solvers": solvers,
        # "3 of 12 answered" is the number that decides whether a count means
        # anything, so it is computed here rather than left to the reader.
        "coverage": round(100 * answered / solvers) if solvers else None,
    }


def _part_titles():
    """challenge id -> the part it belongs to, from the sync's own document map.

    A closing step is called "Fin de la partie 2", which names the step and not
    the part. The question the report answers is which *part* lost people, so
    the row is titled with the part.
    """
    return {cid: d["title"] for d in _documents() for cid in d["challenge_ids"]}


def collect():
    """Everything the report shows, in one pass over the counts."""
    scope = current_scope()
    challenges = {c.id: c for c in Challenges.query.all()}
    part_of = _part_titles()
    # How many people solved each step: the population that could answer.
    solvers = per_challenge(Solves, scope)
    steps_by_key = _steps()
    by_challenge = {}
    for r in _counts(scope):
        picked = by_challenge.setdefault(_challenge_of(r, steps_by_key), {})
        picked[r.choice] = picked.get(r.choice, 0) + r.count

    final_id = _final_step_id()
    verdicts, steps, silent = [], [], []
    for cid, challenge in challenges.items():
        if challenge.state == "hidden":
            continue
        picked = by_challenge.get(cid, {})
        row = _row(challenge, picked, solvers.get(cid, 0))
        if getattr(challenge, "quiz_type", None) == "rating":
            row["final"] = cid == final_id
            row["scope"] = part_of.get(cid) or challenge.name
            verdicts.append(row)
        elif row["answered"]:
            steps.append(row)
        elif solvers.get(cid, 0):
            # Solved by somebody and answered by nobody: unmeasured, not good.
            silent.append(row)

    verdicts.sort(key=lambda r: r["position"])
    # Worst first: this page is a working list of what to rewrite, so the step
    # with the most problems named leads, and a tie is broken by how many
    # people weighed in.
    steps.sort(key=lambda r: (-r["negative"], -r["answered"], r["position"]))
    silent.sort(key=lambda r: r["position"])

    final = next((v for v in verdicts if v.get("final")), None)
    parts = [v for v in verdicts if not v.get("final")]
    return {
        "final": final,
        "parts": parts,
        "steps": steps,
        "unrated": silent,
        "ratings_disabled": not choices.enabled(),
        "totals": {"answers": sum(r["answered"] for r in verdicts + steps)},
    }


@workshop_feedback.route("/admin/workshop/feedback")
@staff_only
def report():
    return render_template("workshop_feedback.html", base_template=staff_base(),
                           **collect())


@workshop_feedback.route("/admin/workshop/feedback.csv")
@staff_only
def report_csv():
    """The counts as they are filed: one line per step, cohort and version.

    The page adds the cohorts and the versions up; this is where they can be
    read apart.
    """
    scope = current_scope()
    challenges = {c.id: c for c in Challenges.query.all()}
    steps_by_key = _steps()
    final_id = _final_step_id()
    lines = {}
    for r in _counts(scope):
        challenge = challenges.get(_challenge_of(r, steps_by_key))
        if challenge is None or challenge.state == "hidden":
            continue
        line = lines.setdefault(
            (challenge.position or 0, challenge.id, r.cohort, r.version),
            {"challenge": challenge, "content": r.content})
        line[r.choice] = r.count
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["kind", "step", "part", "content", "step_slug", "cohort",
                "version", "answered"] + list(choices.KEYS))
    slug_of = {cid: key for key, cid in steps_by_key.items()}
    for (_position, cid, cohort, version), line in sorted(
            lines.items(), key=lambda item: item[0]):
        challenge = line["challenge"]
        if getattr(challenge, "quiz_type", None) == "rating":
            kind = "workshop" if cid == final_id else "part"
        else:
            kind = "step"
        w.writerow([kind, challenge.name, challenge.category, line["content"],
                    slug_of.get(cid, "#%d" % cid), cohort, version,
                    line.get(choices.ANSWERED, 0)]
                   + [line.get(key, 0) for key in choices.KEYS])
    return Response(
        out.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=workshop-feedback.csv"})


def load_feedback(app):
    app.register_blueprint(workshop_feedback)
