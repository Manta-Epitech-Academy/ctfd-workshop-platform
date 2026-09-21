"""What participants typed, step by step (PLAN.md §32).

CTFd's own /admin/submissions is the same list with two things a supervisor
must not have: the admin nav around it and the delete controls on it, both
wired to admin-only APIs. This is the list without them — the same order,
the same page size, the same two filters plus one on the step — and it is
read-only by construction: there is no route to delete from.

Participants only. A supervisor who walks the subject to see what a step
looks like, or an admin testing it, is not in this list, for the same
reason neither is in the answer sheet's "who is where".
"""
from flask import Blueprint, render_template, request, url_for

from CTFd.models import Challenges, Submissions, Users

from .links import documents as _documents
from .progress import ordered_challenges as _ordered_challenges
from .staff import staff_base, staff_only

workshop_submissions = Blueprint("workshop_submissions", __name__,
                                 template_folder="templates")

PER_PAGE = 50
KINDS = ("correct", "incorrect")
# What a submission looks like on the page. A quizset answer is a JSON blob
# and a free-text one can be a paragraph; the cell shows this much and the
# whole thing on hover.
SHOWN = 160


def _rows(items, part_of):
    rows = []
    for s in items:
        provided = s.provided or ""
        rows.append({
            "id": s.id,
            "date": s.date,
            "user_id": s.user_id,
            "user": s.user.name if s.user else "?",
            "challenge_id": s.challenge_id,
            "step": s.challenge.name if s.challenge else "?",
            "part": part_of.get(s.challenge_id),
            "kind": s.type,
            "provided": provided[:SHOWN],
            "truncated": len(provided) > SHOWN,
            "full": provided,
        })
    return rows


@workshop_submissions.route("/admin/workshop/submissions")
@staff_only
def listing():
    kind = request.args.get("type")
    kind = kind if kind in KINDS else None
    q = (request.args.get("q") or "").strip()
    step = request.args.get("step", type=int)
    page = max(1, request.args.get("page", 1, type=int))

    query = (Submissions.query
             .join(Users, Users.id == Submissions.user_id)
             .join(Challenges, Challenges.id == Submissions.challenge_id)
             .filter(Users.type != "admin", Users.hidden == False))  # noqa: E712
    if kind:
        query = query.filter(Submissions.type == kind)
    if step:
        query = query.filter(Submissions.challenge_id == step)
    if q:
        query = query.filter(Users.name.ilike(f"%{q}%"))
    pagination = (query.order_by(Submissions.date.desc())
                  .paginate(page=page, per_page=PER_PAGE, error_out=False))

    part_of = {cid: d["title"] for d in _documents() for cid in d["challenge_ids"]}
    args = {k: v for k, v in (("type", kind), ("q", q), ("step", step)) if v}
    return render_template(
        "workshop_submissions.html",
        base_template=staff_base(),
        rows=_rows(pagination.items, part_of),
        total=pagination.total,
        page=page,
        pages=pagination.pages,
        prev_page=(url_for("workshop_submissions.listing", page=pagination.prev_num, **args)
                   if pagination.has_prev else None),
        next_page=(url_for("workshop_submissions.listing", page=pagination.next_num, **args)
                   if pagination.has_next else None),
        kind=kind, q=q, step=step,
        steps=[(c.id, c.name) for c in _ordered_challenges()],
    )


def load_submissions(app):
    app.register_blueprint(workshop_submissions)
