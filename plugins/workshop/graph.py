"""Challenge-graph endpoint for the workshop platform.

`GET /api/v1/workshop/graph` returns the prerequisite DAG for the current
user, feeding two UI features (both plugin JS, no CTFd core edit):
  - the branching "next challenge" button in the challenge modal
  - the dedicated "Parcours" path-visualization page

CTFd exposes prerequisites only to admins, so this endpoint computes the
graph server-side and applies the same visibility rules as the board:
locked challenge names are hidden ('???') unless the user has unlocked
(prereqs met) or solved them.

Node: {id, name, category, position, solved, unlocked, prerequisites: [id]}
Edges are the reverse of prerequisites (next[a] = [b, ...]) and are derivable
client-side; we also return them precomputed for convenience.
"""
from flask import Blueprint, jsonify

from CTFd.models import Challenges
from CTFd.utils.challenges import get_solve_ids_for_user_id
from CTFd.utils.decorators import authed_only, during_ctf_time_only
from CTFd.utils.decorators.visibility import check_challenge_visibility
from CTFd.utils.user import get_current_user

workshop_api = Blueprint("workshop_api", __name__)


# Same guards as the page this feeds (page.py:491). Without them the DAG —
# every challenge name, category and edge — is readable before the CTF opens
# and when challenge visibility is set to admins only, which the board itself
# would refuse.
@workshop_api.route("/api/v1/workshop/graph", methods=["GET"])
@during_ctf_time_only
@check_challenge_visibility
@authed_only
def graph():
    user = get_current_user()
    solved = get_solve_ids_for_user_id(user.id)

    challenges = (
        Challenges.query.filter(Challenges.state != "hidden")
        .order_by(Challenges.position.is_(None), Challenges.position, Challenges.id)
        .all()
    )
    all_ids = {c.id for c in challenges}

    def prereqs(c):
        reqs = (c.requirements or {}).get("prerequisites", []) if c.requirements else []
        return [r for r in reqs if r in all_ids]

    def unlocked(c):
        return set(prereqs(c)).issubset(solved)

    nodes, next_map = [], {}
    for c in challenges:
        pr = prereqs(c)
        is_unlocked = set(pr).issubset(solved)
        visible = c.id in solved or is_unlocked
        nodes.append({
            "id": c.id,
            "name": c.name if visible else "???",
            "category": c.category if visible else "???",
            "position": c.position or 0,
            "solved": c.id in solved,
            "unlocked": is_unlocked,
            "prerequisites": pr,
        })
        for p in pr:
            next_map.setdefault(p, []).append(c.id)

    return jsonify({
        "success": True,
        "data": {"nodes": nodes, "next": next_map, "solved": sorted(solved)},
    })


def load_graph(app):
    app.register_blueprint(workshop_api)
