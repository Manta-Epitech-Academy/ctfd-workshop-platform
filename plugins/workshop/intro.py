"""Hiding the introductions, without touching the graph the sync wrote.

A subject's entrypoint document is a step (`__intro__`, an `ack` quiz: read
it, press « J'ai lu ») that gates everything after it, and in a workshop the
advanced subject's intro is where the starter-before-advanced gate sits
(PLAN.md §19). That is the right shape when the introduction is read here.
It is the wrong shape once the introduction is given on Jump, before the
talent ever reaches this instance: the participant then lands on a page of
text they have just read, with a button whose only purpose is to unlock the
work, and a progress bar that counts that click as a step.

So: one config key, `workshop_intro_hidden`, **unset reads as hidden**, since
the introduction now lives on Jump by default and an instance has to ask for
it back. Ticked on `/admin/workshop/settings`, read at render time like the
mode (§25), so flipping it is a toggle and not a re-sync. Every subject's
intro, not only the starter's: the advanced subject's introduction is on
Jump too.

What hidden means, in four rules:

  not shown     the step is on no page: not above the index cards, not folded
                into part 1, not in the stepper, not in the Parcours graph.
  not counted   `progress.counts()` leaves it out, so the bar, the Answers
                table and the number Jump receives all agree (§31).
  passed through
                a step that waited on the intro waits on what the intro
                waited on. The starter's intro waited on nothing, so part 1
                opens at once; the advanced subject's intro waited on the
                starter's closing step, so the advanced subject still does.
  still kept    its toolbox and glossary regions stay on the toolbox page,
                open from the start, because the tools a subject introduces
                there are the ones every step after it relies on.

## Why a solve is written on the participant's behalf

CTFd enforces prerequisites itself, inside the attempt endpoint
(CTFd/api/v1/challenges.py:712): a submission on a step whose prerequisites
are not all solved is a 403 before any plugin code runs. The page can show
part 1 open; the first answer would still be refused. Rewriting the
requirements the sync wrote would make the toggle a re-import in disguise.

Instead, the moment a hidden intro's own prerequisites are met, this module
records the acknowledgement for the participant: a `Solves` row, the same
row pressing the button would have written, inserted directly rather than
through `QuizChallenge.solve()` so the Jump outbox never hears of it (a
step nobody sees reports nothing). The row is harmless in every direction:
it stays out of the counters while hidden, and if the intro is shown again
it reads as acknowledged, which is what the participant's progress past it
already says.

It happens where the participant's state is read — the page and the graph —
because both run before any attempt can, and both already hold the solve
set. A participant who never loads a page cannot submit anything.
"""
from sqlalchemy.exc import IntegrityError

from CTFd.cache import clear_challenges, clear_standings
from CTFd.models import ChallengeTopics, Solves, Topics, db
from CTFd.utils import get_config, set_config
from CTFd.utils.user import get_ip

CONFIG_KEY = "workshop_intro_hidden"
INTRO_SLUG = "__intro__"


def intro_hidden():
    """Are the introductions hidden? Unset means yes.

    Empty counts as unset: a config cleared through the API, or put back by a
    check script that found it unset, is stored as "", and reading that as
    false would show every introduction on an instance nobody asked to.
    """
    value = get_config(CONFIG_KEY)
    return True if value in (None, "") else bool(value)


def set_intro_hidden(on):
    # "true"/"false": `_get_config` turns exactly those two strings back into
    # booleans (CTFd/utils/__init__.py), where an int reads back as 1/0.
    set_config(CONFIG_KEY, "true" if on else "false")


def intro_ids():
    """Every entrypoint step on the instance, from the topics the sync tags.

    The sync records each step under `ws:<subject>:<slug>` (sync_subject.py
    `slug_topic`), and the entrypoint step's slug is `__intro__`. Read from
    the topics rather than from `quiz_type == "ack"` so that an acknowledgement
    step an author writes on purpose is never mistaken for an introduction.
    """
    rows = (db.session.query(ChallengeTopics.challenge_id, Topics.value)
            .join(Topics, Topics.id == ChallengeTopics.topic_id)
            .filter(Topics.value.like("ws:%")).all())
    return {cid for cid, value in rows
            if value.startswith("ws:") and value.endswith(":" + INTRO_SLUG)}


def hidden_intro_ids():
    """The intro steps to leave out, or nothing when they are shown."""
    return intro_ids() if intro_hidden() else set()


def pass_through(prereqs, hidden):
    """Rewrite `{id: [prerequisite ids]}` so no step waits on a hidden intro.

    A hidden intro is replaced by its own prerequisites, transitively: a
    step gated on an intro gated on another subject's closing step is now
    gated on that closing step. The intros' own entries are kept, since the
    acknowledgement written for the participant has to know when it is due.
    """
    if not hidden:
        return prereqs

    def expand(cid, seen):
        out = []
        for p in prereqs.get(cid, []):
            if p in hidden:
                if p not in seen:
                    out.extend(expand(p, seen | {p}))
            else:
                out.append(p)
        # Order kept, duplicates dropped: `blocked_by` names these.
        return list(dict.fromkeys(out))

    return {cid: expand(cid, {cid}) for cid in prereqs}


def acknowledge_due(user, prereqs, hidden, solved):
    """Write the acknowledgement of every hidden intro whose turn has come.

    `prereqs` is the passed-through map and `solved` the participant's solve
    ids; the set handed back includes what was just written, so the caller's
    own unlock computation sees it without a second read of the memoized
    solve list.
    """
    due = [cid for cid in hidden
           if cid not in solved and set(prereqs.get(cid, [])) <= solved]
    if not due:
        return solved
    written = set()
    for cid in due:
        try:
            db.session.add(Solves(user_id=user.id, team_id=user.team_id,
                                  challenge_id=cid, ip=get_ip(),
                                  provided="hidden introduction", type="correct"))
            db.session.commit()
            written.add(cid)
        except IntegrityError:
            # Two requests of the same participant raced; the row exists.
            db.session.rollback()
            written.add(cid)
    if written:
        # The same two invalidations the attempt endpoint runs after a solve
        # (CTFd/api/v1/challenges.py:884), so the memoized solve list and the
        # standings do not lag this row by a minute.
        clear_standings()
        clear_challenges()
    return solved | written


def resolve(user, challenges, solved):
    """`(hidden, prereqs, solved)` for one participant over the board.

    One call for the page and one for the graph, so the two can never
    disagree about what is hidden, what waits on what, and what has been
    acknowledged on the participant's behalf. `prereqs` only ever names
    challenges on the board; `solved` is a set.
    """
    known = {c.id for c in challenges}
    raw = {c.id: [r for r in ((c.requirements or {}).get("prerequisites", []))
                  if r in known]
           for c in challenges}
    hidden = hidden_intro_ids() & known
    prereqs = pass_through(raw, hidden)
    solved = acknowledge_due(user, prereqs, hidden, set(solved))
    return hidden, prereqs, solved
