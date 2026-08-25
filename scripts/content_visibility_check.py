#!/usr/bin/env python3
"""Does the workshop page show everything that was authored, in source order?

    python3 scripts/content_visibility_check.py <base-url> <admin-pass> \
            content/pypong [content/other ...]

The bug this exists to catch (2026-08-25): a part's opening prose was carried
inside the first step's `body`, and `body` is withheld while a step is locked —
so the introduction to a part disappeared until the participant unlocked it.
Exercises routinely say "reuse the code above", so the step after it pointed at
something invisible.

Prose is not a statement. A locked step hides its statement, its hints and its
quiz, and shows its title (page.py); a part introduction belongs with the title.

Three things are checked per subject:

  prose     every authored prose block is on a participant page for a brand new
            account that has solved nothing, so nearly everything is locked
  complete  every authored block reached CTFd at all — prose on the page, step
            statements in the challenge description (which is gated on purpose,
            and is where a closing note lives)
  order     both the prose blocks and the steps appear in the order authored

A heading with no body under it is not checked as prose: it is rendered as the
part title, which is checked instead.

It registers a throwaway account, so point it at an instance you do not mind
gaining a user. It never solves anything and never needs an answer.
"""
import html
import re
import sys
import unicodedata
import uuid
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
from ws_parser import parse_subject  # noqa: E402

fails = []


def check(cond, label):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        fails.append(label)


def normalize(text):
    """Comparable plain text: no markup, no entities, one space between words."""
    text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[`*_#>\[\]()]", " ", text)
    text = re.sub(r"[‘’“”«»]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def page_text(source):
    body = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", source)
    return normalize(re.sub(r"(?s)<[^>]+>", " ", body))


LIST_MARK = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def probes(block, want=3):
    """Distinctive lines of a markdown block, longest first.

    Fenced code lines count: the reported case was a `padx = padx - 2` block
    vanishing, and that code is as much the lesson as the prose around it.

    The list marker goes first. Most workshop prose here is written as bullets,
    and the renderer turns `- ` into an <li>, so a probe that keeps it matches
    the markdown but never the page — which is a checker that reports every
    bulleted block as missing.
    """
    lines = [normalize(LIST_MARK.sub("", ln)) for ln in block.splitlines()]
    lines = sorted({ln for ln in lines if len(ln) >= 18}, key=len, reverse=True)
    if not lines:
        # A block of very short bullets is still content that must be found.
        lines = sorted({normalize(LIST_MARK.sub("", ln)) for ln in block.splitlines()
                        if len(normalize(LIST_MARK.sub("", ln))) >= 4},
                       key=len, reverse=True)
    return lines[:want]


def split_heading(block):
    """(heading, prose) — the parser prefixes a prose block with its own title."""
    lines = block.strip().splitlines()
    if lines and re.fullmatch(r"\*\*.+\*\*", lines[0].strip()):
        return lines[0].strip().strip("*"), "\n".join(lines[1:]).strip()
    return "", block.strip()


def participant(base, admin_pass):
    """An account that has solved nothing, so nearly everything is locked.

    A throwaway registration where the instance allows it. Production gates
    registration behind a code, and creating accounts on a live instance to run
    a check is the wrong trade — so there it falls back to the admin account,
    which has no solves either. Lock state is computed from the user's own
    solves (page.py:_steps), not from being an admin, so the page renders
    exactly as it does for a participant who has just arrived.
    """
    run = uuid.uuid4().hex[:8]
    s = requests.Session()
    reg = s.get(base + "/register", timeout=20)
    if re.search(r'name="registration_code"', reg.text):
        print("registration is code-gated; using the admin account "
              "(no solves, so the same lock state)")
        return admin(base, admin_pass)
    nonce = re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", reg.text).group(1)
    s.post(base + "/register", data={
        "name": f"vischeck{run}", "email": f"vischeck{run}@example.com",
        "password": f"vischeck-{run}", "nonce": nonce, "_submit": "Submit"},
        timeout=20)
    if "/login" in s.get(base + "/workshop", timeout=20).url:
        raise SystemExit("could not register a participant")
    return s


def admin(base, password):
    s = requests.Session()
    nonce = re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"",
                      s.get(base + "/login", timeout=20).text).group(1)
    s.post(base + "/login", data={"name": "admin", "password": password,
                                  "nonce": nonce}, timeout=20)
    return s


def challenges(session, base):
    """id -> (name, position, normalized description).

    Keyed by id rather than by name on purpose: starter1.md alone has three
    steps called "Sprite" and two called "Input" (CLAUDE.md), so a map keyed by
    name drops all but the last of each and then reports their statements as
    missing.
    """
    out = {}
    listing = session.get(base + "/api/v1/challenges?view=admin", timeout=30)
    for row in listing.json()["data"]:
        d = session.get(base + f"/api/v1/challenges/{row['id']}",
                        timeout=30).json()["data"]
        out[d["id"]] = (d["name"], d.get("position"),
                        normalize(d.get("description") or ""))
    return out


def locate(needles, haystacks):
    """First (key, offset) where any needle appears, or None."""
    for needle in needles:
        for key, text in haystacks.items():
            at = text.find(needle)
            if at >= 0:
                return key, at, needle
    return None


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    base, admin_pass, subject_dirs = sys.argv[1], sys.argv[2], sys.argv[3:]

    session = participant(base, admin_pass)
    top = session.get(base + "/workshop", timeout=30)
    routes = ["/workshop"] + list(dict.fromkeys(
        re.findall(r'href="(/workshop/[a-zA-Z0-9\-_]+)"', top.text)))
    raw_pages = {r: session.get(base + r, timeout=30).text for r in routes}
    pages = {r: page_text(t) for r, t in raw_pages.items()}
    print(f"participant pages: {', '.join(pages)}")

    catalog = challenges(admin(base, admin_pass), base)
    stored = {cid: desc for cid, (_n, _p, desc) in catalog.items()}
    print(f"challenges in the instance: {len(stored)}\n")

    for subject_dir in subject_dirs:
        print(f"== {subject_dir}")
        subject = parse_subject(subject_dir)

        prose_placed, missing_any = [], False
        for doc in subject.documents:
            for ex in doc.exercises:
                heading, prose = split_heading(ex.context_md)
                if heading:
                    # Rendered as the part title rather than as body text.
                    hit = locate([normalize(heading)], pages)
                    check(hit is not None,
                          f"part heading shown: {heading!r}")
                if not prose:
                    continue
                label = f"prose before {ex.slug!r}"
                hit = locate(probes(prose), pages)
                check(hit is not None, f"visible to a locked-out participant: {label}")
                if hit:
                    prose_placed.append((label, hit[0], hit[1]))
                else:
                    missing_any = True

            # A closing note is the outro step's own statement, so it is gated
            # like any statement. It must still have reached the instance.
            trailing = getattr(doc, "trailing_md", "")
            if trailing.strip():
                _, prose = split_heading(trailing)
                found = locate(probes(trailing), {**pages, **stored})
                check(found is not None,
                      f"closing note reached the instance ({Path(doc.path).name})")

            # Every step statement must be stored, whether or not it is shown.
            for ex in doc.exercises:
                if not ex.body_md.strip():
                    continue
                found = locate(probes(ex.body_md), stored)
                check(found is not None,
                      f"statement stored: {ex.slug!r}")

        by_route = {}
        for label, route, at in prose_placed:
            by_route.setdefault(route, []).append((label, at))
        for route, items in by_route.items():
            offsets = [at for _, at in items]
            if offsets == sorted(offsets):
                check(True, f"prose in source order on {route} "
                            f"({len(items)} block(s))")
            else:
                bad = [items[i][0] for i in range(1, len(offsets))
                       if offsets[i] < offsets[i - 1]]
                check(False, f"prose in source order on {route} "
                             f"(out of place: {', '.join(bad)})")

        # Steps: the page must list them in the order the sync gave them,
        # which is the order the source was written in. Matched on the step ids
        # the template stamps into the DOM, not on titles: a title can repeat
        # ("Sprite" three times in starter1.md) and can also collide with a
        # part heading ("Collision"), and either makes a text search report a
        # correctly ordered page as broken.
        for route in raw_pages:
            ids = [int(i) for i in
                   dict.fromkeys(re.findall(r'id="step-(\d+)"', raw_pages[route]))]
            if len(ids) < 2:
                continue
            positions = [catalog[i][1] for i in ids if i in catalog]
            positions = [p for p in positions if p is not None]
            check(positions == sorted(positions),
                  f"steps in board order on {route} ({len(ids)} step(s))")

        print()

    print("FAILED" if fails else "All authored content is present and in order")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
