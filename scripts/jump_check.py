#!/usr/bin/env python3
"""Does the Jump handoff behave (issue #6, AC1 to AC9, and PLAN.md §50)?

    python3 scripts/jump_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite.
It mints its own tickets and runs its own callback sink, so it needs no Jump:
what it cannot prove is that the real Jump accepts what this sends, only that
what this sends is what the contract says.

§50 adds the session a ticket names: a ticket with half a session is
refused, an account follows the session its latest ticket names, two sessions
do not see each other's accounts, the supervision pages
narrow to a campus and a session, and an erasure Jump reports deletes the
account and nothing else. The sink plays Jump's half of that last one too: it
answers the erasure question from a list this script writes.

§51 adds the content: a ticket naming a content this instance does not serve,
or naming one on an instance no sync has recorded a content on, is refused
before any account exists, and every progress report names the content.

One case is not an acceptance criterion but a regression: a label that already
has accounts behind it cannot move to another key id. Both halves are checked,
the ticket and the settings page, because provisioning writes the config
straight and never goes through the form.

Shaped like scripts/workshop_check.py — positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back.** The key allowlist and the slug are saved and
restored, and every account it creates is deleted, because the instance this is
pointed at is usually the one a demo runs on. Interrupt it and the same cleanup
still runs; kill -9 it and you are left with two config keys to clear and a
handful of `*.jumpcheck.jump.invalid` accounts to delete.

Two things it cannot do over CTFd's own API, both of which the plugin exposes
on admin-only endpoints because it runs inside CTFd and has the database:
read the outbox (`/api/v1/workshop/jump/events`) and read the link rows
(`/api/v1/workshop/jump/links`). checkpoint.py made the same call earlier for
the same reason.

It needs `docker`, because the sink is a container on the instance's own
network rather than a socket on the host. A socket on the host is reached
through the bridge gateway, and a host firewall drops that silently — which is
indistinguishable from the bug this script exists to catch. The sink runs the
instance's own image, so nothing is pulled, and `docker stop` / `docker start`
is the outage, the idiom scripts/mirror_check.sh already uses.
"""
import atexit
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

fails = []
RUN = uuid.uuid4().hex[:6]

# Two environments, because "one instance serves the dev Jump and the
# production one at once" is a claim with its own acceptance criterion.
KID_A, SECRET_A, LABEL_A = "jumpcheck-a", "secret-a-" + RUN, "jumpcheck"
KID_B, SECRET_B, LABEL_B = "jumpcheck-b", "secret-b-" + RUN, "jumpcheckb"
# A third key id that claims A's label. It never becomes a way in: a label is
# owned by the accounts it namespaces, so this is the shape of "somebody
# renamed a key id in deploy/instances.yaml and kept its label".
KID_C = "jumpcheck-c"
SLUG = "jumpcheck-" + RUN
# What a sync would have recorded as this instance's content (§51).
CONTENT = "jumpcheck-content-" + RUN
TALENT = "talent" + RUN
# Two talents in two sessions of one campus, for §50.
TALENT_S1, TALENT_S2 = "tals1" + RUN, "tals2" + RUN
# CTFd's page size on /users and /api/v1/users (users.py, api/v1/users.py).
USERS_PAGE = 50
CAMPUS = {"campus": "campus-" + RUN, "campus_label": "Campus " + RUN}
SESSION_1 = {"session": "evt1-" + RUN, "session_label": "Coding Club un " + RUN,
             **CAMPUS}
SESSION_2 = {"session": "evt2-" + RUN, "session_label": "Coding Club deux " + RUN,
             **CAMPUS}
# The other Jump's session, on a campus with the SAME id: two environments'
# ids are unrelated, so this is another campus that happens to share one.
TALENT_S3 = "tals3" + RUN
SESSION_3 = {"session": "evt3-" + RUN, "session_label": "Coding Club trois " + RUN,
             **CAMPUS}
# How the picker names a campus: its key id, then its id (scope.py).
CAMPUS_A, CAMPUS_B = f"{KID_A}/{CAMPUS['campus']}", f"{KID_B}/{CAMPUS['campus']}"

# The drainer polls every 2 s and backs off 5, 10, 20 … seconds, so anything
# waiting on it needs room. Generous rather than tight: a flaky check is worse
# than a slow one.
DRAIN_DEADLINE = 40
RETURN_DEADLINE = 90


def check(cond, label):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}")
    if not cond:
        fails.append(label)


def waitfor(predicate, deadline, interval=1.0):
    """Poll until true or out of time. Returns what the predicate returned."""
    end = time.time() + deadline
    value = predicate()
    while not value and time.time() < end:
        time.sleep(interval)
        value = predicate()
    return value


# --------------------------------------------------------------------------
# The instance
# --------------------------------------------------------------------------

def nonce(session, base, path="/"):
    r = session.get(base + path, timeout=20)
    m = (re.search(r"'csrfNonce':\s*\"([0-9a-f]+)\"", r.text)
         or re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text))
    if not m:
        sys.exit(f"no CSRF nonce on {path} (HTTP {r.status_code})")
    return m.group(1)


class Admin:
    """An admin session that survives a long poll.

    The retrying adapter is not belt and braces: this script idles for tens of
    seconds while the drainer backs off, gunicorn closes the keep-alive
    connection underneath it, and requests' default adapter does not retry, so
    the next poll dies on `RemoteDisconnected` with the checks half done.
    """

    def __init__(self, base, password):
        self.base = base
        self.session = requests.Session()
        retry = Retry(total=4, connect=4, read=4, status=0, backoff_factor=0.3,
                      allowed_methods=frozenset(["GET", "PATCH", "DELETE"]))
        self.session.mount(base, HTTPAdapter(max_retries=retry))
        self.session.post(base + "/login", timeout=20, data={
            "name": "admin", "password": password,
            "nonce": nonce(self.session, base, "/login"), "_submit": "Submit"})
        self.nonce = nonce(self.session, base)
        if self.api("GET", "/configs/ctf_name").status_code != 200:
            sys.exit("could not sign in as admin — wrong password?")

    def api(self, method, path, **kw):
        return self.session.request(
            method, self.base + "/api/v1" + path,
            headers={"CSRF-Token": self.nonce, "Content-Type": "application/json"},
            timeout=30, **kw)

    def config(self, key):
        r = self.api("GET", f"/configs/{key}")
        if r.status_code != 200:
            return None
        return (r.json().get("data") or {}).get("value")

    def set_configs(self, values):
        r = self.api("PATCH", "/configs", json=values)
        if r.status_code != 200:
            sys.exit(f"could not write configs: HTTP {r.status_code} {r.text[:200]}")

    def events(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return self.api("GET", "/workshop/jump/events" + ("?" + query if query else "")).json()["data"]

    def links(self):
        return self.api("GET", "/workshop/jump/links").json()["data"]


# --------------------------------------------------------------------------
# The ticket, minted exactly as the contract says Jump mints it
# --------------------------------------------------------------------------

def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def derived_key(secret, purpose):
    return hmac.new(secret.encode(), purpose.encode(), hashlib.sha256).hexdigest().encode()


def mint(kid, secret, *, slug=SLUG, sub=TALENT, name="Check T.", iss="jump",
         aud=None, iat=None, exp=None, jti=None, tamper=False, session=None,
         content=None):
    now = int(time.time())
    iat = now if iat is None else iat
    exp = iat + 120 if exp is None else exp
    claims = {"kid": kid, "sub": sub, "name": name,
              "aud": aud if aud is not None else f"workshop:{slug}",
              "iss": iss, "iat": iat, "exp": exp,
              "jti": jti or uuid.uuid4().hex, **(session or {}),
              **({"content": content} if content is not None else {})}
    head = b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    digest = hmac.new(derived_key(secret, "jump/ticket"), head.encode(),
                      hashlib.sha256).digest()
    if tamper:
        digest = bytes([digest[0] ^ 0xFF]) + digest[1:]
    return head + "." + b64url(digest)


# --------------------------------------------------------------------------
# The sink: what Jump would be
# --------------------------------------------------------------------------

SINK_SOURCE = """
import json, os, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OUT = "/out/calls"
os.makedirs(OUT, exist_ok=True)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        record = {"path": self.path,
                  "body": body.decode("utf-8", "replace"),
                  "headers": {k: v for k, v in self.headers.items()}}
        name = "%.6f-%s.json" % (time.time(), uuid.uuid4().hex[:8])
        with open(os.path.join(OUT, name), "w") as handle:
            json.dump(record, handle)
        answer, status = {"ok": True}, 200
        if self.path == "/api/workshops/erasures":
            # What Jump would say it has erased: whatever the script listed,
            # asked about or not, so the plugin's own subset check is tested.
            try:
                with open("/out/erased.json") as handle:
                    answer = {"erased": json.load(handle)}
            except OSError:
                answer = {"erased": []}
            # A Jump that is down: the answer is a 503, and what it would
            # have said had it been up is still on disk, unread.
            if os.path.exists("/out/erasures_down"):
                answer, status = {"error": "down"}, 503
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(answer).encode())

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
"""


def docker(*args, check=True):
    try:
        out = subprocess.run(["docker", *args], capture_output=True, text=True,
                             timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.exit(f"this check needs docker on PATH ({exc})")
    if check and out.returncode != 0:
        sys.exit(f"docker {' '.join(args)} failed: {out.stderr.strip()[:300]}")
    return out.stdout.strip()


def instance_container(port):
    """The container publishing `port`, which is the instance under test."""
    for line in docker("ps", "--format", "{{.Names}}\t{{.Ports}}").splitlines():
        name, _, ports = line.partition("\t")
        if f":{port}->" in ports:
            return name
    sys.exit(f"no running container publishes port {port} — "
             f"this check needs the instance to be a local docker stack")


class Sink:
    """Jump's end of the callback: a container on the instance's own network.

    **Not a socket on the host.** A container reaches the host through the
    bridge gateway, and a host firewall — ufw, on the machine this was written
    on — drops that silently, in exactly the shape of "the callback never
    arrives". Which is indistinguishable from the bug this script exists to
    catch. On the instance's own network there is no firewall in the path and
    the origin is a DNS name that resolves the same everywhere.

    It runs the instance's *own image*, so nothing has to be pulled: the image
    is on any machine where the instance itself runs.

    `docker stop` / `docker start` is the outage, the same idiom
    scripts/mirror_check.sh already uses to take an upstream away.
    """

    def __init__(self, container, run):
        self.name = f"jumpcheck-sink-{run}"
        self.image = docker("inspect", container, "--format", "{{.Config.Image}}")
        self.network = self._network_of(container)
        self.dir = tempfile.mkdtemp(prefix="jumpcheck-")
        self.calls = os.path.join(self.dir, "calls")
        os.makedirs(self.calls, exist_ok=True)
        with open(os.path.join(self.dir, "sink.py"), "w") as handle:
            handle.write(SINK_SOURCE)
        self.created = False

    @staticmethod
    def _network_of(container):
        names = docker("inspect", container, "--format",
                       "{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}").split()
        for name in names:
            # An `internal: true` network has no route out, which is fine for
            # us, but CTFd is on both and we want the one it shares with
            # everything else on the stack.
            if docker("network", "inspect", name, "--format", "{{.Internal}}") == "false":
                return name
        return names[0] if names else sys.exit("the instance is on no network")

    @property
    def origin(self):
        return f"http://{self.name}:8080"

    def start(self):
        if self.created:
            docker("start", self.name)
        else:
            docker("run", "-d", "--name", self.name, "--network", self.network,
                   "--user", f"{os.getuid()}:{os.getgid()}",
                   "-v", f"{self.dir}:/out", "--entrypoint", "python",
                   self.image, "/out/sink.py")
            self.created = True
        # Give it the moment it needs to bind, so the first callback is not
        # recorded as an outage.
        time.sleep(1.5)

    def stop(self):
        if self.created:
            docker("stop", "-t", "1", self.name, check=False)

    def destroy(self):
        if self.created:
            docker("rm", "-f", self.name, check=False)
        shutil.rmtree(self.dir, ignore_errors=True)

    @property
    def received(self):
        out = []
        for name in sorted(os.listdir(self.calls)):
            with open(os.path.join(self.calls, name)) as handle:
                out.append(json.load(handle))
        return out

    def clear(self):
        for name in os.listdir(self.calls):
            os.remove(os.path.join(self.calls, name))

    def report_erased(self, talent_ids):
        """What the sink answers when asked which talents Jump erased."""
        with open(os.path.join(self.dir, "erased.json"), "w") as handle:
            json.dump(list(talent_ids), handle)

    def erasures_down(self, down):
        """Make the erasure question fail with a 503, or answer again."""
        flag = os.path.join(self.dir, "erasures_down")
        if down:
            open(flag, "w").close()
        elif os.path.exists(flag):
            os.remove(flag)


# --------------------------------------------------------------------------
# Participant helpers
# --------------------------------------------------------------------------

def enter(base, token):
    """Present a ticket. Returns the response and the session that holds it."""
    session = requests.Session()
    r = session.get(base + "/jump/enter", params={"t": token},
                    allow_redirects=False, timeout=30)
    return r, session


def page_counters(session, base):
    """What the participant reads on /workshop: `(solved, total)`."""
    html = session.get(base + "/workshop", timeout=30, allow_redirects=True).text
    m = re.search(r'class="ws-overall"\s+data-solved="(\d+)"\s+data-total="(\d+)"', html)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def solvable_steps(admin, session, base):
    """Steps this participant can solve without knowing an answer.

    `ack` is a lone Submit button by construction, so it is the one kind that
    is solvable on any instance without reading the content. A self-serve
    instance adds `checkpoint`, which is also a button there.
    """
    self_serve = str(admin.config("workshop_mode") or "instructor_led") == "self_serve"
    kinds = {"ack"} | ({"checkpoint"} if self_serve else set())
    visible = session.get(base + "/api/v1/challenges", timeout=30).json()["data"]
    out = []
    for row in visible:
        detail = admin.api("GET", f"/challenges/{row['id']}").json()["data"]
        if detail.get("quiz_type") in kinds:
            out.append(row["id"])
    return out


def attempt(session, base, challenge_id, submission="done"):
    """Submit, returning `(json, seconds)`. The seconds are the point of AC6."""
    csrf = nonce(session, base, "/workshop")
    started = time.time()
    r = session.post(base + "/api/v1/challenges/attempt", timeout=30,
                     headers={"CSRF-Token": csrf, "Content-Type": "application/json"},
                     json={"challenge_id": challenge_id, "submission": submission})
    elapsed = time.time() - started
    try:
        return r.json(), elapsed
    except ValueError:
        return {"status": r.status_code, "text": r.text[:200]}, elapsed


# --------------------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    base = sys.argv[1].rstrip("/")
    admin_pass = sys.argv[2]
    port = int(re.search(r":(\d+)", base).group(1)) if re.search(r":(\d+)", base) else 80

    admin = Admin(base, admin_pass)
    sink = Sink(instance_container(port), RUN)
    origin = sink.origin
    print(f"instance {base}, sink {origin} on {sink.network}\n")

    saved = {"workshop_jump_keys": admin.config("workshop_jump_keys") or "",
             "workshop_jump_instance": admin.config("workshop_jump_instance") or "",
             "workshop_content": admin.config("workshop_content") or ""}
    created = []

    def restore():
        # Ordered so the instance is never left accepting this script's keys:
        # config first, then the accounts.
        admin.set_configs(saved)
        for user_id in created:
            admin.api("DELETE", f"/users/{user_id}")
        print(f"\nrestored: config put back, {len(created)} account(s) deleted")

    atexit.register(restore)

    keys = {KID_A: {"origin": origin, "secret": SECRET_A, "label": LABEL_A},
            KID_B: {"origin": origin, "secret": SECRET_B, "label": LABEL_B}}
    admin.set_configs({"workshop_jump_keys": json.dumps(keys),
                       "workshop_jump_instance": SLUG,
                       "workshop_content": CONTENT})

    sink.start()
    atexit.register(sink.destroy)

    # -- the login page's two doors (login.html, jump.py login_is_staff) ---
    print("== the login page opens on the talent door, the staff form behind it ==")
    anon = requests.Session()
    talent = anon.get(base + "/login", timeout=20).text
    check(talent.count(f'href="{origin}"') == 2,
          "one Jump button per configured key")
    # The label in its parentheses, as the button renders it: LABEL_A alone
    # is a substring of LABEL_B and of the sink's host name in both hrefs,
    # so a bare `in` would pass with key A unlabelled.
    check(f"({LABEL_A})" in talent and f"({LABEL_B})" in talent,
          "labelled by environment, since there are two")
    check('name="password"' not in talent, "and no password field")
    check('href="/login?staff=1"' in talent, "a link leads to the staff door")
    deep = anon.get(base + "/login?next=/scoreboard", timeout=20).text
    check('href="/login?staff=1&amp;next=%2Fscoreboard"' in deep,
          "and carries `next` there, the only door that can honour it")
    staff = anon.get(base + "/login?staff=1", timeout=20).text
    check('name="password"' in staff, "the staff door is the form")
    check('href="/login"' in staff and f'href="{origin}"' not in staff,
          "which links back to the talent door rather than to Jump itself")
    r = anon.post(base + "/login", timeout=20, data={
        "name": "nobody" + RUN, "password": "wrong",
        "nonce": nonce(anon, base, "/login")})
    check('name="password"' in r.text and "alert-danger" in r.text,
          "a wrong password is answered next to the form, not on the talent door")
    bounced = anon.get(base + "/login?next=/admin/workshop/stats", timeout=20).text
    check('name="password"' in bounced,
          "an admin page bounced to the login opens on the staff door")
    check("epi-login-credit" in talent and "epi-login-credit" in staff,
          "both doors carry the credit core's hidden footer used to")
    # The header's Login button links to the page it would sit on. Present
    # on the anonymous front page, so its absence here is the login page's
    # doing (navbar.html) and not a header that lost it everywhere.
    front = anon.get(base + "/", timeout=20).text
    check("epi-cta-on-band" in front
          and "epi-cta-on-band" not in talent and "epi-cta-on-band" not in staff,
          "the header drops its Login button on the login page, and only there")

    # -- AC1, AC4 ----------------------------------------------------------
    print("== a cold entry creates one account and one link ==")
    before = len(admin.links())
    r, participant = enter(base, mint(KID_A, SECRET_A))
    check(r.status_code == 302, f"a valid ticket is accepted ({r.status_code})")
    check("/workshop" in r.headers.get("Location", ""),
          f"and lands on the workshop ({r.headers.get('Location')})")
    links = [l for l in admin.links() if l["talent_id"] == TALENT]
    check(len(admin.links()) == before + 1, "exactly one link row was created")
    check(len(links) == 1 and links[0]["kid"] == KID_A,
          "the link row names this key id")
    if not links:
        raise SystemExit("no link row — nothing further can be checked")
    user_a = links[0]["user_id"]
    created.append(user_a)
    check(links[0]["email"] == f"{TALENT}@{LABEL_A}.jump.invalid",
          f"the email is namespaced by the key's label ({links[0]['email']})")
    check(links[0]["has_password"] is False,
          "the account has no password, so local sign-in cannot reach it")

    # -- AC2 ---------------------------------------------------------------
    print("== a ticket is good once ==")
    once = mint(KID_A, SECRET_A)
    first, _ = enter(base, once)
    second, _ = enter(base, once)
    check(first.status_code == 302 and second.status_code == 403,
          f"the same ticket twice gives {first.status_code} then {second.status_code}")

    print("== and good once even for two requests at the same instant ==")
    concurrent = mint(KID_A, SECRET_A)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result()[0].status_code
                   for f in [pool.submit(enter, base, concurrent) for _ in range(2)]]
    check(sorted(results) == [302, 403],
          f"two concurrent requests with one jti give {sorted(results)} "
          f"(a get-then-set would give [302, 302])")

    # -- AC3 ---------------------------------------------------------------
    print("== a ticket that is wrong in any one way is refused ==")
    now = int(time.time())
    bad = {
        "a tampered signature": mint(KID_A, SECRET_A, tamper=True),
        "an audience naming another instance": mint(KID_A, SECRET_A, aud="workshop:somewhere-else"),
        "an unknown key id": mint("jumpcheck-nope", SECRET_A),
        "the right key id signed with the other key's secret": mint(KID_A, SECRET_B),
        "an expired exp": mint(KID_A, SECRET_A, iat=now - 600, exp=now - 60),
        "a lifetime of a day": mint(KID_A, SECRET_A, iat=now, exp=now + 86400),
        "an iat well in the future": mint(KID_A, SECRET_A, iat=now + 600, exp=now + 700),
        "an issuer that is not jump": mint(KID_A, SECRET_A, iss="not-jump"),
        "a content this instance does not serve":
            mint(KID_A, SECRET_A, content="another-content-" + RUN),
        "an empty content": mint(KID_A, SECRET_A, content=""),
    }
    for label, token in bad.items():
        r, _ = enter(base, token)
        check(r.status_code == 403, f"{label} is refused ({r.status_code})")

    # -- §51 ---------------------------------------------------------------
    print("== a ticket names the content it is for, and only that one gets in ==")
    stranger = "talentc" + RUN
    r, _ = enter(base, mint(KID_A, SECRET_A, sub=stranger,
                            content="another-content-" + RUN))
    check(r.status_code == 403 and not [l for l in admin.links()
                                        if l["talent_id"] == stranger],
          "a ticket for another content creates no account")
    r, _ = enter(base, mint(KID_A, SECRET_A, content=CONTENT))
    check(r.status_code == 302,
          f"a ticket for the content synced here is accepted ({r.status_code})")
    admin.set_configs({"workshop_content": ""})
    r, _ = enter(base, mint(KID_A, SECRET_A, content=CONTENT))
    check(r.status_code == 403,
          f"on an instance no sync has recorded a content on, a ticket naming "
          f"one is refused ({r.status_code})")
    r, _ = enter(base, mint(KID_A, SECRET_A))
    check(r.status_code == 302,
          f"and one naming none, from a Jump older than the claim, is not "
          f"({r.status_code})")
    admin.set_configs({"workshop_content": CONTENT})

    # -- AC1 warm, AC5 -----------------------------------------------------
    print("== the same talent comes back, and a talent from the other Jump does not collide ==")
    r, participant = enter(base, mint(KID_A, SECRET_A))
    warm = [l for l in admin.links() if l["talent_id"] == TALENT and l["kid"] == KID_A]
    check(r.status_code == 302 and len(warm) == 1 and warm[0]["user_id"] == user_a,
          "a warm entry reuses the same account and adds no link row")

    r, _ = enter(base, mint(KID_B, SECRET_B))
    other = [l for l in admin.links() if l["talent_id"] == TALENT and l["kid"] == KID_B]
    check(r.status_code == 302 and len(other) == 1, "the other key id is accepted too")
    if other:
        created.append(other[0]["user_id"])
        check(other[0]["user_id"] != user_a,
              "the same talent id on two key ids is two distinct accounts")
        check(other[0]["email"] == f"{TALENT}@{LABEL_B}.jump.invalid",
              "and two distinct namespaces, so neither is on the other's scoreboard")

    # -- a label belongs to the accounts it namespaces ---------------------
    print("== a label that already has accounts cannot move to another key id ==")
    stolen = dict(keys)
    stolen[KID_C] = {"origin": origin, "secret": SECRET_A, "label": LABEL_A}
    admin.set_configs({"workshop_jump_keys": json.dumps(stolen)})
    r, _ = enter(base, mint(KID_C, SECRET_A))
    check(r.status_code == 403,
          f"a ticket whose key id reuses another's label is refused "
          f"({r.status_code})")
    check(not [l for l in admin.links()
               if l["talent_id"] == TALENT and l["kid"] == KID_C],
          "and no account was re-linked under the new key id")

    # The same refusal from the other direction, because the two paths are
    # separate: provisioning writes the config straight and never sees the form.
    page = admin.session.post(base + "/admin/workshop/jump", timeout=30, data={
        "nonce": admin.nonce, "instance": SLUG, "row_count": "1",
        "kid-0": KID_C, "origin-0": origin,
        "label-0": LABEL_A, "secret-0": SECRET_A})
    check("already namespaces the accounts" in page.text,
          "and the settings page refuses the same pairing rather than saving it")
    # Whatever that POST decided, the next checks need the two real keys back.
    admin.set_configs({"workshop_jump_keys": json.dumps(keys)})

    check_sessions(admin, base, sink, created)

    # -- AC6, AC8 ----------------------------------------------------------
    print("== solving queues one row, and it reaches the sink ==")
    steps = solvable_steps(admin, participant, base)
    if not steps:
        print("  (no button-only step is available to this account — "
              "AC6 to AC9 need one, so they are skipped)")
        return finish()

    sink.clear()
    response, baseline = attempt(participant, base, steps[0])
    check(response.get("data", {}).get("status") in ("correct", "already_solved"),
          f"the step is accepted ({response.get('data', {}).get('status')})")
    rows = admin.events(user_id=user_a)
    check(len(rows) == 1 and rows[0]["challenge_id"] == steps[0],
          f"exactly one outbox row was written ({len(rows)})")

    sent = waitfor(lambda: [e for e in admin.events(user_id=user_a)
                            if e["status"] == "sent"], DRAIN_DEADLINE)
    check(bool(sent), "the row went pending then sent")
    check(len(sink.received) >= 1, f"the sink received it ({len(sink.received)})")

    if sink.received:
        call = sink.received[-1]
        raw = call["body"].encode()
        body = json.loads(raw)
        check(call["path"] == "/api/workshops/callback",
              f"posted to the contract's path ({call['path']})")
        check(set(body) == {"instanceSlug", "contentSlug", "talentId",
                            "solvedSteps", "totalSteps", "isComplete"},
              f"the payload is the agreed shape ({sorted(body)})")
        check(body["instanceSlug"] == SLUG and body["talentId"] == TALENT,
              "and names this instance and this talent")
        check(body["contentSlug"] == CONTENT,
              f"and the content synced here ({body['contentSlug']!r})")
        ts = call["headers"].get("X-Timestamp", "")
        expected = "sha256=" + hmac.new(derived_key(SECRET_A, "jump/callback"),
                                        f"{ts}.".encode() + raw,
                                        hashlib.sha256).hexdigest()
        check(call["headers"].get("X-Signature") == expected,
              "the signature covers the exact bytes on the wire")
        check(abs(int(ts) - time.time()) < 300,
              "the timestamp is inside Jump's 300 s freshness window")
        check(call["headers"].get("X-Idempotency-Key") == f"{user_a}:{steps[0]}",
              "the idempotency key is the outbox row's natural key")
        solved, total = page_counters(participant, base)
        check((body["solvedSteps"], body["totalSteps"]) == (solved, total),
              f"the reported counters are what the page renders "
              f"({body['solvedSteps']}/{body['totalSteps']} vs {solved}/{total})")

    # -- AC7 ---------------------------------------------------------------
    print("== the sink is down: solving is unaffected, and the queue waits ==")
    if len(steps) < 2:
        # A re-solve cannot stand in for a second step: an already-solved
        # challenge answers `already_solved` from CTFd/api/v1/challenges.py:988
        # without ever calling `solve()`, so no row is written and there would
        # be nothing to watch back off. Skipped rather than asserted against an
        # empty queue, which is a failure that means nothing.
        print("  (this instance has one button-only step, so there is no second "
              "solve to make during the outage — skipped)")
    else:
        sink.stop()
        sink.clear()
        response, outage = attempt(participant, base, steps[1])
        check(outage < max(2.0, baseline * 3 + 0.5),
              f"the attempt is as fast with Jump down as with it up "
              f"({outage:.2f}s vs {baseline:.2f}s)")

        backed_off = waitfor(
            lambda: [e for e in admin.events(user_id=user_a)
                     if e["status"] == "pending" and e["attempts"] > 0],
            DRAIN_DEADLINE)
        check(bool(backed_off), "rows back off rather than disappearing")

        print("== the sink comes back: the queue drains on its own ==")
        sink.start()
        drained = waitfor(lambda: not [e for e in admin.events(user_id=user_a)
                                       if e["status"] == "pending"],
                          RETURN_DEADLINE, 2)
        check(bool(drained), "every queued row drained once the sink returned")
        check(len(sink.received) >= 1, "and the sink received what it had missed")

    # -- AC9 ---------------------------------------------------------------
    print("== a key id that is gone is dropped, not retried forever ==")
    r, orphan_session = enter(base, mint(KID_B, SECRET_B))
    orphan_steps = solvable_steps(admin, orphan_session, base)
    orphan_id = other[0]["user_id"] if other else None
    if orphan_steps and orphan_id:
        sink.stop()
        attempt(orphan_session, base, orphan_steps[0])
        queued = waitfor(lambda: admin.events(user_id=orphan_id), DRAIN_DEADLINE)
        check(bool(queued), "the second talent's solve queued a row")
        admin.set_configs({"workshop_jump_keys": json.dumps({KID_A: keys[KID_A]})})
        dropped = waitfor(
            lambda: [e for e in admin.events(user_id=orphan_id)
                     if e["status"] == "dropped"], DRAIN_DEADLINE)
        check(bool(dropped),
              "removing its key id drops the row instead of retrying it")
        sink.start()
    else:
        print("  (no step available to the second account, skipped)")

    return finish()


def check_sessions(admin, base, sink, created):
    """§50: the session a ticket names, what it hides, and the erasure pull."""
    print("== a ticket naming half a session is refused ==")
    half = {"session": SESSION_1["session"]}
    r, _ = enter(base, mint(KID_A, SECRET_A, sub=TALENT_S1, session=half))
    check(r.status_code == 403, f"a session without its campus ({r.status_code})")
    check(not [l for l in admin.links() if l["talent_id"] == TALENT_S1],
          "and no account was created for it")

    # Fifty older accounts in no session, ahead of both talents in id order:
    # without them every list fits on one page and a page cut before the
    # filter looks exactly like one cut after it.
    filler = []
    for i in range(USERS_PAGE):
        r = admin.api("POST", "/users", json={
            "name": f"Filler {i} {RUN}", "email": f"filler{i}-{RUN}@example.invalid",
            "password": uuid.uuid4().hex})
        if r.status_code == 200:
            filler.append(r.json()["data"]["id"])
    created.extend(filler)
    check(len(filler) == USERS_PAGE, f"{USERS_PAGE} older accounts to page past")

    print("== each account is filed under the session its ticket names ==")
    r1, s1 = enter(base, mint(KID_A, SECRET_A, sub=TALENT_S1, name="Check Un.",
                              session=SESSION_1))
    r2, s2 = enter(base, mint(KID_A, SECRET_A, sub=TALENT_S2, name="Check Deux.",
                              session=SESSION_2))
    check(r1.status_code == 302 and r2.status_code == 302,
          f"both tickets are accepted ({r1.status_code}, {r2.status_code})")
    by_talent = {l["talent_id"]: l for l in admin.links()}
    one, two = by_talent.get(TALENT_S1), by_talent.get(TALENT_S2)
    if not one or not two:
        check(False, "both accounts exist")
        return
    created.extend([one["user_id"], two["user_id"]])
    check(one["session_id"] and two["session_id"]
          and one["session_id"] != two["session_id"],
          "two sessions, two different session rows")

    # Jump names another session only when this instance has moved on to
    # another content and the talent came back for it (§51): the account
    # follows, and a ticket naming the first session again brings it back.
    enter(base, mint(KID_A, SECRET_A, sub=TALENT_S1, session=SESSION_2))
    again = {l["talent_id"]: l for l in admin.links()}[TALENT_S1]
    check(again["session_id"] == two["session_id"],
          "coming back under another session moves the account there")
    enter(base, mint(KID_A, SECRET_A, sub=TALENT_S1, session=SESSION_1))
    again = {l["talent_id"]: l for l in admin.links()}[TALENT_S1]
    check(again["session_id"] == one["session_id"],
          "and a ticket naming the first one moves it back, to the same row")

    print("== two sessions do not see each other ==")
    listed = {u["id"] for u in s1.get(base + "/api/v1/users", timeout=30)
              .json().get("data", [])}
    check(one["user_id"] in listed, "a talent sees their own session in the list")
    check(two["user_id"] not in listed, "and not the other session")
    r = s1.get(base + f"/api/v1/users/{two['user_id']}", timeout=30)
    check(r.status_code == 404,
          f"the other session's account answers 404, as a deleted one would "
          f"({r.status_code})")
    r = s1.get(base + f"/api/v1/users/{one['user_id']}", timeout=30)
    check(r.status_code == 200, f"their own still answers ({r.status_code})")

    print("== a room's lists are cut after the filter, not before ==")
    # Fifty older accounts come first in id order, so a page cut before the
    # filter would hold none of the room.
    body = s1.get(base + "/api/v1/users", timeout=30).json()
    check([u["id"] for u in body.get("data", [])] == [one["user_id"]],
          "page 1 of /api/v1/users is the room, behind fifty older accounts")
    check((body.get("meta") or {}).get("pagination", {}).get("total") == 1
          and body["meta"]["pagination"].get("pages") == 1,
          f"and its page count is the room's ({(body.get('meta') or {}).get('pagination')})")
    # By the row's link, not the name: the navbar names the reader on every
    # page, listed or not.
    page = s1.get(base + "/users", timeout=30).text
    rows = set(int(i) for i in re.findall(r'href="/users/(\d+)"', page))
    check(rows == {one["user_id"]}, f"page 1 of /users is the room too ({sorted(rows)[:5]})")
    # The other session's talent outscores this one, so the instance's top
    # one is somebody this reader may not see.
    for user_id, value in ((two["user_id"], 100), (one["user_id"], 1)):
        admin.api("POST", "/awards", json={
            "user_id": user_id, "name": "jumpcheck " + RUN, "value": value,
            "category": "jumpcheck"})
    top = s1.get(base + "/api/v1/scoreboard/top/1", timeout=30).json().get("data") or {}
    check([row["id"] for row in top.values()] == [one["user_id"]],
          f"the score graph's top one is the room's own best ({top and list(top)})")
    check(all(row.get("solves") for row in top.values()),
          "with the points that put them there")

    r3, _ = enter(base, mint(KID_B, SECRET_B, sub=TALENT_S3, name="Check Trois.",
                             session=SESSION_3))
    three = {l["talent_id"]: l for l in admin.links()}.get(TALENT_S3)
    if r3.status_code != 302 or not three:
        check(False, f"the other Jump's talent exists ({r3.status_code})")
        return
    created.append(three["user_id"])

    print("== the supervision pages narrow to a campus and a session ==")

    def answers(**params):
        return admin.session.get(base + "/admin/workshop/answers", timeout=30,
                                 params=params).text

    page = answers(campus=CAMPUS_A, session=one["session_id"])
    check(CAMPUS["campus_label"] in page and SESSION_1["session_label"] in page,
          "the picker names the campus and the session as Jump labelled them")
    check("Check Un." in page and "Check Deux." not in page,
          "one session: its talent is listed, the other session's is not")
    page = admin.session.get(base + "/admin/workshop/stats", timeout=30).text
    check(SESSION_1["session_label"] in page,
          "the choice follows the supervisor to the next page")
    page = answers(campus=CAMPUS_A)
    check("Check Un." in page and "Check Deux." in page,
          "the whole campus: both sessions are listed")
    check("Check Trois." not in page,
          "and not the other Jump's campus that shares its id")
    check(f"{CAMPUS['campus_label']} ({KID_A})" in page
          and f"{CAMPUS['campus_label']} ({KID_B})" in page,
          "two Jumps on one instance: the picker names each campus's key id")
    page = answers(campus=CAMPUS_B, session=one["session_id"])
    check("Check Trois." in page and "Check Un." not in page,
          "a session of another campus falls back to the campus picked")
    page = answers(campus=f"{KID_A}/no-such-campus-{RUN}")
    check(all(n in page for n in ("Check Un.", "Check Deux.", "Check Trois.", "Check T.")),
          "a campus no session names falls back to the whole instance")
    page = answers(campus="")
    check("Check Un." in page and "Check Deux." in page and "Check T." in page,
          "the whole instance: an account with no session is listed too")

    print("== a session row is written by the entries that name it, and kept put ==")
    unseen = {"session": "evt4-" + RUN, "session_label": "Coding Club quatre " + RUN,
              **CAMPUS}
    enter(base, mint(KID_A, SECRET_A, sub=TALENT_S1, session=unseen))
    check(unseen["session_label"] in answers(campus=CAMPUS_A),
          "a ticket for a new session records its room, with the talent in it")
    moved = {**SESSION_1, "session_label": "Coding Club un, renamed " + RUN,
             "campus": "moved-" + RUN, "campus_label": "Moved " + RUN}
    enter(base, mint(KID_A, SECRET_A, sub=TALENT_S1, session=moved))
    page = answers(campus=CAMPUS_A, session=one["session_id"])
    check(moved["session_label"] in page and "Check Un." in page,
          "a renamed session takes its new name and gets its talent back")
    check(moved["campus_label"] not in page,
          "and stays in its campus, whatever a later ticket says")
    check(unseen["session_label"] not in page,
          "the room left behind, now empty, leaves the picker")

    print("== a step's solvers, the submissions and the feedback follow the room ==")
    # A step of the check's own, since a fresh instance has none: one flag,
    # solved by both rooms, one wrong try and one review each.
    r = admin.api("POST", "/challenges", json={
        "name": "jumpcheck step " + RUN, "category": "jumpcheck", "description": "-",
        "value": 1, "type": "standard", "state": "visible"})
    step = (r.json().get("data") or {}).get("id") if r.status_code == 200 else None
    if step is None:
        check(False, f"a step of the check's own ({r.status_code})")
        return
    atexit.register(lambda: admin.api("DELETE", f"/challenges/{step}"))
    admin.api("POST", "/flags", json={"challenge": step, "content": "flag-" + RUN,
                                      "type": "static", "data": "case_insensitive"})
    for talent, said in ((s1, "review un " + RUN), (s2, "review deux " + RUN)):
        headers = {"CSRF-Token": nonce(talent, base), "Content-Type": "application/json"}
        for answer in ("wrong-" + RUN, "flag-" + RUN):
            talent.post(base + "/api/v1/challenges/attempt", headers=headers, timeout=30,
                        json={"challenge_id": step, "submission": answer})
        talent.put(base + f"/api/v1/challenges/{step}/ratings", headers=headers,
                   timeout=30, json={"value": 1, "review": said})

    solvers = {row["account_id"] for row in s1.get(
        base + f"/api/v1/challenges/{step}/solves", timeout=30).json().get("data", [])}
    check(solvers == {one["user_id"]},
          f"the step's solver list names the reader's room only ({sorted(solvers)})")
    count = (s1.get(base + f"/api/v1/challenges/{step}", timeout=30).json()
             .get("data") or {}).get("solves")
    check(count == 2, f"and its solve count is still the instance's ({count})")

    room = {"campus": CAMPUS_A, "session": one["session_id"]}
    page = admin.session.get(base + "/admin/workshop/submissions", timeout=30,
                             params=room).text
    check("Check Un." in page and "Check Deux." not in page,
          "the submissions page lists the session's attempts only")
    page = admin.session.get(base + "/admin/workshop/feedback", timeout=30,
                             params=room).text
    check("review un " + RUN in page and "review deux " + RUN not in page,
          "the feedback page reads the session's reviews only")
    page = admin.session.get(base + "/admin/workshop/feedback", timeout=30,
                             params={"campus": ""}).text
    check("review un " + RUN in page and "review deux " + RUN in page,
          "and the whole instance's when no campus is picked")

    print("== an erasure Jump reports deletes that account, and only that one ==")
    sink.clear()
    sink.report_erased([TALENT_S2, "never-asked-" + RUN])
    report = admin.api("POST", "/workshop/jump/erasures").json().get("data") or {}
    check(report.get("deleted") == 1,
          f"one account deleted ({report})")
    after = {l["talent_id"] for l in admin.links()}
    check(TALENT_S2 not in after, "the erased talent's account and link are gone")
    check(TALENT_S1 in after and TALENT in after,
          "every other account is untouched, including one Jump never named")
    asked = [c for c in sink.received if c["path"] == "/api/workshops/erasures"]
    check(bool(asked), "the question reached the contract's path")
    if asked:
        call = next((c for c in asked
                     if TALENT_S1 in json.loads(c["body"]).get("talentIds", [])),
                    asked[0])
        raw = call["body"].encode()
        ts = call["headers"].get("X-Timestamp", "")
        expected = "sha256=" + hmac.new(derived_key(SECRET_A, "jump/callback"),
                                        f"{ts}.".encode() + raw,
                                        hashlib.sha256).hexdigest()
        check(call["headers"].get("X-Signature") == expected,
              "signed like a callback, over the exact bytes on the wire")
        check(set(json.loads(raw)) == {"talentIds"},
              "and the body is the agreed shape")

    sink.report_erased([])
    report = admin.api("POST", "/workshop/jump/erasures").json().get("data") or {}
    check(report.get("deleted") == 0 and TALENT_S1 in {
        l["talent_id"] for l in admin.links()},
        "an answer naming nobody deletes nobody")

    print("== a Jump that does not answer: nothing deleted, and the call says so ==")
    sink.report_erased([TALENT_S1])
    sink.erasures_down(True)
    r = admin.api("POST", "/workshop/jump/erasures")
    body = r.json() if r.headers.get("Content-Type", "").startswith("application/json") else {}
    check(r.status_code == 502 and body.get("success") is False,
          f"the manual pass answers 502, not success ({r.status_code}, {body.get('success')})")
    check((body.get("data") or {}).get("errors") and not body["data"].get("deleted"),
          f"and reports the failure with nothing deleted ({body.get('data')})")
    check(TALENT_S1 in {l["talent_id"] for l in admin.links()},
          "the talent the down Jump would have named is still here")
    sink.erasures_down(False)
    sink.report_erased([])

    print("== a key no longer configured: its accounts are counted, not deleted ==")
    configured = admin.config("workshop_jump_keys")
    under_b = len([l for l in admin.links() if l["kid"] == KID_B])
    admin.set_configs({"workshop_jump_keys": json.dumps(
        {k: v for k, v in json.loads(configured).items() if k != KID_B})})
    sink.report_erased([TALENT_S3])
    report = admin.api("POST", "/workshop/jump/erasures").json().get("data") or {}
    check((report.get("unverifiable") or {}).get(KID_B) == under_b and under_b,
          f"the pass counts {under_b} account(s) under the retired key ({report})")
    check(TALENT_S3 in {l["talent_id"] for l in admin.links()},
          "and deletes none of them, even one its old Jump would name")
    page = admin.session.get(base + "/admin/workshop/jump", timeout=30).text
    check(f"under <code>{KID_B}</code>" in page,
          "the settings page names the key and its accounts")
    admin.set_configs({"workshop_jump_keys": configured})
    sink.report_erased([])


def finish():
    print("\n" + ("ALL GREEN" if not fails else f"FAILURES: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
