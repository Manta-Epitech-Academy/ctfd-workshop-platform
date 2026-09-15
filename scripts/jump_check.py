#!/usr/bin/env python3
"""Does the Jump handoff behave (issue #6, AC1 to AC9)?

    python3 scripts/jump_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite.
It mints its own tickets and runs its own callback sink, so it needs no Jump:
what it cannot prove is that the real Jump accepts what this sends, only that
what this sends is what the contract says.

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
SLUG = "jumpcheck-" + RUN
TALENT = "talent" + RUN

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
         aud=None, iat=None, exp=None, jti=None, tamper=False):
    now = int(time.time())
    iat = now if iat is None else iat
    exp = iat + 120 if exp is None else exp
    claims = {"kid": kid, "sub": sub, "name": name,
              "aud": aud if aud is not None else f"workshop:{slug}",
              "iss": iss, "iat": iat, "exp": exp,
              "jti": jti or uuid.uuid4().hex}
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
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

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
             "workshop_jump_instance": admin.config("workshop_jump_instance") or ""}
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
                       "workshop_jump_instance": SLUG})

    sink.start()
    atexit.register(sink.destroy)

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
    }
    for label, token in bad.items():
        r, _ = enter(base, token)
        check(r.status_code == 403, f"{label} is refused ({r.status_code})")

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
        check(set(body) == {"instanceSlug", "talentId", "solvedSteps",
                            "totalSteps", "isComplete"},
              f"the payload is the agreed shape ({sorted(body)})")
        check(body["instanceSlug"] == SLUG and body["talentId"] == TALENT,
              "and names this instance and this talent")
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
        print("  (only one button-only step on this instance, so the outage "
              "is exercised on a re-solve rather than a new one)")
    sink.stop()
    sink.clear()
    target = steps[1] if len(steps) > 1 else steps[0]
    response, outage = attempt(participant, base, target)
    check(outage < max(2.0, baseline * 3 + 0.5),
          f"the attempt is as fast with Jump down as with it up "
          f"({outage:.2f}s vs {baseline:.2f}s)")

    backed_off = waitfor(
        lambda: [e for e in admin.events(user_id=user_a)
                 if e["status"] == "pending" and e["attempts"] > 0], DRAIN_DEADLINE)
    check(bool(backed_off), "rows back off rather than disappearing")

    print("== the sink comes back: the queue drains on its own ==")
    sink.start()
    drained = waitfor(lambda: not [e for e in admin.events(user_id=user_a)
                                   if e["status"] == "pending"], RETURN_DEADLINE, 2)
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


def finish():
    print("\n" + ("ALL GREEN" if not fails else f"FAILURES: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
