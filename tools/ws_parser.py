"""Shared parser for subject repos (docs/CONTENT_CONVENTION.md).

Reads `subject.yaml` + its markdown documents and produces the structure the
platform imports and the CI linter validates — one parser for both, so what
CI accepts, the platform imports, by construction.

Model produced (see Subject):
  documents (in subject.yaml order)
    nodes: heading-anchored, typed by the `ws:` comment that follows the
           heading — `chapter` / `exercise`; unmarked headings are `prose`
    inline within a node body: hints (marker + <details> block),
           quizzes (marker + blockquote question + option list),
           a resume region (instructor-led summary)

Conventions implemented here:
- A `<!-- ws: ... -->` comment holds YAML (flow or block) and attaches to the
  nearest preceding heading; `type: hint` / `type: quiz` markers instead
  attach to the content block that follows them.
- Headings inside fenced code blocks are ignored.
- Prose content is not lost: each exercise carries `context_md`, the bodies of
  the prose/chapter nodes between the previous exercise and itself — so an
  imported exercise is self-sufficient reading (the Phase 0 lesson).
- Exercise `category`: title of the nearest enclosing chapter or *unmarked*
  heading. An explicit `type: prose` marker keeps a heading out of that
  structure, so a mid-chapter explanation does not rename the part.
- Quiz options: `- A. text` (single) / `* A. text` (multiple) /
  `- A. …` + `- a. …` rows (match), per workshop-metadata-tools QUIZ.md.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Regions fenced by an opening and a closing comment, rather than metadata
# attached to a heading. They carry content the platform lifts out and renders
# elsewhere — the author's short version above the statement, the toolbox and
# the glossary on the toolbox page — so the marker is a name, not a YAML
# mapping, and the metadata parser has to keep its hands off it.
FENCED_REGIONS = ("resume", "toolbox", "glossary")
WS_COMMENT = re.compile(
    r"<!--\s*ws:(?!(?:%s)\b)(.*?)-->" % "|".join(FENCED_REGIONS), re.DOTALL)
RESUME_OPEN = "<!-- ws:resume -->"
RESUME_CLOSE = "<!-- /ws:resume -->"
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
FENCE = re.compile(r"^(```|~~~)")
OPTION = re.compile(r"^([-*])\s+([A-Za-z])\.\s+(.*)$")
# Inline images: markdown `![alt](path)` and raw `<img src="path">`. Only the
# path is captured; whether it is repo-relative is decided by _is_local_asset.
IMAGE_MD = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
IMAGE_HTML = re.compile(r"<img\b[^>]*?\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


@dataclass
class Quiz:
    id: str
    kind: str
    points: int
    question: str
    items: list          # [{"letter": ..., "text": ...}] — single/multiple
    left: list           # match only
    right: list          # match only
    category: str = ""
    host_exercise: str = ""   # slug of the exercise it appeared under, if any
    order: int = 0            # source-reading position across the whole subject


@dataclass
class Hint:
    content_md: str
    cost: int = 0


@dataclass
class Chapter:
    """A `type: chapter` heading — the scope a topology applies to.

    Chapters are not challenges and never become one. They exist so `topology`
    has something to sit on: `linear` chains their exercises, `free` unlocks all
    of them at once (CONTENT_CONVENTION §3.3). A document with no chapter marker
    behaves as one implicit linear chapter, which is what every subject written
    before topologies existed relies on.
    """
    slug: str
    title: str
    topology: str = "linear"
    exercises: list = field(default_factory=list)   # slugs, in source order


@dataclass
class Exercise:
    slug: str
    title: str
    category: str
    points: int
    body_md: str          # own body, hint/quiz/resume markup stripped
    context_md: str       # preceding prose since the last exercise
    resume_md: str        # instructor-led summary ("" if none)
    hints: list = field(default_factory=list)
    quizzes: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)   # raw ws fields (review, ...)
    # How this step is validated (CONTENT_CONVENTION §3.2/§3.3):
    #   checkpoint  the instructor reads out a per-exercise code (the default)
    #   flag        the answer IS the flag, authored in flags.yaml — the
    #               participant discovers it by doing the task
    #   token       the runtime reveals a token it derives from the session's
    #               secret and `token_id`; the sync derives the same value
    # `tests` and `review` are specified and not implemented.
    validation: str = "checkpoint"
    # `validation: token` only: the id the runtime derives its token from — the
    # exercise's own id inside that runtime, not this subject's slug.
    token_id: str = ""
    order: int = 0            # source-reading position across the whole subject
    chapter: str = ""         # slug of the enclosing chapter ("" = document-level)
    requires: list = field(default_factory=list)   # explicit prerequisite slugs
    # Bonus work: unlocked like its neighbours, but it gates nothing and counts
    # towards nothing. Without this a subject with optional steps can never
    # reach 100%, and 100% is what gets the end-of-workshop feedback in.
    optional: bool = False


@dataclass
class Document:
    path: str
    title: str            # first H1, or the filename
    body_md: str          # full doc, ws markup stripped (for page rendering)
    exercises: list = field(default_factory=list)
    quizzes: list = field(default_factory=list)   # quizzes outside any exercise
    chapters: list = field(default_factory=list)  # Chapter, in source order
    # Prose that follows the last exercise (bonus / "going further" / credits).
    # It is nobody's exercise context, so without this it would be dropped.
    trailing_md: str = ""
    trailing_title: str = ""
    cover: dict = field(default_factory=dict)     # §3.2b, {} when undeclared


@dataclass
class Asset:
    """An image the content references by a repo-relative path.

    `ref` is the path exactly as written in the markdown, because that is what
    the importer has to substitute; `path` is where it lives on disk.

    `key` identifies the FILE, which is not the same thing as the path written
    to reach it: `img/x.gif` in subject.yaml and `img/x.gif` inside
    `docs/part1.md` resolve from different roots and are two different files.
    It is what the `assets` dict is keyed on and what the importer's upload
    mapping is keyed on, so a lookup cannot drift from the entry it is looking
    for. A markdown ref is its own key — that is what `rewrite_asset_refs`
    substitutes on — and a cover ref is namespaced by `cover_asset_key`.
    """
    ref: str
    path: Path
    documents: list = field(default_factory=list)   # docs that reference it
    key: str = ""

    def __post_init__(self):
        if not self.key:
            self.key = self.ref

    @property
    def in_markdown(self):
        """True for an image referenced from a document body.

        Only these are substituted by `rewrite_asset_refs`, which matches on the
        path as written; a cover is a plain dict value and goes through
        `rewrite_cover_refs` instead.
        """
        return self.key == self.ref

    @property
    def exists(self):
        return self.path.is_file()


@dataclass
class Subject:
    slug: str
    name: str
    manifest: dict
    documents: list = field(default_factory=list)
    assets: list = field(default_factory=list)      # Asset, in first-seen order
    cover: dict = field(default_factory=dict)       # §3.2b, {} when undeclared

    @property
    def exercises(self):
        return [e for d in self.documents for e in d.exercises]

    @property
    def quizzes(self):
        out = [q for d in self.documents for q in d.quizzes]
        out += [q for e in self.exercises for q in e.quizzes]
        return out


class ParseError(Exception):
    pass


def _parse_ws_yaml(raw, where):
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ParseError(f"{where}: invalid YAML in ws comment: {e}")
    if not isinstance(data, dict):
        raise ParseError(f"{where}: ws comment must be a YAML mapping")
    return data


def slugify(text):
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE)
    return re.sub(r"[\s_]+", "-", text).strip("-")


def _is_local_asset(ref):
    """True for a repo-relative path, false for anything already resolvable.

    Absolute URLs, protocol-relative URLs, data URIs and site-absolute paths are
    left alone: they either already work in CTFd or are deliberately external.
    """
    if not ref or ref.startswith(("http://", "https://", "//", "data:", "/", "#")):
        return False
    return True


def _strip_fences(text):
    """Blank out fenced code blocks so examples in them are not scanned."""
    out, in_fence = [], False
    for line in text.split("\n"):
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def find_asset_refs(text):
    """Repo-relative image paths referenced by a markdown document, in order."""
    scannable = _strip_fences(text)
    refs = []
    for pattern in (IMAGE_MD, IMAGE_HTML):
        for m in pattern.finditer(scannable):
            ref = m.group(1).strip()
            if _is_local_asset(ref) and ref not in refs:
                refs.append(ref)
    return refs


COVER_ASSET_KEYS = ("media", "poster", "mascot")
# A cover path is namespaced so it cannot collide with an identical-looking path
# written inside a document. The two resolve differently (see cover_asset_refs),
# so `img/x.gif` in subject.yaml and `img/x.gif` inside `docs/part1.md` are two
# files; sharing one key would upload one of them under the other's URL.
COVER_REF_PREFIX = "subject.yaml:"


def cover_asset_key(ref, document=None):
    """The `Asset.key` of a cover image, and therefore its upload-mapping key.

    One function because the key is written by `parse_subject` and read back by
    `rewrite_cover_refs`, through a mapping built in another module entirely
    (tools/sync_subject.py). Spelled twice, the two spellings drift and every
    cover silently keeps its unresolved repo path.
    """
    return COVER_REF_PREFIX + (document + ":" if document else "") + ref


def cover_asset_refs(cover):
    """The local image paths a `cover:` block references, in declaration order.

    **These resolve against the subject root, not against a document.** §3.8's
    rule is "relative to the document that uses it", which a manifest has no
    document to be relative to. Today the two coincide for every converted
    subject because their documents sit at the repo root; they stop coinciding
    the day one puts its markdown in a subdirectory, so the difference is stated
    here rather than discovered then.
    """
    return [cover[k] for k in COVER_ASSET_KEYS
            if isinstance(cover.get(k), str) and _is_local_asset(cover[k])]


def rewrite_cover_refs(cover, mapping, document=None):
    """Point a cover's image paths at their uploaded URLs.

    Not `rewrite_asset_refs`: that is a regex over markdown image syntax, and
    these are plain dict values. Same contract though — a path with no mapping
    is left exactly as written rather than dropped.

    `mapping` is keyed on `Asset.key`, so the lookup goes through
    `cover_asset_key` — the same function `parse_subject` keyed the entry with.
    `document` names the document a per-part cover was declared in, which is
    part of that key because the two resolve from different roots.
    """
    if not cover:
        return cover
    out = dict(cover)
    for field_name in COVER_ASSET_KEYS:
        ref = out.get(field_name)
        if isinstance(ref, str) and _is_local_asset(ref):
            out[field_name] = mapping.get(cover_asset_key(ref, document), ref)
    return out


def rewrite_asset_refs(md, mapping):
    """Replace repo-relative image paths with their resolved URLs.

    Substitution is on the captured path only, so alt text and titles survive
    untouched — and a path with no mapping is left exactly as written rather
    than silently dropped.
    """
    if not md or not mapping:
        return md

    def sub(m):
        ref = m.group(1).strip()
        target = mapping.get(ref)
        if not target:
            return m.group(0)
        return m.group(0)[:m.start(1) - m.start(0)] + target + m.group(0)[m.end(1) - m.start(0):]

    for pattern in (IMAGE_MD, IMAGE_HTML):
        md = pattern.sub(sub, md)
    return md


def _split_sections(text, where):
    """Split a document into (heading_level, title, ws_meta, body_lines) tuples.
    The leading content before any heading gets level 0."""
    sections = [[0, "", None, []]]
    in_fence = False
    for line in text.split("\n"):
        if FENCE.match(line.strip()):
            in_fence = not in_fence
            sections[-1][3].append(line)
            continue
        m = HEADING.match(line) if not in_fence else None
        if m:
            sections.append([len(m.group(1)), m.group(2), None, []])
        else:
            sections[-1][3].append(line)
    # extract the ws comment that *starts* a section body (attaches to heading)
    out = []
    for level, title, _, body in sections:
        body_text = "\n".join(body)
        meta = None
        m = WS_COMMENT.match(body_text.lstrip())
        if m and body_text.lstrip().startswith("<!--"):
            data = _parse_ws_yaml(m.group(1), f"{where} § {title!r}")
            # `type` names a node; `cover` decorates one without being one, so a
            # document's H1 can carry a cover with no type at all. Anything the
            # convention does not recognise is left in the body untouched,
            # which is what keeps a stray HTML comment from being eaten.
            if data.get("type") in ("chapter", "exercise", "prose") or "cover" in data:
                meta = data
                lead = body_text.lstrip()
                body_text = lead[m.end():]
        out.append((level, title, meta, body_text))
    return out


def _extract_resume(body, where):
    if RESUME_OPEN not in body:
        return body, ""
    pre, rest = body.split(RESUME_OPEN, 1)
    if RESUME_CLOSE not in rest:
        raise ParseError(f"{where}: ws:resume region never closed")
    resume, post = rest.split(RESUME_CLOSE, 1)
    return pre + post, resume.strip()


def _parse_quiz_body(lines, i, where):
    """Parse blockquote question + option list starting at lines[i]."""
    question = []
    while i < len(lines) and lines[i].lstrip().startswith(">"):
        question.append(lines[i].lstrip()[1:].strip())
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    items, bullets = [], []
    while i < len(lines):
        m = OPTION.match(lines[i].strip())
        if not m:
            break
        bullets.append(m.group(1))
        items.append({"letter": m.group(2), "text": m.group(3).strip()})
        i += 1
    if not question or not items:
        raise ParseError(f"{where}: quiz marker without question/options")
    return " ".join(question), items, i


def _extract_inline(body, category, host_slug, defaults, where):
    """Pull hint and quiz markers (with their blocks) out of a section body.
    Returns (clean_body, hints, quizzes)."""
    hints, quizzes = [], []
    lines = body.split("\n")
    out = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        m = WS_COMMENT.match(stripped)
        if m and stripped.startswith("<!--"):
            data = _parse_ws_yaml(m.group(1), where)
            kind = data.get("type")
            if kind == "hint":
                # consume the following <details> block
                j = i + 1
                while j < len(lines) and not lines[j].strip():
                    j += 1
                if j >= len(lines) or not lines[j].strip().startswith("<details"):
                    raise ParseError(f"{where}: hint marker without <details> block")
                block = []
                while j < len(lines):
                    block.append(lines[j])
                    if "</details>" in lines[j]:
                        break
                    j += 1
                else:
                    raise ParseError(f"{where}: <details> never closed")
                content = "\n".join(block)
                content = re.sub(r"</?details>|<summary>.*?</summary>", "", content)
                hints.append(Hint(content_md=content.strip(), cost=int(data.get("cost", 0))))
                i = j + 1
                continue
            if kind == "quiz":
                question, items, next_i = _parse_quiz_body(lines, i + 1, where)
                q = Quiz(
                    id=data["id"], kind=data.get("kind", "single"),
                    points=int(data.get("points", defaults["points"])),
                    question=question, items=[], left=[], right=[],
                    category=category, host_exercise=host_slug,
                )
                if q.kind == "match":
                    q.left = [it for it in items if it["letter"].isupper()]
                    q.right = [it for it in items if it["letter"].islower()]
                else:
                    q.items = items
                quizzes.append(q)
                i = next_i
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).strip(), hints, quizzes


TOPOLOGIES = ("linear", "free")
VALIDATIONS = ("checkpoint", "flag", "token", "tests", "review")
IMPLEMENTED_VALIDATIONS = ("checkpoint", "flag", "token")


def _topology_of(meta, where):
    topology = meta.get("topology", "linear")
    if topology not in TOPOLOGIES:
        raise ParseError(f"{where}: topology {topology!r} is not one of "
                         f"{', '.join(TOPOLOGIES)}")
    return topology


def _requires_of(meta, where):
    """`requires` as a list of slugs. A bare string is accepted as one entry."""
    raw = meta.get("requires")
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not all(isinstance(r, str) for r in raw):
        raise ParseError(f"{where}: requires must be a slug or a list of slugs")
    return raw


def _validation_of(meta, defaults, where):
    """What proves this step is done, falling back to the subject's default."""
    value = meta.get("validation", defaults["validation"])
    if value not in VALIDATIONS:
        raise ParseError(f"{where}: validation {value!r} is not one of "
                         f"{', '.join(VALIDATIONS)}")
    return value


def parse_subject(subject_dir):
    subject_dir = Path(subject_dir)
    manifest = yaml.safe_load((subject_dir / "subject.yaml").read_text())
    project = manifest["project"]
    defaults = {
        "points": manifest.get("platform", {}).get("points_default", 25),
        "validation": manifest.get("platform", {}).get("validation_default",
                                                       "checkpoint"),
    }
    cover = manifest.get("cover") or {}
    subject = Subject(slug=project["slug"], name=project["name"],
                      manifest=manifest, cover=cover)

    order = 0  # subject-wide source-reading counter (1-based positions)

    assets = {}   # Asset.key -> Asset, first-seen order (dicts keep insertion)

    def collect(asset):
        """Record an asset under its own key, first-seen winning."""
        return assets.setdefault(asset.key, asset)

    # The cover's images go in FIRST, before the document loop, for two reasons:
    # a subject whose only image is its cover still has to collect one, and
    # being first-seen keeps it at the head of `subject.assets` where a caller
    # deriving a fallback cover can find it. `documents` names subject.yaml
    # rather than being left empty — `lint` reports `a.documents[0]` for a
    # missing file, and an empty list would raise IndexError on exactly the
    # typo that check exists to catch.
    for ref in cover_asset_refs(cover):
        collect(Asset(ref=ref, path=subject_dir / ref, documents=["subject.yaml"],
                      key=cover_asset_key(ref)))
    # Subject-wide, not per document: the importer keys challenges on
    # `ws:<subject>:<slug>`, so the same slug in two documents would upsert one
    # challenge twice and silently lose a step.
    seen_slugs = {}

    for doc_path in manifest.get("documents", []):
        where = doc_path
        text = (subject_dir / doc_path).read_text()

        # Images are collected from the raw document: they may sit in a body, a
        # hint or trailing prose, and every one of those ends up in CTFd.
        for ref in find_asset_refs(text):
            # Relative to the document, which is how a markdown renderer
            # resolves it — not to the repo root. A markdown ref is its own
            # key: that is the string `rewrite_asset_refs` substitutes on.
            asset = collect(Asset(ref=ref,
                                  path=(subject_dir / doc_path).parent / ref))
            asset.documents.append(doc_path)

        sections = _split_sections(text, where)

        doc_title = next((t for lv, t, _, _ in sections if lv == 1), doc_path)
        # A `cover:` under the document's own H1 marker, for a part that is a
        # different promise from the subject's front page. Optional: without it
        # the part falls back to the subject's, which is always at least
        # derived.
        doc_meta = next((m for lv, _, m, _ in sections if lv == 1 and m), None)
        doc_cover = (doc_meta or {}).get("cover") or {}
        for ref in cover_asset_refs(doc_cover):
            # Relative to the document, like every other path written inside
            # one — unlike subject.yaml's, which has no document to be relative
            # to. That difference is exactly what the key namespaces.
            collect(Asset(ref=ref,
                          path=(subject_dir / doc_path).parent / ref,
                          documents=[doc_path],
                          key=cover_asset_key(ref, doc_path)))
        doc = Document(path=doc_path, title=doc_title, body_md="",
                       cover=doc_cover)
        page_parts = []          # doc body with ws markup stripped
        context_parts = []       # prose accumulated since the last exercise
        trailing_parts = []      # same prose, real headings — becomes a page if
                                 # it survives to the end (i.e. no exercise consumed it)
        category = doc_title
        # The node a deeper unmarked heading belongs to. §3.3 promises
        # "everything between two markers belongs to the preceding node", so a
        # `### Concept` under a `## Étape` marked as an exercise is part of that
        # exercise, not prose that leaks into the next one's context.
        open_ex, open_level = None, 0
        # The chapter exercises currently fall into. A document that marks none
        # gets one implicit linear chapter, so nothing written before topologies
        # existed changes shape. `open_chapter_level` closes it when a heading at
        # the same depth or shallower arrives.
        chapter, chapter_level = None, 0

        for level, title, meta, body in sections:
            node_type = (meta or {}).get("type", "prose" if level else None)
            wtitle = f"{where} § {title!r}"
            body, resume = _extract_resume(body, wtitle)

            if open_ex is not None and meta is None and level > open_level:
                clean, hints, quizzes = _extract_inline(
                    body, category, open_ex.slug, defaults, wtitle)
                open_ex.body_md = (open_ex.body_md
                                   + f"\n\n{'#' * level} {title}\n\n{clean}").strip()
                open_ex.hints.extend(hints)
                for q in quizzes:
                    q.host_exercise = open_ex.slug
                    order += 1
                    q.order = order
                open_ex.quizzes.extend(quizzes)
                if resume:
                    open_ex.resume_md = (open_ex.resume_md + "\n" + resume).strip()
                page_parts.append(f"{'#' * level} {title}\n\n{clean}".strip())
                continue
            open_ex, open_level = None, 0

            # A heading at the same depth or shallower ends the open chapter.
            if chapter is not None and level and level <= chapter_level:
                chapter, chapter_level = None, 0

            if node_type == "exercise":
                clean, hints, quizzes = _extract_inline(
                    body, category, meta.get("id", ""), defaults, wtitle)
                slug = meta.get("id") or slugify(f"{category}-{title}")
                if slug in seen_slugs:
                    raise ParseError(f"{wtitle}: duplicate slug {slug!r} "
                                     f"(already used in {seen_slugs[slug]})")
                seen_slugs[slug] = wtitle
                order += 1
                ex = Exercise(
                    slug=slug, title=title, category=category,
                    points=int(meta.get("points", defaults["points"])),
                    body_md=clean, context_md="\n\n".join(context_parts).strip(),
                    resume_md=resume, hints=hints, quizzes=quizzes, meta=meta,
                    order=order,
                    chapter=chapter.slug if chapter else "",
                    requires=_requires_of(meta, wtitle),
                    optional=bool(meta.get("optional", False)),
                    validation=_validation_of(meta, defaults, wtitle),
                    token_id=str(meta.get("token_id", "")),
                )
                if chapter is not None:
                    chapter.exercises.append(slug)
                for q in quizzes:      # hosted quizzes read right after the exercise body
                    q.host_exercise = slug
                    order += 1
                    q.order = order
                doc.exercises.append(ex)
                open_ex, open_level = ex, level
                context_parts = []
                trailing_parts = []   # consumed as this exercise's context
                page_parts.append(f"{'#' * level} {title}\n\n{clean}")
            else:
                # chapter or prose: contributes context and page content
                clean, hints, quizzes = _extract_inline(
                    body, category or doc_title, "", defaults, wtitle)
                if hints:
                    raise ParseError(f"{wtitle}: hint marker outside an exercise")
                for q in quizzes:
                    order += 1
                    q.order = order
                doc.quizzes.extend(quizzes)
                if node_type == "chapter":
                    chapter = Chapter(
                        # Scoped by the document, not by the title above it: an
                        # H1 chapter would otherwise slug as "<title>-<title>".
                        slug=meta.get("id") or slugify(f"{Path(doc_path).stem}-{title}"),
                        title=title,
                        topology=_topology_of(meta, wtitle),
                    )
                    chapter_level = level
                    doc.chapters.append(chapter)
                # What opens a part: a chapter, or an *unmarked* heading. An
                # explicit `type: prose` marker means "a heading inside the
                # current part" — the only way to write a mid-chapter
                # explanation without silently renaming the part around it
                # (CONTENT_CONVENTION §3.3). Unmarked stays as it was, because
                # that is where every existing subject's parts come from.
                if level and level < 3 and (node_type == "chapter" or meta is None):
                    category = title
                heading = f"{'#' * level} {title}\n\n" if level else ""
                if clean or level:
                    page_parts.append(f"{heading}{clean}".strip())
                    piece = (f"**{title}**\n\n{clean}" if level else clean).strip()
                    if piece:
                        context_parts.append(piece)
                        trailing_parts.append(f"{heading}{clean}".strip())

        doc.body_md = "\n\n".join(p for p in page_parts if p).strip()
        # Trailing prose only matters when the document has exercises (otherwise
        # the whole body is the page already, e.g. the entrypoint intro).
        if doc.exercises and trailing_parts:
            doc.trailing_md = "\n\n".join(trailing_parts).strip()
            first = next((t for lv, t, _, _ in sections
                          if lv and f"{'#' * lv} {t}" in doc.trailing_md), "")
            doc.trailing_title = first
        subject.documents.append(doc)

    subject.assets = list(assets.values())
    return subject


def load_quiz_answers(subject_dir):
    path = Path(subject_dir) / "quiz_answers.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()).get("answers", {})


def load_flags(subject_dir):
    """Authored answers for `validation: flag` exercises, keyed by exercise id.

    A sidecar rather than the markdown, for the obvious reason: a flag printed
    next to its own exercise is not a flag. Same shape as quiz_answers.yaml, and
    the same handling — a public subject repo encrypts or ignores it.

        flags:
          011_mkdir: "shell1{mkdir ok}"
          019_livrable: {value: "shell1{...}", case_insensitive: false}
    """
    path = Path(subject_dir) / "flags.yaml"
    if not path.exists():
        return {}
    return (yaml.safe_load(path.read_text()) or {}).get("flags", {})


# A tagline is the one line a participant reads before anything else, so it is a
# headline and not a paragraph. The ceiling is editorial, not measured: this
# parser is shared, the band that renders a tagline lives in the platform and
# not here, and a constant that claimed to know that band's pixel width would be
# wrong the first time the band changed. Ninety characters is one sentence, and
# about one line at the width the platform gives it today.
TAGLINE_MAX = 90


def _cover_warnings(subject):
    """Advice about the `cover:` block. Never fatal — see `lint_all`.

    One message per situation, deliberately. An earlier version reported the
    missing tagline *and* the missing block for a subject that declares no cover
    at all — which is every subject not yet converted — so the common case
    produced two overlapping paragraphs on every CI run. That is how a warning
    teaches people to skip warnings, and it would have taken the three
    actionable ones down with it.
    """
    cover = subject.cover
    if not cover:
        return ["subject.yaml: no `cover:` block. The platform falls back to "
                "`project.summary` and the first image of the entrypoint "
                "document, which beats nothing and loses to a frame you chose. "
                "One sentence and one path is the whole block (§3.2b)."]

    out = []
    tagline = cover.get("tagline")
    if not tagline:
        out.append("subject.yaml: `cover:` declares no `tagline`, so the front "
                   "page falls back to `project.summary`. One sentence saying "
                   "what the participant will have built is the cheapest thing "
                   "you can add (§3.2b).")
    elif len(tagline) > TAGLINE_MAX:
        out.append(f"subject.yaml: `cover.tagline` is {len(tagline)} "
                   f"characters. Keep it under {TAGLINE_MAX} — it is a "
                   f"headline, not a paragraph.")

    media = cover.get("media")
    if isinstance(media, str) and media.lower().endswith(".gif") \
            and not cover.get("poster"):
        out.append("subject.yaml: `cover.media` is an animated GIF with no "
                   "`cover.poster`. A GIF cannot be paused, so a participant "
                   "who asked their system for reduced motion gets the "
                   "animation anyway; the poster is the still shown instead.")
    return out


def lint_all(subject_dir):
    """Returns (problems, warnings).

    A problem refuses the subject; a warning is advice and refuses nothing.
    The split exists because "your cover has no tagline" must not be able to
    stop a workshop importing mid-session, while still being said somewhere a
    CI run will show it.

    `lint` keeps its original one-list contract and is still what every caller
    uses, including subject repos' own CI through workshop-content-tools.
    Changing its return type would have broken all of them at once — and this
    repo's own `__main__` and regression suite compare it against `[]`.
    """
    problems, subject = _lint_problems(subject_dir)
    if problems or subject is None:
        # Nothing parsed, or parsed and failed: advice about a cover would be
        # noise next to a real error, and may not even be computable.
        return problems, []
    return problems, _cover_warnings(subject)


def lint(subject_dir):
    """Returns a list of problems; empty list = valid. Shared CI entrypoint."""
    return _lint_problems(subject_dir)[0]


def _lint_problems(subject_dir):
    """(problems, subject) — the subject is None when nothing parsed.

    Handing the parsed subject back is what lets `lint_all` add its advice
    without paying for a second parse.
    """
    problems = []
    try:
        subject = parse_subject(subject_dir)
    except (ParseError, KeyError, OSError, yaml.YAMLError) as e:
        return [str(e)], None
    answers = load_quiz_answers(subject_dir)
    flags = load_flags(subject_dir)
    # The importer resolves `project.entrypoint` against these documents to build
    # the index page and the opening step, and it does so after the images have
    # been uploaded — in a workshop, after the earlier subjects have been
    # imported whole. Checked here it costs a lint run instead of an instance in
    # a state nobody asked for. Nothing else checks the pairing: every other
    # check reads one document on its own.
    entry = (subject.manifest.get("project") or {}).get("entrypoint")
    declared = [d.path for d in subject.documents]
    if not entry:
        problems.append("subject.yaml: no `project.entrypoint`. It names the "
                        "document a participant reads first, which becomes the "
                        "front page and the first step.")
    elif entry not in declared:
        problems.append(f"subject.yaml: `project.entrypoint` is {entry!r}, which is "
                        f"not one of `documents:` "
                        f"({', '.join(declared) or 'none'})")
    for ex in subject.exercises:
        if ex.validation not in IMPLEMENTED_VALIDATIONS:
            problems.append(f"exercise {ex.slug!r}: validation {ex.validation!r} is "
                            f"specified but not implemented")
        elif ex.validation == "token" and not ex.token_id:
            problems.append(f"exercise {ex.slug!r}: validation: token, but no "
                            f"`token_id` for the runtime to derive it from")
        elif ex.validation == "flag" and not flags.get(ex.slug):
            # The answer is the flag, so a missing one is not a small gap: the
            # step would import with no way to solve it.
            problems.append(f"exercise {ex.slug!r}: validation: flag, but no entry "
                            f"in flags.yaml")
    for slug in flags:
        if slug not in {e.slug for e in subject.exercises}:
            problems.append(f"flags.yaml: {slug!r} matches no exercise")
    for a in subject.assets:
        if not a.exists:
            problems.append(f"{a.documents[0]}: image {a.ref!r} does not exist")
    for q in subject.quizzes:
        if q.id not in answers:
            problems.append(f"quiz {q.id!r}: no entry in quiz_answers.yaml")
        elif q.kind == "freeform":
            entry = answers[q.id]
            patterns = entry.get("patterns", entry if isinstance(entry, list) else [])
            for p in patterns:
                try:
                    re.compile(p)
                except re.error as e:
                    problems.append(f"quiz {q.id!r}: bad regex {p!r}: {e}")
    for a in answers:
        if a not in [q.id for q in subject.quizzes]:
            problems.append(f"quiz_answers.yaml: {a!r} matches no quiz marker")
    problems.extend(_lint_prerequisites(subject))
    return problems, subject


def _lint_prerequisites(subject):
    """`requires` targets resolve, and the graph they build has no cycle.

    Slugs are derived from headings unless an explicit `id:` is set, so a
    cross-chapter `requires` breaks silently the day somebody rewords a
    heading. This is the check that makes it loud (CONTENT_CONVENTION §3.9).
    """
    problems = []
    known = {e.slug: e for e in subject.exercises}
    for ex in subject.exercises:
        for target in ex.requires:
            if target not in known:
                problems.append(
                    f"exercise {ex.slug!r}: requires {target!r}, which is not "
                    f"an exercise in this subject")
            elif target == ex.slug:
                problems.append(f"exercise {ex.slug!r}: requires itself")

    # Depth-first cycle detection over the explicit edges only. The implicit
    # linear chain cannot cycle — it follows source order.
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {s: WHITE for s in known}

    def walk(slug, trail):
        colour[slug] = GREY
        for target in known[slug].requires:
            if target not in known:
                continue
            if colour[target] == GREY:
                cycle = " -> ".join(trail + [slug, target])
                problems.append(f"requires cycle: {cycle}")
            elif colour[target] == WHITE:
                walk(target, trail + [slug])
        colour[slug] = BLACK

    for slug in known:
        if colour[slug] == WHITE:
            walk(slug, [])
    return problems


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    issues, advice = lint_all(target)
    if issues:
        print("\n".join(f"FAIL {i}" for i in issues))
        sys.exit(1)
    for a in advice:
        print(f"warn {a}")
    s = parse_subject(target)
    print(f"ok {s.slug}: {len(s.documents)} documents, "
          f"{len(s.exercises)} exercises, {len(s.quizzes)} quizzes, "
          f"{len(s.assets)} images")
