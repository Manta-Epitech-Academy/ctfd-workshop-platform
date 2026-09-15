"""Quiz challenge type (PLAN.md §12, docs/CONTENT_CONVENTION.md §3.7).

Four graded kinds, plus three that are graded by something other than an answer:
  checkpoint  the work itself is the answer, and somebody has to say it is done.
        In an instructor-led instance that somebody is the instructor, who
        reads out the per-exercise code this challenge stores; in a self-serve
        one there is nobody to ask, so the button is the completion — exactly
        `ack` (PLAN.md §25.4). One kind rather than two, because the mode is a
        property of the instance and can be flipped after a session: the code
        stays stored either way, unrevealed, so the flip works in both
        directions.
  ack   a reading acknowledgement — a lone Submit button under the text, so
        finishing it is a deliberate action. Used for a subject's intro, which
        gates the rest. A quiz kind rather than a static flag on purpose: a
        "dumb" flag would have to be embedded in the page for the button to
        send it, which reads like a leak and invites copy-paste.
  info  a closing note. No control at all, and it counts for nothing.
  rating the closing step of a subject: how did the workshop go? **The rating
        IS the submission** — grading it stores the rating, so the step is
        solved at the exact moment the feedback lands, never before. It counts
        in the progress bar like any other step, so the workshop reads 14/15
        until it is rated; reaching 100% without leaving feedback is
        impossible, which is the whole point.
        (CTFd's own `PUT /challenges/<id>/ratings` cannot do this: it refuses
        a rating for an unsolved challenge, so going through it would mean
        solving first — reaching 100% and only then, maybe, recording an
        opinion. We write the same `Ratings` row from here instead, so the
        data lands in CTFd's native table either way.)
        Submission: `{"value": 1|-1, "review": "…"}`, or a bare "1"/"-1".

Four kinds, all auto-graded server-side in attempt():
  single    one correct letter            submission "B"
  multiple  set of letters                submission "A,D" (order-free)
  match     uppercase→lowercase pairs     submission "A-c,B-a" (order-free, ":" ok)
  freeform  regex (or list, any match)    submission is free text

`quiz_answers` never reaches the client: read() only exposes `quiz_type`.
Answers column shapes (tolerant — bare values accepted):
  checkpoint {"code": "4b279a"}                   or "4b279a"
  single    {"answer": "B"}                       or "B"
  multiple  {"answers": ["A", "D"]}               or ["A", "D"]
  match     {"pairs": {"A": "c", "B": "a"}}       or {"A": "c", ...}
  freeform  {"patterns": ["^ritchie$"], "case_sensitive": false} or ["^ritchie$"]
"""
import json
import re

from flask import Blueprint

from flask_babel import gettext as _

from CTFd.exceptions.challenges import (
    ChallengeCreateException,
    ChallengeUpdateException,
)
from CTFd.models import Challenges, Ratings, db
from CTFd.plugins.challenges import BaseChallenge
from CTFd.utils.user import get_current_user

from .jumpqueue import enqueue_solve
from .mode import is_self_serve


class QuizChallengeModel(Challenges):
    __mapper_args__ = {"polymorphic_identity": "quiz"}
    id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True
    )
    quiz_type = db.Column(db.String(16), default="single")
    # Structured question/items for the SPA; the description carries the
    # rendered question for stock CTFd themes.
    quiz_spec = db.Column(db.JSON)
    # Correct answers. Excluded from read(); never serialized to the client.
    quiz_answers = db.Column(db.JSON)


def _grade_single(answers, submission):
    correct = answers.get("answer") if isinstance(answers, dict) else answers
    return submission.strip().upper() == str(correct).strip().upper()


def _grade_multiple(answers, submission):
    correct = answers.get("answers") if isinstance(answers, dict) else answers
    expected = {str(a).strip().upper() for a in correct}
    given = {p.strip().upper() for p in submission.split(",") if p.strip()}
    return given == expected


def _grade_match(answers, submission):
    pairs = answers.get("pairs") if isinstance(answers, dict) and "pairs" in answers else answers
    expected = {str(k).strip().upper(): str(v).strip().lower() for k, v in pairs.items()}
    given = {}
    for part in re.split(r"[,;]", submission):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"([A-Za-z])\s*[-:]\s*([A-Za-z])", part)
        if not m:
            return False
        given[m.group(1).upper()] = m.group(2).lower()
    return given == expected


def _grade_freeform(answers, submission):
    if isinstance(answers, dict):
        patterns = answers.get("patterns", [])
        case_sensitive = answers.get("case_sensitive", False)
    else:
        patterns = answers if isinstance(answers, list) else [answers]
        case_sensitive = False
    flags = 0 if case_sensitive else re.IGNORECASE
    text = submission.strip()
    return any(re.search(p, text, flags) for p in patterns)


def _store_rating(challenge, submission):
    """Persist the feedback carried by the submission; solving is its effect.

    Written straight into CTFd's own `Ratings` table so the data lives where
    every other rating lives — the API's "must be solved first" rule is a
    property of that endpoint, not of the model, and obeying it here would
    invert the order we need.
    """
    value, review = None, ""
    text = (submission or "").strip()
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        payload = None
    if isinstance(payload, dict):
        # What the UI sends: the thumb plus an optional written reason.
        value = payload.get("value")
        review = (payload.get("review") or "")[:2000]
    else:
        # Anything else is read as the thumb alone. `json.loads("1")` returns an
        # int, not a dict, so a bare value has to be handled here or it reaches
        # `.get` and 500s — which is what a script posting "1" used to get.
        value = text
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = None
    if value not in (1, -1):
        # No rating, no solve — this is what keeps the progress bar honest.
        return False, _("Pick 👍 or 👎 first")

    user = get_current_user()
    rating = Ratings.query.filter_by(user_id=user.id, challenge_id=challenge.id).first()
    if rating:
        rating.value, rating.review = value, review
    else:
        db.session.add(Ratings(user_id=user.id, challenge_id=challenge.id,
                               value=value, review=review))
    db.session.commit()
    return True, _("Thanks for the feedback!")


def _checkpoint_code(answers):
    """The instructor's code for this step, whatever shape it was stored in."""
    if isinstance(answers, dict):
        answers = answers.get("code")
    return str(answers).strip() if answers else ""


def _grade_checkpoint(challenge, submission):
    """Who says this exercise is done — the instructor, or the participant.

    Self-serve accepts the button and never looks at the code, which stays in
    the database so that switching the instance back to instructor-led works
    (PLAN.md §25.4). Instructor-led compares the code, case-insensitively, the
    way the static flag it replaces did.
    """
    if is_self_serve():
        return True, _("Noted")
    code = _checkpoint_code(challenge.quiz_answers)
    if not code:
        return False, _("Misconfigured checkpoint, please report it")
    if submission.strip().lower() == code.lower():
        return True, _("Correct")
    return False, _("Incorrect")


GRADERS = {
    "single": _grade_single,
    "multiple": _grade_multiple,
    "match": _grade_match,
    "freeform": _grade_freeform,
}


class QuizChallenge(BaseChallenge):
    id = "quiz"
    name = "quiz"
    templates = {
        "create": "/plugins/workshop/assets/create.html",
        "update": "/plugins/workshop/assets/update.html",
        # Participant view renders real controls (radios/checkboxes/selects)
        # from quiz_spec; see assets/view.html.
        "view": "/plugins/workshop/assets/view.html",
    }
    scripts = {
        "create": "/plugins/workshop/assets/create.js",
        "update": "/plugins/workshop/assets/update.js",
        # Core submit logic works untouched: view.html mirrors the composed
        # submission string into the hidden #challenge-input it reads.
        "view": "/plugins/challenges/assets/view.js",
    }
    route = "/plugins/workshop/assets/"
    blueprint = Blueprint("workshop", __name__, static_folder="assets")
    challenge_model = QuizChallengeModel

    @staticmethod
    def _parse_json_fields(data, exception):
        """The admin form posts quiz_answers/quiz_spec as strings; the API
        sends JSON objects. Normalize to objects either way."""
        for field in ("quiz_answers", "quiz_spec"):
            value = data.get(field)
            if isinstance(value, str) and value.strip():
                try:
                    data[field] = json.loads(value)
                except ValueError:
                    raise exception(f"'{field}' is not valid JSON")
            elif isinstance(value, str):
                data[field] = None
        return data

    @classmethod
    def create(cls, request):
        data = request.form or request.get_json()
        data = dict(data)
        cls._parse_json_fields(data, ChallengeCreateException)
        challenge = cls.challenge_model(**data)
        db.session.add(challenge)
        db.session.commit()
        return challenge

    @classmethod
    def update(cls, challenge, request):
        data = request.form or request.get_json()
        data = dict(data)
        cls._parse_json_fields(data, ChallengeUpdateException)
        for attr, value in data.items():
            setattr(challenge, attr, value)
        db.session.commit()
        return challenge

    @classmethod
    def read(cls, challenge):
        challenge = QuizChallengeModel.query.filter_by(id=challenge.id).first()
        data = super().read(challenge)
        data.update({"quiz_type": challenge.quiz_type, "quiz_spec": challenge.quiz_spec})
        return data  # quiz_answers deliberately absent

    @classmethod
    def solve(cls, user, team, challenge, request):
        """Record the solve, then queue it for Jump. Two statements, no network.

        The order matters: `super().solve` is what writes the `Solves` row and
        raises `ChallengeSolveException` on a duplicate, which the API turns
        into "already solved" — so queuing after it means a duplicate submit
        never queues anything.

        The queue write is wrapped because a participant's solve must never
        fail over a reporting problem. What is lost is a row the pull
        reconciler will pick up; what would be lost otherwise is the student's
        work, which is not a trade this feature is allowed to make.
        """
        super().solve(user, team, challenge, request)
        try:
            enqueue_solve(user.id, challenge.id)
        except Exception:  # noqa: BLE001
            db.session.rollback()

    @classmethod
    def attempt(cls, challenge, request):
        challenge = QuizChallengeModel.query.filter_by(id=challenge.id).first()
        data = request.form or request.get_json()
        submission = str(data.get("submission", ""))
        if challenge.quiz_type == "checkpoint":
            return _grade_checkpoint(challenge, submission)
        if challenge.quiz_type == "ack":
            # Pressing the button IS the completion — there is nothing to grade.
            return True, _("Noted")
        if challenge.quiz_type == "rating":
            return _store_rating(challenge, submission)
        if challenge.quiz_type == "info":
            return False, _("This step is just something to read")
        grader = GRADERS.get(challenge.quiz_type)
        if grader is None or challenge.quiz_answers is None:
            return False, _("Misconfigured quiz, please report it")
        try:
            if grader(challenge.quiz_answers, submission):
                return True, _("Correct")
        except (AttributeError, TypeError, re.error):
            return False, _("Misconfigured quiz, please report it")
        return False, _("Incorrect")
