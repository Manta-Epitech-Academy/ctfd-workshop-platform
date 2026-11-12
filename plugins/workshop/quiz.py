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
  rating the closing step of a part: how did it go? **The answer IS the
        submission**: one choice or more from the platform's fixed list
        (choices.py, PLAN.md §67), so the step is solved at the exact moment
        the feedback lands, never before. It counts in the progress bar like
        any other step, so the workshop reads 14/15 until it is answered;
        reaching 100% without leaving feedback is impossible, which is the
        whole point.
        The choices are counted, not kept: the solve's `provided` is `-`.
        Submission: the keys, comma-separated: `good,too_hard`.

Four kinds, all auto-graded server-side in attempt():
  single    one correct letter            submission "B"
  multiple  set of letters                submission "A,D" (order-free)
  match     uppercase→lowercase pairs     submission "A-c,B-a" (order-free, ":" ok)
  freeform  regex (or list, any match)    submission is free text
  quizset   several questions in one step  submission "A|B,C" ('|' between
            questions, in spec order). Every question must be right: the step
            is one exercise, so it is solved or it is not. Each entry carries
            the `kind` that grades it, so a set can mix single and multiple.

`quiz_answers` never reaches the client: read() only exposes `quiz_type`.
Answers column shapes (tolerant — bare values accepted):
  checkpoint {"code": "4b279a"}                   or "4b279a"
  single    {"answer": "B"}                       or "B"
  multiple  {"answers": ["A", "D"]}               or ["A", "D"]
  match     {"pairs": {"A": "c", "B": "a"}}       or {"A": "c", ...}
  freeform  {"patterns": ["^ritchie$"], "case_sensitive": false} or ["^ritchie$"]
  quizset   {"questions": [{"kind": "single", "answer": "B"}, ...]}
"""
import json
import re

from flask import Blueprint

from flask_babel import gettext as _, ngettext

from CTFd.exceptions.challenges import (
    ChallengeCreateException,
    ChallengeUpdateException,
)
from CTFd.models import Challenges, Fails, Ratelimiteds, Submissions, db
from CTFd.plugins.challenges import BaseChallenge
from CTFd.utils.user import get_current_user, is_admin

from . import choices
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


def _grade_rating(submission):
    """The closing step: any choice of the list completes it (PLAN.md §67).

    Nothing is stored from here. `QuizChallenge.solve` counts the choices
    once the solve is in, so an answer is counted exactly when it completes
    the step.
    """
    if choices.parse(submission) is None:
        # No choice, no solve: this is what keeps the progress bar honest.
        return False, _("Pick at least one first")
    return True, _("Thanks for the feedback!")


def _checkpoint_code(answers):
    """The instructor's code for this step, whatever shape it was stored in."""
    if isinstance(answers, dict):
        answers = answers.get("code")
    return str(answers).strip() if answers else ""


def _grade_checkpoint(challenge, submission, skip=False):
    """Who says this exercise is done — the instructor, or the participant.

    Self-serve accepts the button and never looks at the code, which stays in
    the database so that switching the instance back to instructor-led works
    (PLAN.md §25.4). Instructor-led compares the code, case-insensitively, the
    way the static flag it replaces did.

    An admin may skip the code (PLAN.md §49). They are the one who would read
    it out, and testing a subject should not mean opening the answer sheet in
    a second tab for every step. `skip` is a field of its own in the request,
    not a magic submission, and it is honoured for an admin only: sent by
    anybody else it is ignored and the code is compared as usual.
    """
    if is_self_serve():
        return True, _("Noted")
    code = _checkpoint_code(challenge.quiz_answers)
    if not code:
        # Before the skip, not after: an admin who tests with the button is
        # the one person placed to notice a step nobody could validate.
        return False, _("Misconfigured checkpoint, please report it")
    if skip and is_admin():
        return True, _("Noted")
    if submission.strip().lower() == code.lower():
        return True, _("Correct")
    return False, _("Incorrect")


def _grade_quizset(answers, submission):
    """Every question of the step, in spec order, separated by "|".

    All-or-nothing on purpose: the questions belong to one exercise, and a step
    half solved would still gate the next one. A missing or extra part means the
    page and the answer sheet disagree, which is never the participant's fault
    to guess at — it simply does not grade.
    """
    questions = answers.get("questions") if isinstance(answers, dict) else answers
    parts = submission.split("|")
    if not questions or len(parts) != len(questions):
        return False
    for question, part in zip(questions, parts):
        grader = GRADERS.get(question.get("kind", "single"))
        if grader is None or not grader(question, part):
            return False
    return True


def _quizset_wrong(answers, submission):
    """Les numeros (a partir de 1) des questions ratees, dans l'ordre du sujet.

    Vide quand la copie et la feuille de reponses n'ont pas le meme nombre de
    questions : la ou `_grade_quizset` refuse de noter, il n'y a rien a nommer
    non plus.
    """
    questions = answers.get("questions") if isinstance(answers, dict) else answers
    parts = submission.split("|")
    if not questions or len(parts) != len(questions):
        return []
    wrong = []
    for i, (question, part) in enumerate(zip(questions, parts), start=1):
        grader = GRADERS.get(question.get("kind", "single"))
        if grader is None or not grader(question, part):
            wrong.append(i)
    return wrong


# --- what is kept of a wrong answer (PLAN.md §65) ---------------------------

# Kinds whose submission is a choice among options the page offered. Nothing
# of the participant is in it, and "which wrong option" is what improves a
# question, so it is kept as given.
CHOICE_KINDS = ("single", "multiple", "match")
# What a choice looks like: option letters, or pairs of them, between commas.
# The page sends nothing else, but the API takes any string, and a text sent
# to a multiple-choice step is still a text.
_CHOICE = re.compile(r"^\s*[A-Za-z]{1,2}(?:\s*[-:]\s*[A-Za-z]{1,2})?\s*$")
# Longer than any answer a step expects; bounds the work of one comparison.
_DISTANCE_CAP = 200
_REGEX_SPECIAL = set(".*+?[]{}()|^$\\")


def _distance(a, b):
    """Levenshtein distance: insertions, deletions and substitutions."""
    a, b = a[:_DISTANCE_CAP], b[:_DISTANCE_CAP]
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        previous, row[0] = row[0], i
        for j, cb in enumerate(b, start=1):
            previous, row[j] = row[j], min(
                row[j] + 1, row[j - 1] + 1, previous + (ca != cb))
    return row[-1]


def _literals(pattern):
    """The plain texts a pattern accepts, when it is plain text in disguise.

    A freeform answer is a regex, and almost always an anchored word or a few
    of them: `^ritchie$`, `^(ken|dennis)$`. Those have a text to measure a
    distance to. A pattern that is really a regex (digits, any character) has none, and
    yields nothing.
    """
    text = str(pattern)
    if text.startswith("^"):
        text = text[1:]
    if text.endswith("$") and not text.endswith("\\$"):
        text = text[:-1]
    if text.startswith("(?:") and text.endswith(")"):
        text = text[3:-1]
    elif text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    found = []
    for choice in text.split("|"):
        plain, escaped = [], False
        for char in choice:
            if escaped:
                if char.isalnum():
                    # A class or an assertion (digit, word, boundary), not a
                    # character.
                    return []
                plain.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char in _REGEX_SPECIAL:
                return []
            else:
                plain.append(char)
        if escaped:
            return []
        found.append("".join(plain))
    return found


def _how_far(text, accepted, case_sensitive=False):
    """`~3`: how far a wrong text is from the nearest accepted one.

    Compared the way the answer is matched: surrounding spaces off, and case
    ignored unless the step says otherwise. `~?` when there is no accepted
    text to compare with. The tilde is what tells a distance from an answer
    on the staff's page.
    """
    text = text.strip()
    accepted = [a.strip() for a in accepted if a is not None]
    if not case_sensitive:
        text = text.lower()
        accepted = [a.lower() for a in accepted]
    if not accepted:
        return "~?"
    return "~%d" % min(_distance(text, a) for a in accepted)


def _freeform_kept(answers, submission):
    if isinstance(answers, dict):
        patterns = answers.get("patterns", [])
        case_sensitive = bool(answers.get("case_sensitive", False))
    else:
        patterns = answers if isinstance(answers, list) else [answers]
        case_sensitive = False
    accepted = [text for p in patterns or [] if p for text in _literals(p)]
    return _how_far(submission, accepted, case_sensitive)


def _choice_kept(submission):
    parts = [part for part in submission.split(",") if part.strip()]
    if all(_CHOICE.match(part) for part in parts):
        return submission
    return "~?"


def kept_of_wrong(challenge, submission):
    """What a wrong submission leaves in the database.

    A choice stays a choice. Anything the participant typed becomes its
    distance to the right answer: enough to tell a typo from a guess, and
    nothing of what a lyceen may have written in a text box.
    """
    kind = challenge.quiz_type
    answers = challenge.quiz_answers
    if kind in CHOICE_KINDS:
        return _choice_kept(submission)
    if kind == "checkpoint":
        code = _checkpoint_code(answers)
        return _how_far(submission, [code] if code else [])
    if kind == "freeform":
        return _freeform_kept(answers, submission)
    if kind == "quizset":
        questions = answers.get("questions") if isinstance(answers, dict) else answers
        parts = submission.split("|")
        if not questions or len(parts) != len(questions):
            return "~?"
        return "|".join(
            _choice_kept(part) if question.get("kind", "single") in CHOICE_KINDS
            else _freeform_kept(question, part)
            for question, part in zip(questions, parts))
    # ack, info, rating, or a kind this does not know: nothing to compare
    # with, and no reason to keep the text.
    return "~?"


GRADERS = {
    "single": _grade_single,
    "multiple": _grade_multiple,
    "match": _grade_match,
    "freeform": _grade_freeform,
    "quizset": _grade_quizset,
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
        cls._count_rating(user, challenge, request)
        try:
            enqueue_solve(user.id, challenge.id)
        except Exception:  # noqa: BLE001
            db.session.rollback()

    @staticmethod
    def _count_rating(user, challenge, request):
        """A closing step's solve counts its choices and does not keep them.

        Core stores the submission on the solve, next to the account. The
        choices go to the counts instead (choices.py), which name nobody.
        """
        row = QuizChallengeModel.query.filter_by(id=challenge.id).first()
        if row is None or row.quiz_type != "rating":
            return
        data = request.form or request.get_json()
        keys = choices.parse(str(data.get("submission", "")))
        # On `submissions`, where the column is: a solve is a joined row and
        # an UPDATE through `Solves` would span both tables.
        Submissions.query.filter_by(
            user_id=user.id, challenge_id=challenge.id, type="correct").update(
            {"provided": "-"}, synchronize_session=False)
        if keys:
            choices.record(user, challenge.id, keys)
        db.session.commit()

    @classmethod
    def _keep_wrong(cls, model, user, team, challenge, request):
        """Core's `fail`, with what is kept in place of what was typed."""
        challenge = QuizChallengeModel.query.filter_by(id=challenge.id).first()
        data = request.form or request.get_json()
        submission = str(data.get("submission", "")).strip()
        try:
            kept = kept_of_wrong(challenge, submission)
        except (AttributeError, TypeError):
            # A malformed answer sheet. Not the moment to raise: the attempt
            # is already graded, and the text is still not kept.
            kept = "~?"
        db.session.add(model(
            user_id=user.id,
            team_id=team.id if team else None,
            challenge_id=challenge.id,
            provided=kept,
        ))
        db.session.commit()

    @classmethod
    def fail(cls, user, team, challenge, request):
        """A wrong answer is recorded without its text (PLAN.md §65)."""
        cls._keep_wrong(Fails, user, team, challenge, request)

    @classmethod
    def ratelimited(cls, user, team, challenge, request):
        """Same for an attempt refused for coming too fast: core stores it
        ungraded, so it is kept as a wrong one would be."""
        cls._keep_wrong(Ratelimiteds, user, team, challenge, request)

    @classmethod
    def attempt(cls, challenge, request):
        challenge = QuizChallengeModel.query.filter_by(id=challenge.id).first()
        data = request.form or request.get_json()
        submission = str(data.get("submission", ""))
        if challenge.quiz_type == "checkpoint":
            return _grade_checkpoint(challenge, submission,
                                     skip=data.get("skip_code") is True)
        if challenge.quiz_type == "ack":
            # Pressing the button IS the completion — there is nothing to grade.
            return True, _("Noted")
        if challenge.quiz_type == "rating":
            return _grade_rating(submission)
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
        if challenge.quiz_type == "quizset":
            wrong = _quizset_wrong(challenge.quiz_answers, submission)
            if wrong:
                listed = ", ".join(str(n) for n in wrong)
                return False, ngettext(
                    "Not yet: look again at question %(list)s",
                    "Not yet: look again at questions %(list)s",
                    len(wrong), list=listed)
        return False, _("Incorrect")
