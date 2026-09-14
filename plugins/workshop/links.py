"""Where a step lives, as a URL.

A step is a CTFd challenge, and a participant reaches it on a workshop page:
`/workshop/<document>#step-<id>` for a subject synced with per-document routes,
`/workshop#step-<id>` for one synced before they existed. Two places need to
build that — the page, so a "finish X first" note can link to X, and the graph
endpoint, so a Parcours node opens the step rather than CTFd's own challenge
board (PLAN.md §30).

They used to build it twice, and the second one did not build it at all: the
Parcours graph linked to `/challenges#<name>-<id>`, which is the board the
workshop view exists to replace. One function, so the two cannot drift, and one
module so the API does not have to import a view to get it.
"""
import json

from CTFd.utils import get_config


def documents():
    """The subject's documents, as written by the sync (`workshop_documents`).

    Each is one *part* of the workshop with its own route and navbar entry.
    Empty for a subject synced before per-document routes existed, in which
    case `/workshop` remains the only view — which is also the right answer for
    a single-document subject.
    """
    raw = get_config("workshop_documents")
    if not raw:
        return []
    try:
        docs = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return [d for d in docs if d.get("slug") and d.get("challenge_ids")]


def step_pages(docs=None):
    """challenge id -> the document slug whose page shows that step."""
    return {cid: d["slug"] for d in (docs if docs is not None else documents())
            for cid in d["challenge_ids"]}


def step_href(challenge_id, pages=None):
    """The page a step can be done on, anchored at the step.

    `pages` is `step_pages()`, passed in when the caller is already iterating so
    the config is read once rather than per step.
    """
    if pages is None:
        pages = step_pages()
    slug = pages.get(challenge_id)
    anchor = f"#step-{challenge_id}"
    return f"/workshop/{slug}{anchor}" if slug else f"/workshop{anchor}"
