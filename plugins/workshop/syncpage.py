"""The admin sync page (PLAN.md §26): fetch the subject repo, import it.

    GET  /admin/workshop/sync           the page
    POST /admin/workshop/sync/source    what this instance follows
    POST /api/v1/workshop/sync          start a job
    GET  /api/v1/workshop/sync          how it is going
    POST /api/v1/workshop/sync/answers  plaintext answers, decrypted in the
                                        browser (§26.3)

One job at a time, in a background thread — which is a greenlet here, since the
worker class is gevent, so a two-minute import does not block the instance and
may call the instance's own API. That is also why the job authenticates over
HTTP to 127.0.0.1 with the preset admin token instead of reaching into the ORM:
the importer is `tools/sync_subject.py`, unchanged, the same one the CLI runs.

The job **lints before it touches the instance**. A push that does not parse
fails on this page with the linter's own message, rather than half-way through
an import. (`sync()` calls `sys.exit` on lint problems, which in a thread is a
`SystemExit` nobody would see, so the lint is run here first and its exit is
caught anyway.)

State lives in this module rather than the database: one worker, one job, and a
job that did not survive a restart is not worth resuming. What does persist is
the record of the last import, in `workshop_last_sync`.
"""
import base64
import os
import shutil
import sys
import tempfile
import threading
import time

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from CTFd.utils.decorators import admins_only
from CTFd.utils.user import get_current_user

from .source import (SourceError, current_source, last_sync, materialize,
                     record_sync, resolve_ref, set_source)

workshop_sync = Blueprint("workshop_sync", __name__, template_folder="templates")

SELF_URL = os.environ.get("WORKSHOP_SELF_URL") or "http://127.0.0.1:8000"
VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
TOOLS = os.environ.get("WORKSHOP_TOOLS") or "/opt/workshop/tools"
# How long a job parked on "needs the passphrase" waits for the browser before
# giving up and cleaning its temp directory.
ANSWERS_TIMEOUT = 15 * 60
# The tip of the ref, cached briefly: the page asks GitHub on every view, and
# an admin refreshing while they wait should not spend the hourly allowance.
TIP_TTL = 60

_job = None
_job_lock = threading.Lock()
_tip_cache = {}


def missing_pieces():
    """What this instance lacks for the page to work, in words an admin can act on."""
    problems = []
    if not os.path.isdir(os.path.join(VENDOR, "yaml")):
        problems.append("PyYAML is not vendored — run tools/build_vendor.sh on the server")
    if not os.path.isfile(os.path.join(TOOLS, "sync_subject.py")):
        problems.append(f"the platform's tools are not mounted at {TOOLS} — "
                        f"add the mount from docker-compose.yml and re-create the container")
    if not os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "assets", "vendor", "openpgp.min.mjs")):
        problems.append("openpgp.js is not vendored — run tools/build_vendor.sh "
                        "(only needed for a subject whose answers are encrypted)")
    if not (os.environ.get("PRESET_ADMIN_TOKEN") or os.environ.get("PRESET_ADMIN_PASSWORD")):
        problems.append("this instance has no preset admin credentials, so the importer "
                        "cannot authenticate to it — set WS_ADMIN_TOKEN and re-create it, "
                        "or sync from the command line")
    return problems


def _import_tools():
    """The platform's own parser and importer, from the mounted tools."""
    for path in (VENDOR, TOOLS):
        if path not in sys.path:
            sys.path.insert(0, path)
    try:
        from sync_subject import CTFdAdmin, sync            # noqa: E402
        from sync_workshop import sync_workshop             # noqa: E402
        from ws_parser import lint_all                      # noqa: E402
    except ImportError as exc:
        raise SourceError(f"this instance cannot load the importer ({exc}). "
                          f"{'; '.join(missing_pieces()) or 'Check the mounts.'}") from exc
    return CTFdAdmin, sync, sync_workshop, lint_all


def _admin_client(CTFdAdmin):
    token = os.environ.get("PRESET_ADMIN_TOKEN")
    if token:
        return CTFdAdmin(SELF_URL, token=token)
    password = os.environ.get("PRESET_ADMIN_PASSWORD")
    if password:
        return CTFdAdmin(SELF_URL, os.environ.get("PRESET_ADMIN_NAME") or "admin", password)
    raise SourceError("no preset admin credentials in this instance's environment")


class _Log:
    """Collects the importer's own output, and lets it through to the container log."""

    def __init__(self, job):
        self.job = job
        self.buffer = ""

    def write(self, text):
        sys.__stdout__.write(text)
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.job.log.append(line.rstrip())

    def flush(self):
        sys.__stdout__.flush()


class SyncJob:
    """One import: fetch, park for answers if needed, lint, import."""

    def __init__(self, app, repo, ref, who):
        self.app, self.repo, self.ref, self.who = app, repo, ref, who
        self.state = "running"
        self.log = []
        self.needs = []
        self.error = ""
        self.summary = {}
        self.started = int(time.time())
        self.finished = None
        self.commit = ""
        self._answers = None
        self._answers_ready = threading.Event()
        self._cancelled = False
        self._tmp = None

    # -- what the page sees ------------------------------------------------
    def snapshot(self):
        return {"state": self.state, "log": list(self.log), "needs": self.needs,
                "error": self.error, "summary": self.summary, "repo": self.repo,
                "ref": self.ref, "commit": self.commit, "who": self.who,
                "started": self.started, "finished": self.finished}

    def note(self, line):
        self.log.append(line)

    def supply_answers(self, answers):
        """Plaintext sidecars, decrypted in the admin's browser."""
        self._answers = answers or {}
        self._answers_ready.set()

    def cancel(self):
        """Give up on a job parked for a passphrase nobody came back with.

        Only a parked job is ever cancelled: it is waiting on a human, and the
        human pressing Sync again *is* the answer to that wait. A job that is
        actually importing is left alone.
        """
        self._cancelled = True
        self._answers_ready.set()

    # -- the work ----------------------------------------------------------
    def run(self):
        with self.app.app_context():
            try:
                self._run()
            except SourceError as exc:
                self._fail(str(exc))
            except SystemExit:
                # The importer exits on a lint failure; its message is already
                # in the log, and turning it into a state is this page's job.
                self._fail("the content did not pass the linter — see the log above")
            except Exception as exc:                        # noqa: BLE001
                self._fail(f"{type(exc).__name__}: {exc}")
            finally:
                if self._tmp:
                    shutil.rmtree(self._tmp, ignore_errors=True)

    def _fail(self, message):
        self.error = message
        self.state = "failed"
        self.finished = int(time.time())
        self.note(f"FAILED: {message}")

    def _run(self):
        CTFdAdmin, sync, sync_workshop, lint_all = _import_tools()
        self._tmp = tempfile.mkdtemp(prefix="workshop-sync-")
        info = materialize(self.repo, self.ref, self._tmp, log=self.note)
        self.commit = info["sha"]

        if info["sidecars"]:
            self._collect_answers(info["sidecars"])

        problems, advice = [], []
        for subject in info["subjects"]:
            name = os.path.basename(subject["dir"])
            errs, warns = lint_all(subject["dir"])
            problems += [f"{name}: {p}" for p in errs]
            advice += [f"{name}: {w}" for w in warns]
        if problems:
            for p in problems:
                self.note(f"FAIL {p}")
            raise SourceError(f"{len(problems)} problem(s) in the content — "
                              f"nothing was imported")
        self.note(f"lint: {len(info['subjects'])} subject(s) clean")
        # Advice refuses nothing. It is shown here because this page is where
        # the person who can act on it is standing.
        for a in advice:
            self.note(f"warn {a}")

        ctfd = _admin_client(CTFdAdmin)
        codes = os.path.join(self._tmp, "codes")
        os.makedirs(codes, exist_ok=True)
        out = _Log(self)
        stdout, sys.stdout = sys.stdout, out
        try:
            if info["kind"] == "workshop":
                result = sync_workshop(info["dir"], SELF_URL, None, None,
                                       codes_dir=codes, ctfd=ctfd)
            else:
                result = sync(info["dir"], SELF_URL, None, None,
                              os.path.join(codes, "codes.yaml"), ctfd=ctfd)
        finally:
            sys.stdout = stdout

        stats = (result or {}).get("stats") or {}
        self.summary = {
            "created": stats.get("created", 0),
            "updated": stats.get("updated", 0),
            "subjects": [s["repo"] + "@" + s["sha"][:7] for s in info["subjects"]],
            "kind": info["kind"],
        }
        self.state = "done"
        self.finished = int(time.time())
        record_sync(repo=self.repo, ref=self.ref, commit=info["sha"],
                    message=info["message"], by=self.who,
                    created=self.summary["created"], updated=self.summary["updated"],
                    needs_passphrase=bool(info["sidecars"]),
                    subjects=self.summary["subjects"])

    def _collect_answers(self, sidecars):
        """Park until the browser sends the decrypted answers, or give up.

        The passphrase is never sent here — the page decrypts with openpgp.js
        and posts the plaintext, which this instance is about to store in its
        own database anyway (PLAN.md §26.3).
        """
        if self._answers is None:
            self.needs = [
                {"id": str(i), "name": s["name"],
                 "subject": os.path.basename(s["dir"]),
                 "blob": base64.b64encode(open(s["path"], "rb").read()).decode()}
                for i, s in enumerate(sidecars)]
            self.state = "needs_answers"
            self.note(f"{len(sidecars)} encrypted answer file(s): "
                      + ", ".join(s["name"] for s in sidecars))
            record_sync(needs_passphrase=True)
            if not self._answers_ready.wait(ANSWERS_TIMEOUT):
                raise SourceError("gave up waiting for the answers to be unlocked")
            if self._cancelled:
                raise SourceError("superseded by a newer sync")
            self.state = "running"

        for i, sidecar in enumerate(sidecars):
            text = (self._answers or {}).get(str(i))
            if not text:
                raise SourceError(f"{sidecar['name']} was not unlocked, so the answers "
                                  f"in the repository could not be imported — nothing "
                                  f"was changed")
            with open(os.path.join(sidecar["dir"], sidecar["name"]), "w") as fh:
                fh.write(text)
            self.note(f"answers: {sidecar['name']} unlocked in the browser "
                      f"({len(text)} bytes)")


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------

def _tip(source):
    """The tip of the configured ref, cached for a minute; never fatal."""
    key = (source["repo"], source["ref"])
    hit = _tip_cache.get(key)
    if hit and time.time() - hit["at"] < TIP_TTL:
        return hit["value"]
    value = {"sha": "", "message": "", "error": ""}
    if source["repo"]:
        try:
            value["sha"], value["message"] = resolve_ref(source["repo"], source["ref"])
        except SourceError as exc:
            value["error"] = str(exc)
    _tip_cache[key] = {"at": time.time(), "value": value}
    return value


@workshop_sync.route("/admin/workshop/sync")
@admins_only
def page():
    source = current_source()
    return render_template("workshop_sync.html", source=source, last=last_sync(),
                           tip=_tip(source), problems=missing_pieces(),
                           job=_job.snapshot() if _job else None)


@workshop_sync.route("/admin/workshop/sync/source", methods=["POST"])
@admins_only
def save_source():
    try:
        set_source(request.form.get("repo"), request.form.get("ref"))
    except SourceError:
        pass        # the page shows what is stored; a bad value simply is not
    _tip_cache.clear()
    return redirect(url_for("workshop_sync.page"))


@workshop_sync.route("/api/v1/workshop/sync", methods=["POST"])
@admins_only
def start():
    global _job
    from flask import current_app
    payload = request.get_json(silent=True) or {}
    source = current_source()
    repo = (payload.get("repo") or source["repo"]).strip()
    ref = (payload.get("ref") or source["ref"]).strip()
    if not repo:
        return jsonify({"success": False,
                        "errors": ["this instance has no source repository yet"]}), 400
    problems = missing_pieces()
    if problems and any("tools are not mounted" in p or "PyYAML" in p or
                        "preset admin" in p for p in problems):
        return jsonify({"success": False, "errors": problems}), 409

    with _job_lock:
        if _job and _job.state == "running":
            return jsonify({"success": False,
                            "errors": ["a sync is already running"]}), 409
        if _job and _job.state == "needs_answers":
            # Parked on a passphrase nobody supplied — pressing Sync again is
            # how somebody who closed the tab starts over, so it supersedes.
            _job.cancel()
        user = get_current_user()
        _job = SyncJob(current_app._get_current_object(), repo, ref,
                       user.name if user else "admin")
        threading.Thread(target=_job.run, daemon=True).start()
    return jsonify({"success": True, "data": _job.snapshot()})


@workshop_sync.route("/api/v1/workshop/sync", methods=["GET"])
@admins_only
def status():
    if not _job:
        return jsonify({"success": True, "data": {"state": "idle"}})
    return jsonify({"success": True, "data": _job.snapshot()})


@workshop_sync.route("/api/v1/workshop/sync/answers", methods=["POST"])
@admins_only
def answers():
    payload = request.get_json(silent=True) or {}
    if not _job or _job.state != "needs_answers":
        return jsonify({"success": False,
                        "errors": ["no sync is waiting for answers"]}), 409
    _job.supply_answers({str(k): v for k, v in (payload.get("answers") or {}).items()})
    return jsonify({"success": True, "data": _job.snapshot()})


def load_syncpage(app):
    app.register_blueprint(workshop_sync)
