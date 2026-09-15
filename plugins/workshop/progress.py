"""How far a participant got, counted once for everybody who asks.

Three places need the same two numbers — the workshop page renders "8 / 15",
the admin Answers table shows a column of them, and the Jump outbox reports
them so a talent's XP match the screen they are looking at (PLAN.md §31). They
used to be three implementations of one rule: `page.py` wrote the predicate
once and the sum four times, and `answers.py` carried its own copy against
`Challenges` rows instead of step dicts. A fourth copy, in a background
drainer nobody watches, is how "Jump says 7, the page says 8" happens.

The rule itself, in one line: a step counts unless it is a pure note or the
content marked it optional. Both exclusions are deliberate and neither is a
property of the challenge alone —

  `info`     a closing note is text, not work. `rating` is NOT excluded: the
             workshop rating is the last step and it counts, so the bar reads
             14/15 until it is given.
  optional   bonus work, listed in the `workshop_optional` config by the sync.
             Without it a subject with bonus steps can never read 100%, and
             100% is what gets the end-of-workshop feedback in.

This module imports nothing from `page.py` — the dependency runs the other
way, so the page, the admin report and the drainer can all reach it without a
cycle and without a view being imported for its arithmetic.
"""
import json

from CTFd.models import Challenges
from CTFd.utils import get_config
from CTFd.utils.challenges import get_solve_ids_for_user_id

# A pure note is text rather than work: it stays out of the progress counters
# and never becomes the current step. `rating` is NOT in here on purpose — see
# the module docstring.
NON_TASK_KINDS = ("info",)


def ordered_challenges():
    """Board order = the reading order the sync wrote into `position`."""
    return (
        Challenges.query.filter(Challenges.state != "hidden")
        .order_by(Challenges.position.is_(None), Challenges.position, Challenges.id)
        .all()
    )


def id_set(config_key):
    """A set of challenge ids the sync wrote to a config, or empty."""
    raw = get_config(config_key)
    if not raw:
        return set()
    try:
        return set(json.loads(raw))
    except (TypeError, ValueError):
        return set()


def optional_ids():
    """Steps that are bonus work: they count towards nothing."""
    return id_set("workshop_optional")


def counts(challenge, optional):
    """Does this step count towards progress?

    `optional` is passed in rather than read here so a caller iterating over a
    board reads the config once instead of once per step. A non-quiz challenge
    has no `quiz_type` at all, hence the defensive getattr.
    """
    return (getattr(challenge, "quiz_type", None) not in NON_TASK_KINDS
            and challenge.id not in optional)


def count_steps(steps):
    """`(solved, total)` over a list of rendered step dicts.

    The page counts within whatever it is showing — one document reads "3 / 10"
    on its own rather than as a slice of the subject — so it cannot go through
    `progress_for_user`, which is instance-wide. What it must not do is spell
    the ratio out by hand: both halves have to count the same population or the
    outro pushes it to "15 / 14", which is exactly what happened once.
    """
    counted = [s for s in steps if s["counts"]]
    return sum(1 for s in counted if s["solved"]), len(counted)


def counted_ids(challenges=None, optional=None):
    """The ids of every step that counts, on this instance."""
    if challenges is None:
        challenges = ordered_challenges()
    if optional is None:
        optional = optional_ids()
    return {c.id for c in challenges if counts(c, optional)}


def progress_for_user(user_id):
    """`(solved, total)` over the counted population, for one participant.

    Deliberately built on `get_solve_ids_for_user_id`, the same memoized
    reader the page uses, so the two can never disagree about which solves
    exist. Its 60 s memoization is cleared by `clear_challenges()` on every
    solve (CTFd/api/v1/challenges.py:884), which is why this must be called
    *after* a solve request has finished rather than inside the solve hook.
    """
    counted = counted_ids()
    solved = set(get_solve_ids_for_user_id(user_id))
    return len(solved & counted), len(counted)
