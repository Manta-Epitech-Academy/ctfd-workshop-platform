"""Supervisor notes: a few lines per step, for the adult in the room (PLAN.md §69).

A subject may carry `supervisor_notes.md`, encrypted like its answers
(`supervisor_notes.md.gpg`, convention §3.5c). Each note is filed under a step
id: what to look at first when a participant is stuck there. The notes are
optional, and a subject without them imports exactly as before.

**Where they are kept.** One row per step in `workshop_supervisor_note`, and
nowhere else. Not in the challenge's description, its hints or its topics:
every one of those has a participant route that reads it. This table has two
readers, the answer sheet (answers.py, staff-only) and the sync's write below
(admin-only).

**How they get here.** The sync, like the codes it reads back
(checkpoint.py), through a plugin route, since CTFd's API has no place for
them:

  POST /api/v1/workshop/notes  {"challenge_ids": [...], "notes": {id: markdown}}

`challenge_ids` is every step of the subject being imported. Their notes are
replaced by `notes`: a step absent from it loses its note. That is what makes
a note deleted from the file disappear on the next sync, and what keeps one
subject of a workshop from erasing another's. A sync that could not open the
file (only the `.gpg`, from the command line) does not call this at all, so
it leaves the notes as they were.

**Text and code only.** The linter refuses images, scenes, animations and
linked files in a note: anything uploaded is served to whoever has its URL.
The markdown is stored as written and rendered when the sheet is drawn,
sanitized like any page of the instance.
"""
from flask import Blueprint, jsonify, request
from markupsafe import Markup

from CTFd.models import Challenges, db
from CTFd.utils.config.pages import build_markdown
from CTFd.utils.decorators import admins_only

workshop_notes = Blueprint("workshop_notes", __name__)

# A note is a few paragraphs. The cap only stops a file pasted by mistake.
MAX_NOTE = 64 * 1024


class SupervisorNote(db.Model):
    """The note of one step, as the subject wrote it. See the module docstring."""
    __tablename__ = "workshop_supervisor_note"

    challenge_id = db.Column(db.Integer,
                             db.ForeignKey("challenges.id", ondelete="CASCADE"),
                             primary_key=True)
    body = db.Column(db.Text, nullable=False)


def notes_html(challenge_ids=None):
    """{challenge id: rendered note} — every note, or those of these steps."""
    query = SupervisorNote.query
    if challenge_ids is not None:
        query = query.filter(SupervisorNote.challenge_id.in_(list(challenge_ids)))
    return {n.challenge_id: Markup(build_markdown(n.body, sanitize=True))
            for n in query.all()}


def _refuse(reason):
    return jsonify({"success": False, "errors": {"": [reason]}}), 400


@workshop_notes.route("/api/v1/workshop/notes", methods=["POST"])
@admins_only
def replace():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _refuse("send a JSON object")
    ids, notes = body.get("challenge_ids"), body.get("notes")
    if (not isinstance(ids, list) or not isinstance(notes, dict)
            or not all(isinstance(i, int) for i in ids)):
        return _refuse("challenge_ids is a list of ids, notes an object")
    try:
        notes = {int(k): v for k, v in notes.items()}
    except (TypeError, ValueError):
        return _refuse("notes are keyed by challenge id")
    if not set(notes) <= set(ids):
        return _refuse("a note names a step outside challenge_ids")
    if not all(isinstance(v, str) and v.strip() and len(v) <= MAX_NOTE
               for v in notes.values()):
        return _refuse("a note is non-empty text, at most 64 KB")
    known = {cid for (cid,) in db.session.query(Challenges.id)
             .filter(Challenges.id.in_(ids)).all()} if ids else set()
    if set(notes) - known:
        return _refuse("a note names a step that does not exist")

    kept = 0
    for row in SupervisorNote.query.filter(SupervisorNote.challenge_id.in_(ids)).all():
        if row.challenge_id in notes:
            row.body = notes.pop(row.challenge_id)
            kept += 1
        else:
            db.session.delete(row)
    for cid, text in notes.items():
        db.session.add(SupervisorNote(challenge_id=cid, body=text))
    db.session.commit()
    return jsonify({"success": True,
                    "data": {"notes": kept + len(notes), "steps": len(ids)}})


def load_notes(app):
    app.register_blueprint(workshop_notes)
