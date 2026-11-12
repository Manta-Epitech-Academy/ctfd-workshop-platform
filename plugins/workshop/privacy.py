"""What the instance stops keeping: addresses, and wrong answers (PLAN.md §65).

CTFd writes the client's IP address in three places and the plugin has no use
for any of them: on every submission, on every tracking row (one per account
and address, plus one each time a challenge is opened), and at the head of
most log lines. It also logs every submitted text, right or wrong. A
participant here is a minor who came in with a code and gave no name; what is
kept about them should not be more than the workshop needs.

Nothing here edits CTFd. Three hooks, all from the plugin's `load`:

  the rows   a `before_insert` listener on `Submissions` and on `Tracking`,
             with `propagate=True` so every subclass is covered (`Solves`,
             `Fails`, a challenge-open tracking row). Whatever wrote the row,
             core, this plugin or another challenge type, it lands with no
             address. A listener rather than an override of `solve()` and
             `fail()`: an override only covers the quiz type, and only the
             calls somebody remembered.
  the tracker  core's `before_request` tracker looks for its row by address
             and inserts one when it finds none. With the address blanked in
             the row and not in the lookup, it would insert a row on every
             request. So the name `get_ip` is rebound in that one module
             (`CTFd.utils.initialization`) to a function that answers nothing:
             the tracker then finds its one row per account and only moves
             its date.
  the logs   `get_ip` is rebound the same way in `CTFd.utils.logging`, so the
             `{ip}` of every log format, core's and ours, prints `-`. And a
             filter on the `submissions` logger takes the submitted text out
             of its lines, which core writes for every attempt.

`CTFd.utils.user.get_ip` itself is left alone: the rate limits (core's
`ratelimit` decorator, the Jump door's throttle) key a short-lived cache entry
on the address, and that is the one legitimate use of it.

Porting to a later CTFd: check that the tracker in
`CTFd/utils/initialization/__init__.py` and `log()` in
`CTFd/utils/logging/__init__.py` still call a module-level `get_ip`, and that
the submissions log line still reads `submitted <text> on <id> with kpm`.
`scripts/privacy_check.py` fails if either moved.

What this does not reach: the access log of gunicorn (`ACCESS_LOG`) and of
whatever proxy sits in front. Those belong to the deployment.
"""
import logging
import re

from sqlalchemy import event

import CTFd.utils.initialization
import CTFd.utils.logging
from CTFd.models import Submissions, Tracking

# What replaces the address in a log line, and the submitted text.
NO_ADDRESS = "-"
NO_TEXT = "-"

# Greedy on purpose: the text is everything up to the *last* " on <id> with
# kpm ", so a submission that contains those words cannot leave a piece behind.
_SUBMITTED = re.compile(r" submitted .* on (\d+) with kpm ", re.DOTALL)


def _blank_address(mapper, connection, target):
    target.ip = None


def _no_address(req=None):
    """`get_ip` for the tracker: the same answer for everybody, which is what
    the rows hold."""
    return None


def _no_address_logged(req=None):
    return NO_ADDRESS


class NoSubmittedText(logging.Filter):
    """Takes the submitted text out of a `submissions` log line."""

    def filter(self, record):
        message = record.getMessage()
        record.msg = _SUBMITTED.sub(
            r" submitted %s on \1 with kpm " % NO_TEXT, message)
        record.args = None
        return True


def load_privacy(app):
    for model in (Submissions, Tracking):
        if not event.contains(model, "before_insert", _blank_address):
            event.listen(model, "before_insert", _blank_address, propagate=True)
    CTFd.utils.initialization.get_ip = _no_address
    CTFd.utils.logging.get_ip = _no_address_logged
    logger = logging.getLogger("submissions")
    if not any(isinstance(f, NoSubmittedText) for f in logger.filters):
        logger.addFilter(NoSubmittedText())
