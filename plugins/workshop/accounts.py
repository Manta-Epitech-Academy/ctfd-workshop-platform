"""Deleting an account outright: the one way this plugin does it.

The way core's own DELETE /api/v1/users does it (api/v1/users.py
`UserPublic.delete`): every table that names the user, then the user. What
core leaves to the database is left to it here too: ratings, and this plugin's
own rows (the Jump link and outbox, a saved workspace, a staff role), all
cascade on `users.id`.

Two callers, each with its own guard in front of this: staff.py deletes a
supervisor and nobody else, and jumpqueue.py deletes the accounts of talents
Jump has erased and nobody else (PLAN.md §50). Neither guard belongs here,
which is why there is one function and not two copies of six deletes.
"""
from CTFd.cache import clear_challenges, clear_standings, clear_user_session
from CTFd.models import (Awards, Notifications, Solves, Submissions, Tracking,
                         Unlocks, Users, db)

OWNED = (Notifications, Awards, Unlocks, Submissions, Solves, Tracking)


def delete_accounts(user_ids):
    """Remove these accounts and everything that names them. Returns how many.

    One commit and one cache clear for the lot, so a batch of erasures costs
    the scoreboard one recomputation rather than one per account.
    """
    user_ids = sorted(set(user_ids))
    if not user_ids:
        return 0
    for model in OWNED:
        model.query.filter(model.user_id.in_(user_ids)).delete(
            synchronize_session=False)
    deleted = Users.query.filter(Users.id.in_(user_ids)).delete(
        synchronize_session=False)
    db.session.commit()
    for user_id in user_ids:
        clear_user_session(user_id=user_id)
    clear_standings()
    clear_challenges()
    return deleted
