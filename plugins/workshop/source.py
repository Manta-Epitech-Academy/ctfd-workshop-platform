"""Where an instance's content comes from, and how it gets here (PLAN.md §26).

The workflow this serves: clone a subject repo, edit, push, open the admin
panel, press Sync. So the **repo is the source of truth** and the instance
fetches it — a tarball from GitHub, no git binary, no credentials for a public
repo.

    workshop_source     {"repo": "kevin-cazal/pypong_subject", "ref": "main"}
    workshop_last_sync  what the last import brought in, and from which commit

One shape covers both cases (§26.4): the fetched tree is a **subject** if its
root holds `subject.yaml` and a **workshop** if it holds `workshop.yaml`, so a
single-subject instance is a workshop of one and there is no second code path.

A workshop names its subjects by repo. `ref: submodule` means "the commit this
wrapper pins", which the API reports without git being involved — a tarball
carries submodule directories empty, but `contents/<path>` returns the pin.
Each subject is then fetched at that exact sha and the manifest is rewritten
with local paths, so `tools/sync_workshop.py` runs on it unchanged.
"""
import io
import json
import os
import re
import tarfile
import time

import requests

CONFIG_SOURCE = "workshop_source"
CONFIG_LAST = "workshop_last_sync"

GITHUB_API = "https://api.github.com"
# Public repos, so no token: 60 requests an hour per IP, against 2 for a
# subject and 5 for a workshop of two. If that ever becomes a limit, a token
# config key is the fix (PLAN.md §26.6).
HEADERS = {"Accept": "application/vnd.github+json",
           "User-Agent": "ctfd-workshop-plugin"}
TIMEOUT = 30
# A subject is a few hundred kilobytes; shell-1 with its images is under a
# megabyte. Anything far past that is not a subject repo.
MAX_TARBALL = 64 * 1024 * 1024

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class SourceError(Exception):
    """Something the admin should read, not a traceback."""


# --------------------------------------------------------------------------
# what this instance is set to sync
# --------------------------------------------------------------------------

def _json_config(key):
    # Imported here, not at module scope: everything below the config helpers
    # is plain GitHub plumbing, and keeping it importable outside a CTFd app is
    # what lets it be tested on its own.
    from CTFd.utils import get_config
    raw = get_config(key)
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def current_source():
    """{"repo", "ref"} — empty until provisioning or the page sets it."""
    source = _json_config(CONFIG_SOURCE)
    repo = (source.get("repo") or "").strip()
    return {"repo": repo, "ref": (source.get("ref") or "main").strip()}


def set_source(repo, ref):
    repo = (repo or "").strip()
    ref = (ref or "main").strip() or "main"
    from CTFd.utils import set_config
    if not REPO_RE.match(repo):
        raise SourceError(f"{repo!r} is not an owner/name repository")
    set_config(CONFIG_SOURCE, json.dumps({"repo": repo, "ref": ref}))
    return {"repo": repo, "ref": ref}


def last_sync():
    return _json_config(CONFIG_LAST)


def record_sync(**fields):
    from CTFd.utils import set_config
    record = {**last_sync(), **fields, "at": int(time.time())}
    set_config(CONFIG_LAST, json.dumps(record))
    return record


# --------------------------------------------------------------------------
# GitHub, read-only
# --------------------------------------------------------------------------

def _get(url, **kw):
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
    except requests.RequestException as exc:
        raise SourceError(f"could not reach GitHub: {exc}") from exc
    if r.status_code == 404:
        raise SourceError(f"not found on GitHub: {url.replace(GITHUB_API, '')}")
    if r.status_code == 403 and "rate limit" in r.text.lower():
        raise SourceError("GitHub rate limit reached for this server — "
                          "wait an hour, or sync from the command line")
    if r.status_code >= 400:
        raise SourceError(f"GitHub answered {r.status_code} for "
                          f"{url.replace(GITHUB_API, '')}")
    return r


def resolve_ref(repo, ref):
    """(sha, subject line) for a branch, a tag or a sha.

    Resolved before fetching so the import records the commit it actually took,
    not the name of a branch that has moved since.
    """
    data = _get(f"{GITHUB_API}/repos/{repo}/commits/{ref}").json()
    message = ((data.get("commit") or {}).get("message") or "").split("\n")[0]
    return data["sha"], message


def submodule_pin(repo, sha, path):
    """(repo, sha) a wrapper repo pins at `path`, without needing git.

    A GitHub tarball carries a submodule as an empty directory — verified on
    this platform's own repo — but the contents API reports the entry as
    `type: submodule` with the sha and the git URL, which is the pin.
    """
    data = _get(f"{GITHUB_API}/repos/{repo}/contents/{path}",
                params={"ref": sha}).json()
    if data.get("type") != "submodule":
        raise SourceError(f"{repo}: {path} is not a submodule, so `ref: submodule` "
                          f"has nothing to follow — name a branch or a tag")
    url = data.get("submodule_git_url") or ""
    match = re.search(r"github\.com[:/]+(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not match:
        raise SourceError(f"{path} points at {url or 'nothing'}, which is not a "
                          f"GitHub repository this instance can fetch")
    return match.group("repo"), data["sha"]


def _safe_extract(tar, dest):
    """Unpack an archive from the internet without letting it out of `dest`."""
    try:
        tar.extractall(dest, filter="data")     # 3.11.4+, and the image is 3.11
    except TypeError:
        for member in tar.getmembers():
            target = os.path.realpath(os.path.join(dest, member.name))
            if not target.startswith(os.path.realpath(dest) + os.sep):
                raise SourceError(f"archive member escapes the directory: {member.name}")
            if member.issym() or member.islnk():
                raise SourceError(f"archive contains a link: {member.name}")
        tar.extractall(dest)


def fetch_tree(repo, sha, dest):
    """Download `repo` at `sha` into `dest`; return the unpacked root."""
    url = f"{GITHUB_API}/repos/{repo}/tarball/{sha}"
    r = _get(url, stream=True)
    blob, size = io.BytesIO(), 0
    for chunk in r.iter_content(64 * 1024):
        size += len(chunk)
        if size > MAX_TARBALL:
            raise SourceError(f"{repo} is larger than {MAX_TARBALL // (1024 * 1024)} MB — "
                              f"that is not a subject repository")
        blob.write(chunk)
    blob.seek(0)
    os.makedirs(dest, exist_ok=True)
    try:
        with tarfile.open(fileobj=blob, mode="r:gz") as tar:
            _safe_extract(tar, dest)
    except tarfile.TarError as exc:
        raise SourceError(f"{repo}@{sha[:7]} is not a readable tar.gz: {exc}") from exc
    entries = [os.path.join(dest, e) for e in os.listdir(dest)]
    roots = [e for e in entries if os.path.isdir(e)]
    if len(roots) != 1:
        raise SourceError(f"{repo}@{sha[:7]} does not unpack to a single directory")
    return roots[0]


# --------------------------------------------------------------------------
# fetching what this instance is set to sync
# --------------------------------------------------------------------------

def sidecars_in(directory):
    """Encrypted answer files sitting in a subject, unopened.

    `flags.yaml.gpg` and `quiz_answers.yaml.gpg` (convention §3.3b, §3.7). The
    plaintext beside one means it is already open and nothing is needed.
    """
    found = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".yaml.gpg"):
            continue
        plain = name[:-4]
        if os.path.isfile(os.path.join(directory, plain)):
            continue
        found.append({"dir": directory, "name": plain, "file": name,
                      "path": os.path.join(directory, name)})
    return found


def materialize(repo, ref, dest, log=print):
    """Fetch a source into `dest` and describe what arrived.

    Returns {kind, dir, repo, sha, message, subjects[], sidecars[]} — `dir` is
    what to hand tools/sync_subject.py or tools/sync_workshop.py.
    """
    sha, message = resolve_ref(repo, ref)
    log(f"{repo}@{ref} -> {sha[:7]} {message}"[:200])
    root = fetch_tree(repo, sha, os.path.join(dest, "root"))

    if os.path.isfile(os.path.join(root, "subject.yaml")):
        log("a subject repository")
        return {"kind": "subject", "dir": root, "repo": repo, "sha": sha,
                "message": message,
                "subjects": [{"repo": repo, "sha": sha, "dir": root}],
                "sidecars": sidecars_in(root)}

    manifest_path = os.path.join(root, "workshop.yaml")
    if not os.path.isfile(manifest_path):
        raise SourceError(f"{repo} has neither subject.yaml nor workshop.yaml at its "
                          f"root, so it is not something this instance can import")

    import yaml  # vendored; imported here so a subject sync never needs it early
    manifest = yaml.safe_load(open(manifest_path).read()) or {}
    entries = manifest.get("subjects") or []
    if not entries:
        raise SourceError(f"{repo}: workshop.yaml lists no subjects")

    log(f"a workshop of {len(entries)} subjects")
    subjects, sidecars, rewritten = [], [], []
    for entry in entries:
        sub_repo = (entry.get("repo") or "").strip()
        if not sub_repo:
            raise SourceError(
                f"{repo}: the subject at {entry.get('path') or '?'} has no `repo:` — a "
                f"workshop fetched from GitHub must name each subject's repository "
                f"(a bare `path:` only works for the command line)")
        sub_ref = (entry.get("ref") or "main").strip()
        if sub_ref == "submodule":
            path = entry.get("path") or sub_repo.split("/")[-1]
            sub_repo_pinned, sub_sha = submodule_pin(repo, sha, path)
            if sub_repo_pinned != sub_repo:
                log(f"  note: {path} pins {sub_repo_pinned}, manifest says {sub_repo}")
            sub_repo, sub_ref_label = sub_repo_pinned, f"submodule {sub_sha[:7]}"
        else:
            sub_sha, _ = resolve_ref(sub_repo, sub_ref)
            sub_ref_label = f"{sub_ref} {sub_sha[:7]}"
        name = sub_repo.split("/")[-1]
        log(f"  {name}: {sub_ref_label}")
        sub_dir = fetch_tree(sub_repo, sub_sha, os.path.join(dest, name))
        if not os.path.isfile(os.path.join(sub_dir, "subject.yaml")):
            raise SourceError(f"{sub_repo}@{sub_sha[:7]} is not a subject repository")
        subjects.append({"repo": sub_repo, "sha": sub_sha, "dir": sub_dir, "name": name})
        sidecars += sidecars_in(sub_dir)
        rewritten.append({**{k: v for k, v in entry.items() if k != "path"},
                          "path": os.path.relpath(sub_dir, root)})

    # sync_workshop.py resolves `path` relative to the manifest and needs
    # nothing else, so the fetched tree is made to look like a vendored one.
    manifest["subjects"] = rewritten
    with open(manifest_path, "w") as fh:
        yaml.safe_dump(manifest, fh, allow_unicode=True, sort_keys=False)

    return {"kind": "workshop", "dir": root, "repo": repo, "sha": sha,
            "message": message, "subjects": subjects, "sidecars": sidecars}
