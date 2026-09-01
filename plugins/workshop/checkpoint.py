"""Checkpoint steps: the read-back and the in-place migration (PLAN.md §25.5).

A checkpoint step used to be a `standard` challenge with a static `Flags` row
holding the code the instructor reads out. It is now a `quiz` challenge of kind
`checkpoint`, because deciding *whether the code is required* is a workshop
rule and only plugin code gets to run at submit time (see quiz.py).

That leaves two things the importer cannot do over CTFd's own API, and both
live here because the plugin runs inside CTFd and has the database:

  GET  /api/v1/workshop/checkpoints          {challenge_id: code}
  POST /api/v1/workshop/checkpoints/migrate  standard -> quiz, in place

**Why a read-back endpoint.** `quiz_answers` is deliberately absent from
`QuizChallenge.read()`, so once the code lives there the sync can no longer see
it — and the sync must see it, or it would mint a new code on every run and
invalidate every sheet already handed out.

**Why the migration is not a delete-and-recreate.** Solves, points, hints,
prerequisites and the answer sheet all key on the challenge id. Recreating the
challenge would give it a new one and take every solve on the instance with it.
So the row is edited in place: the child row is inserted, the discriminator is
flipped, and the now-unused flag is dropped. The id never moves.

Both routes are admin-only. The GET hands out answers, which is exactly what
the answer sheet already does for an admin (§23), and nothing a participant can
reach ever returns a code.
"""
import json

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from CTFd.models import Flags, db
from CTFd.utils.decorators import admins_only

workshop_checkpoint = Blueprint("workshop_checkpoint", __name__)

CHECKPOINT = "checkpoint"
# The quiz child table, which is where a checkpoint code lives after migration.
# Raw SQL rather than the ORM for the migration itself: SQLAlchemy maps the
# `type` column as the polymorphic discriminator, and rewriting it under a
# loaded object is exactly the operation the mapper is designed to prevent.
CHILD_TABLE = "quiz_challenge_model"


def _code_of(answers):
    if isinstance(answers, dict):
        answers = answers.get("code")
    return str(answers).strip() if answers else ""


def checkpoint_codes():
    """{challenge_id: code} for every checkpoint step on this instance."""
    rows = db.session.execute(text(
        f"SELECT id, quiz_answers FROM {CHILD_TABLE} WHERE quiz_type = :kind"
    ), {"kind": CHECKPOINT}).fetchall()
    out = {}
    for cid, raw in rows:
        try:
            answers = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            answers = None
        code = _code_of(answers)
        if code:
            out[int(cid)] = code
    return out


def migrate_to_checkpoint(challenge_ids):
    """Turn `standard` challenges into `checkpoint` quiz challenges, in place.

    Idempotent, and conservative: a challenge already typed `quiz` is left
    alone (its kind and its code are whatever the sync last PATCHed), and an
    unknown id is reported rather than invented.
    """
    result = {"migrated": [], "already": [], "missing": [], "skipped": []}
    for cid in challenge_ids:
        try:
            cid = int(cid)
        except (TypeError, ValueError):
            result["skipped"].append(cid)
            continue
        row = db.session.execute(
            text("SELECT id, type FROM challenges WHERE id = :id"), {"id": cid}
        ).fetchone()
        if row is None:
            result["missing"].append(cid)
            continue
        if row[1] == "quiz":
            result["already"].append(cid)
            continue
        if row[1] != "standard":
            # A challenge type this plugin did not create — dynamic, or
            # something a later phase adds. Converting it would lose whatever
            # its own child row holds.
            result["skipped"].append(cid)
            continue

        # The code the instructor already reads out, carried over rather than
        # regenerated: a sheet handed out this morning has to keep working.
        flag = Flags.query.filter_by(challenge_id=cid, type="static").first()
        answers = json.dumps({"code": flag.content} if flag and flag.content else {})

        child = db.session.execute(
            text(f"SELECT id FROM {CHILD_TABLE} WHERE id = :id"), {"id": cid}
        ).fetchone()
        if child is None:
            db.session.execute(text(
                f"INSERT INTO {CHILD_TABLE} (id, quiz_type, quiz_spec, quiz_answers) "
                f"VALUES (:id, :kind, NULL, :answers)"
            ), {"id": cid, "kind": CHECKPOINT, "answers": answers})
        else:
            db.session.execute(text(
                f"UPDATE {CHILD_TABLE} SET quiz_type = :kind, quiz_answers = :answers "
                f"WHERE id = :id"
            ), {"id": cid, "kind": CHECKPOINT, "answers": answers})
        db.session.execute(
            text("UPDATE challenges SET type = 'quiz' WHERE id = :id"), {"id": cid})
        # The flag is now a second copy of the answer, in the one place a
        # participant's submission no longer reaches. Two sources of truth for
        # the same code is how they drift.
        Flags.query.filter_by(challenge_id=cid).delete()
        result["migrated"].append(cid)

    db.session.commit()
    # Objects loaded before the discriminator moved are stale by definition.
    db.session.expire_all()
    return result


@workshop_checkpoint.route("/api/v1/workshop/checkpoints", methods=["GET"])
@admins_only
def read_codes():
    return jsonify({"success": True,
                    "data": {str(k): v for k, v in checkpoint_codes().items()}})


@workshop_checkpoint.route("/api/v1/workshop/checkpoints/migrate", methods=["POST"])
@admins_only
def migrate():
    payload = request.get_json(silent=True) or {}
    ids = payload.get("challenge_ids") or []
    if not isinstance(ids, list):
        return jsonify({"success": False, "errors": ["challenge_ids must be a list"]}), 400
    return jsonify({"success": True, "data": migrate_to_checkpoint(ids)})


def load_checkpoint(app):
    app.register_blueprint(workshop_checkpoint)
