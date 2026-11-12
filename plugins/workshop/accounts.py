"""Deleting an account outright, and banning a cohort: one way each.

The way core's own DELETE /api/v1/users does it (api/v1/users.py
`UserPublic.delete`): every table that names the user, then the user. What
core leaves to the database is left to it here too: ratings, and this plugin's
own rows (the Jump link and outbox, a saved workspace, a staff role), all
cascade on `users.id`.

Two callers, each with its own guard in front of this: staff.py deletes a
supervisor and nobody else, and jumpqueue.py deletes the accounts of talents
Jump has erased and nobody else (PLAN.md §50). Neither guard belongs here,
which is why there is one function and not two copies of six deletes.

Banning is the same story: the registration codes' page calls it for a code's
cohort and for a cohort of the retired external door (codes.py). The guard is
the same for both, so it is in the function.
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


def ban_accounts(users, banned):
    """Ban, or un-ban, these accounts. Returns how many were changed.

    **Ban and not delete.** Shutting a cohort out is a decision made in a
    hurry, and deleting takes their solves and their history with them,
    irreversibly. Banning stops them signing in and is undone from the same
    page. Deleting one account stays where it already is, under
    `/admin/users`, where the confirmation names the account.

    An account that has since become an admin or a supervisor is skipped: a
    bulk action aimed at participants must not be able to lock out the room's
    staff.
    """
    # Imported here: staff.py imports this module for `delete_accounts`.
    from .staff import WorkshopStaff

    staff = {row.user_id for row in db.session.query(WorkshopStaff.user_id).all()}
    users = [u for u in users if u.type != "admin" and u.id not in staff]
    for user in users:
        user.banned = banned
    db.session.commit()
    for user in users:
        clear_user_session(user_id=user.id)
    clear_standings()
    return len(users)
