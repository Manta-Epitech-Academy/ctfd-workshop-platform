"""What a participant says about a step: a fixed list, and nothing typed (PLAN.md §67).

The thumbs and the comment box are gone. A comment could hold anything, a
name included; a list holds nothing personal. So a participant picks from
`CHOICES`, several if they want, and that is all there is to say.

**What is kept is a count, and it names nobody.** `workshop_step_feedback`
holds one row per

    cohort + content + step + subject version + choice

with how many times it was picked. No user id, no date: nothing on a row says
who picked it, or when. The cohort is the audience key the cohort providers
hand out (cohorts.py), the content and the step are the two topics the sync
writes on a step (`ws-content:`, `ws:`, PLAN.md §66), and the version is the
commit of the last import. The row with the choice `answered` counts the
people who sent the list, since one person can pick three things.

**A choice is a key.** `too_hard`, not « Trop dur »: the label is an English
msgid, French through the catalogue, and can be reworded without breaking
the counts. The list is the platform's, not each subject's.

**Once per step.** With no user id on the counts, an answer cannot be found
again to be changed. So it is sent once, and something has to say it was:
on a closing step that is the solve; on any other step it is a row of
`workshop_feedback_given`, which holds an account and a step and not one
choice.

**Only participants are counted.** An admin's or a hidden account's answer
completes the step and writes nothing: with no user id there is no taking a
test answer out of the counts afterwards, the way scope.py does for every
other report.

Two ways in:

  * the closing step of a part (`quiz_type = 'rating'`): the list **is** the
    submission, and any choice completes the step (quiz.py calls `record`);
  * any other solved step: `POST /api/v1/workshop/feedback/<challenge id>`.

Core's own `PUT /api/v1/challenges/<id>/ratings` took a written review. The
page no longer calls it, and it is refused here for anyone but an admin, so
a comment cannot be written around the list.
"""
import re

from flask import Blueprint, jsonify, request
from flask_babel import gettext
from sqlalchemy.exc import IntegrityError

from CTFd.models import Challenges, ChallengeTopics, Solves, Topics, Users, db
from CTFd.utils import get_config
from CTFd.utils.decorators import authed_only
from CTFd.utils.user import get_current_user, is_admin

from .audience import DEFAULT, audience_of

workshop_choices = Blueprint("workshop_choices", __name__)

def N_(msgid):
    """Marks a label for the catalogue. It is translated when it is drawn."""
    return msgid


# group -> its choices, in the order the page draws them. A key is forever:
# it is what the counts are filed under.
CHOICES = (
    ("feeling", (("great", N_("Great")),
                 ("good", N_("Good")))),
    ("difficulty", (("too_hard", N_("Too hard")),
                    ("too_easy", N_("Too easy")))),
    ("clarity", (("unclear_task", N_("I did not understand what to do")),
                 ("unclear_explanation", N_("The explanation is not clear")))),
    ("problem", (("statement_error", N_("There is a mistake in the instructions")),
                 ("not_working", N_("It does not work")))),
)
KEYS = tuple(key for _group, choices in CHOICES for key, _label in choices)
# The label as written here, in English: what the admin pages show.
LABELS = {key: label for _group, choices in CHOICES for key, label in choices}
# What the report reads as a good sign. Everything else names a problem.
POSITIVE = ("great", "good")
# The row that counts the people who answered, whatever they picked.
ANSWERED = "answered"

STEP_TOPIC = "ws:"
CONTENT_TOPIC = "ws-content:"
CORE_RATING = re.compile(r"/api/v1/challenges/\d+/ratings/?")


class StepFeedback(db.Model):
    """How many times one choice was picked. See the module docstring."""
    __tablename__ = "workshop_step_feedback"
    __table_args__ = (
        db.UniqueConstraint("cohort", "content", "step", "version", "choice",
                            name="uq_workshop_step_feedback"),
    )

    id = db.Column(db.Integer, primary_key=True)
    # "" where there is nothing to say: an account in no cohort, an instance
    # synced from the command line. Never NULL, which a unique key ignores.
    cohort = db.Column(db.String(96), nullable=False, default="")
    content = db.Column(db.String(64), nullable=False, default="")
    step = db.Column(db.String(191), nullable=False)
    version = db.Column(db.String(40), nullable=False, default="")
    choice = db.Column(db.String(32), nullable=False)
    count = db.Column(db.Integer, nullable=False, default=0)


class FeedbackGiven(db.Model):
    """This account has sent the list for this step. Not what it picked."""
    __tablename__ = "workshop_feedback_given"

    user_id = db.Column(db.Integer,
                        db.ForeignKey("users.id", ondelete="CASCADE"),
                        primary_key=True)
    challenge_id = db.Column(db.Integer,
                             db.ForeignKey("challenges.id", ondelete="CASCADE"),
                             primary_key=True)


# ---------------------------------------------------------------------------
# What a row is filed under
# ---------------------------------------------------------------------------

def parse(sent):
    """The choices in what was sent, or None when it is not a valid answer.

    `sent` is the closing step's submission (`"good,too_hard"`) or the list
    the endpoint takes. Empty, or holding anything that is not a key, is not
    an answer: there is no free text to keep, so nothing is kept of it.
    """
    if isinstance(sent, str):
        sent = [part.strip() for part in sent.split(",")]
    if not isinstance(sent, (list, tuple)) or not sent:
        return None
    if not all(isinstance(key, str) and key in KEYS for key in sent):
        return None
    return [key for key in KEYS if key in sent]


def cohort_key(user_id):
    """The cohort an account is in, as text. "" for an account in none."""
    key = audience_of(user_id)
    if key == DEFAULT:
        return ""
    return ("%s:%s" % key)[:96]


def step_key(challenge_id):
    """`(content, step)` of a challenge, from the topics the sync wrote.

    A challenge made by hand has neither: it is filed under its id, in the
    content the instance serves.
    """
    values = [value for (value,) in
              db.session.query(Topics.value)
              .join(ChallengeTopics, ChallengeTopics.topic_id == Topics.id)
              .filter(ChallengeTopics.challenge_id == challenge_id).all()]
    step = next((v[len(STEP_TOPIC):] for v in values
                 if v.startswith(STEP_TOPIC)), "#%d" % challenge_id)
    content = next((v[len(CONTENT_TOPIC):] for v in values
                    if v.startswith(CONTENT_TOPIC)), None)
    if content is None:
        content = str(get_config("workshop_content") or "")
    return content[:64], step[:191]


def version():
    """The commit the instance's content was imported from, or ""."""
    from .source import last_sync
    return str((last_sync() or {}).get("commit") or "")[:40]


def counted(user):
    """Whether this account's answer goes in the counts: participants only."""
    return user.type != "admin" and not user.hidden


def _add_one(where):
    bump = {StepFeedback.count: StepFeedback.count + 1}
    if StepFeedback.query.filter_by(**where).update(bump, synchronize_session=False):
        return
    try:
        with db.session.begin_nested():
            db.session.add(StepFeedback(count=1, **where))
    except IntegrityError:
        # Somebody else made the row between the two statements.
        StepFeedback.query.filter_by(**where).update(bump, synchronize_session=False)


def record(user, challenge_id, keys):
    """Count these choices. The caller commits."""
    if not counted(user):
        return
    content, step = step_key(challenge_id)
    where = {"cohort": cohort_key(user.id), "content": content, "step": step,
             "version": version()}
    for choice in [ANSWERED] + list(keys):
        _add_one({**where, "choice": choice})


def given(user_id, challenge_id):
    return db.session.query(FeedbackGiven.user_id).filter_by(
        user_id=user_id, challenge_id=challenge_id).first() is not None


def enabled():
    """CTFd's own switch for ratings turns the list off too."""
    return get_config("challenge_ratings", default="public") != "disabled"


def groups():
    """What the page draws: `[(group, [(key, label)])]`, in its language."""
    return [(group, [(key, gettext(label)) for key, label in choices])
            for group, choices in CHOICES]


# ---------------------------------------------------------------------------
# The way in for a step that is not a closing step
# ---------------------------------------------------------------------------

def _refuse(status, reason):
    return jsonify({"success": False, "errors": {"": [reason]}}), status


@workshop_choices.route("/api/v1/workshop/feedback/<int:challenge_id>",
                        methods=["POST"])
@authed_only
def send(challenge_id):
    user = get_current_user()
    challenge = Challenges.query.filter_by(id=challenge_id).first()
    if challenge is None or challenge.state == "hidden":
        return _refuse(404, "no such step")
    if not enabled():
        return _refuse(403, "feedback is turned off")
    if getattr(challenge, "quiz_type", None) == "rating":
        # Its list is its submission: it goes through the attempt.
        return _refuse(400, "a closing step is answered by submitting it")
    solved = db.session.query(Solves.id).filter_by(
        user_id=user.id, challenge_id=challenge_id).first()
    if solved is None:
        return _refuse(403, "finish the step first")
    body = request.get_json(silent=True)
    sent = body.get("choices") if isinstance(body, dict) else None
    keys = parse(sent) if isinstance(sent, list) else None
    if keys is None:
        return _refuse(400, "pick from the list")
    db.session.add(FeedbackGiven(user_id=user.id, challenge_id=challenge_id))
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return _refuse(409, "already sent")
    record(user, challenge_id, keys)
    db.session.commit()
    return jsonify({"success": True})


def load_choices(app):
    app.register_blueprint(workshop_choices)
    app.jinja_env.globals["ws_feedback_choices"] = groups

    @app.before_request
    def _no_written_review():
        # Core's rating takes a review, which is free text.
        if (request.method == "PUT" and CORE_RATING.fullmatch(request.path)
                and not is_admin()):
            return _refuse(403, "ratings go through the list")
