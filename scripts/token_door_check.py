#!/usr/bin/env python3
"""Does a token open its own account, and nothing else?

    python3 scripts/token_door_check.py <base-url> <admin-pass>

The plugin has no test suite, so this script **is** this feature's test suite
(PLAN.md §42, §60), for the half the server holds. The other half is the
page, and scripts/token_door_browser_check.js holds that one: the token is
made in the browser and never leaves it, so only a browser can show it.

This script plays the browser. It makes a token the way assets/door.js does
(16 random bytes), derives the sign-in value with its own HKDF (RFC 5869, a
dozen lines below, no library), and holds the server to this:

  * the login page's participant side is the door: one box, no name, no
    e-mail, no password, and the staff form still one link away;
  * a code and a sign-in value make an account; signed out, the same value
    opens the same account, from any browser;
  * **every refusal is the one answer**, byte for byte the answer to a wrong
    code: an unknown value, a malformed one, a banned account;
  * coming back is not throttled, and wrong sign-in values do not close the
    door on the room typing codes;
  * an account with no name cannot type one: no settings page, and the
    request that page saves with is refused; reading the profile still works,
    and staff are not touched;
  * the guard holds with the plugin switched off;
  * **the database holds no token and no sign-in value**: core's own export
    of every table is searched for both, and holds only the SHA-256.

`KNOWN` is a test vector shared with the browser script: one token, the
sign-in value it must derive. This script checks its own HKDF against it; the
browser script checks assets/door.js against it. Three implementations (this
one, Node's, the browser's Web Crypto), one answer.

Shaped like scripts/registration_check.py: positional arguments, `check(cond,
label)`, `ALL GREEN` or `FAILURES: [...]`, non-zero exit on any failure.

**It puts the instance back**: the accounts and the code it made are deleted
and the plugin switch is left as it was found, including on an exception or a
Ctrl-C. It switches the plugin off for a few seconds near the end. Do not run
it during a session.
"""
import atexit
import hashlib
import hmac
import io
import re
import secrets
import sys
import uuid
import zipfile

import requests

if len(sys.argv) < 3:
    sys.exit(__doc__)
BASE = sys.argv[1].rstrip("/")
ADMIN_PASS = sys.argv[2]
RUN = uuid.uuid4().hex[:6]
PAGE = "/admin/workshop/codes"
PLUGIN = "/admin/workshop/plugin"
DOOR = "/api/v1/workshop/door"
NAME = re.compile(r"[A-ZÉ][a-zéèêï]+-[A-ZÉ][a-zéèê]+-\d{2,4}")
fails = []

# assets/door.js, constant for constant.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
SALT = b"ctfd-workshop-token-door-v1"
INFO_SIGNIN = b"sign-in"
KNOWN = (bytes(range(16)),
         "000G-40R4-0M30-E209-185G-R38E-1WQS",
         "86a70e11a5424d57ac29a433b40674b79fbb86b0c7bfca2c8918d29acce4d414")


def check(cond, label):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


def nonce(s, path="/"):
    r = s.get(BASE + path, timeout=20)
    m = (re.search(r'name="nonce"[^>]*value="([0-9a-f]+)"', r.text)
         or re.search(r"['\"]csrfNonce['\"]\s*:\s*['\"]([0-9a-f]+)", r.text))
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# The token, the way the browser makes it
# ---------------------------------------------------------------------------

def token_text(raw):
    """16 bytes as the 28 characters a person is shown."""
    bits = int.from_bytes(raw, "big") << 2          # 128 bits, 2 of padding
    body = "".join(ALPHABET[(bits >> (5 * i)) & 31] for i in range(25, -1, -1))
    digest = hashlib.sha256(raw).digest()
    ten = int.from_bytes(digest[:2], "big") >> 6    # the first ten bits
    text = body + ALPHABET[ten >> 5] + ALPHABET[ten & 31]
    return "-".join(text[i:i + 4] for i in range(0, 28, 4))


def signin_value(raw):
    """HKDF-SHA-256 (RFC 5869), one block: extract, then expand to 32 bytes."""
    prk = hmac.new(SALT, raw, hashlib.sha256).digest()
    return hmac.new(prk, INFO_SIGNIN + b"\x01", hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# The admin
# ---------------------------------------------------------------------------

admin = requests.Session()
admin.post(BASE + "/login", timeout=20,
           data={"name": "admin", "password": ADMIN_PASS,
                 "nonce": nonce(admin, "/login?staff=1"), "_submit": "Submit"})
if admin.get(BASE + PAGE, timeout=20).status_code != 200:
    sys.exit(f"{PAGE} does not answer: wrong password, or the plugin is "
             "older than the registration codes")
TOKEN = nonce(admin, PAGE)
H = {"CSRF-Token": TOKEN, "Content-Type": "application/json"}


def api(method, path, **kw):
    return admin.request(method, BASE + "/api/v1" + path, headers=H, timeout=20, **kw)


def codes_page(**data):
    return admin.post(BASE + PAGE, timeout=20, allow_redirects=False,
                      data={"nonce": TOKEN, **data})


def code_id(code):
    html = admin.get(BASE + PAGE, timeout=20).text
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    for chunk in body.split("<tr>")[1:]:
        if f"<code>{code}</code>" in chunk:
            return re.search(r'name="id" value="(\d+)"', chunk).group(1)
    return None


def plugin_on():
    return "badge-success" in admin.get(BASE + PLUGIN, timeout=20).text


def switch(to):
    admin.post(BASE + PLUGIN, timeout=20,
               data={"nonce": nonce(admin, PLUGIN), "enabled": to})


WAS_ON = plugin_on()
if not WAS_ON:
    sys.exit("the workshop plugin is switched off on this instance")
CODE = f"TOKEN-{RUN}"
made_users = []


def restore():
    if not plugin_on():
        switch("on")
    for uid in made_users:
        api("DELETE", f"/users/{uid}")
    ident = code_id(CODE)
    if ident:
        codes_page(action="delete", id=ident)


atexit.register(restore)
if codes_page(action="create", code=CODE, campus=f"Token {RUN}").status_code != 302:
    sys.exit(f"could not create {CODE}")


class Browser:
    """A browser nobody has signed in on: a session and its CSRF nonce."""

    def __init__(self):
        self.s = requests.Session()
        self.h = {"CSRF-Token": nonce(self.s, "/login"),
                  "Content-Type": "application/json"}

    def post(self, path, **kw):
        r = self.s.post(BASE + DOOR + path, headers=self.h, timeout=20, **kw)
        if r.status_code == 200:
            # Signing in changes the session's nonce.
            self.h["CSRF-Token"] = nonce(self.s, "/")
        return r

    def signin(self, value):
        return self.post("/signin", json={"signin": value})

    def me(self):
        # Not followed: signed out, core answers with a redirect to the login
        # page, and that page is a 200.
        return self.s.get(BASE + "/api/v1/users/me", timeout=20,
                          allow_redirects=False)

    def who(self):
        r = self.me()
        return r.json()["data"] if r.status_code == 200 else None

    def out(self):
        self.s.get(BASE + "/logout", timeout=20)
        self.h["CSRF-Token"] = nonce(self.s, "/login")


# ---------------------------------------------------------------------------
print("== the derivation is the one the page uses ==")
check(token_text(KNOWN[0]) == KNOWN[1], f"the test vector's token is {KNOWN[1]}")
check(signin_value(KNOWN[0]) == KNOWN[2], "and its sign-in value is the known one")
RAW = secrets.token_bytes(16)
SHOWN = token_text(RAW)
SIGNIN = signin_value(RAW)
check(re.fullmatch(r"(?:[0-9A-HJKMNP-TV-Z]{4}-){6}[0-9A-HJKMNP-TV-Z]{4}", SHOWN) is not None,
      "a token is seven groups of four in Crockford's alphabet")

# ---------------------------------------------------------------------------
print("\n== the login page's participant side is the door ==")
visitor = requests.Session()
front = visitor.get(BASE + "/login", timeout=20)
side = front.text.split('class="epi-login-main"', 1)[-1].split("epi-login-foot", 1)[0]
check(front.status_code == 200 and "data-ws-door" in side, "the door is drawn on /login")
check(len(re.findall(r"<input\b", side)) == 3 and 'id="ws-door-code"' in side
      and 'type="file"' in side and 'type="checkbox"' in side,
      "one box to type in, a file control and a tick box")
check('name="name"' not in side and 'name="email"' not in side
      and 'type="password"' not in side and 'name="password"' not in side,
      "no name, no e-mail, no password")
check(re.search(r'data-nonce="[0-9a-f]{64}"', side) is not None,
      "it carries the anonymous session's nonce")
check('href="/login?staff=1"' in front.text, "the staff form is one link away")
staff = visitor.get(BASE + "/login?staff=1", timeout=20).text
check('name="password"' in staff and "data-ws-door" not in staff,
      "and that side is the password form, with no door on it")
for asset in ("door.js", "vendor/qrcode.js"):
    r = visitor.get(f"{BASE}/plugins/workshop/assets/{asset}", timeout=20)
    check(r.status_code == 200 and len(r.content) > 1000, f"assets/{asset} is served")

# ---------------------------------------------------------------------------
print("\n== a code and a sign-in value make an account ==")
first = Browser()
wrong_code = first.post("/code", json={"code": f"NOPE-{RUN}"})
REFUSAL = (wrong_code.status_code, wrong_code.content)
check(wrong_code.status_code == 403, "a wrong code is refused: this is the one answer")


def refused(r):
    return (r.status_code, r.content) == REFUSAL


check(refused(first.signin(SIGNIN)), "before the account exists, its sign-in value opens nothing")
made = first.post("/register", json={"code": CODE, "signin": SIGNIN})
check(made.status_code == 200 and made.json()["success"] is True, "registered with the code")
ME = first.who()
if ME is None:
    sys.exit("registration did not sign the browser in")
made_users.append(ME["id"])
check(NAME.fullmatch(ME["name"]) is not None and made.json()["data"]["name"] == ME["name"],
      f"signed in as {ME['name']}, a name nobody typed")
NEXT = made.json()["data"]["next"]
check(NEXT.startswith("/") and not NEXT.startswith("//"), f"and sent on to {NEXT}")

# ---------------------------------------------------------------------------
print("\n== signed out, the same value opens the same account ==")
first.out()
check(first.who() is None, "signed out")
back = first.signin(SIGNIN)
check(back.status_code == 200 and back.json() == {"success": True,
                                                  "data": {"next": NEXT, "name": ME["name"]}},
      "the sign-in value signs in, and the answer names the account")
check((first.who() or {}).get("id") == ME["id"], "it is the same account")
again = first.signin(SIGNIN)
check(again.status_code == 200 and (first.who() or {}).get("id") == ME["id"],
      "sent again from the browser that is already in: still a yes, still the same account")
other = Browser()
check(other.signin(SIGNIN).status_code == 200 and (other.who() or {}).get("id") == ME["id"],
      "from another browser too")
check((first.who() or {}).get("id") == ME["id"], "and the first browser stays signed in")
bare = requests.Session()
bare.get(BASE + "/login", timeout=20)
r = bare.post(BASE + DOOR + "/signin", json={"signin": SIGNIN}, timeout=20)
check(r.status_code == 403 and "success" not in r.text,
      "without the session's nonce core refuses the request")
form = Browser()
r = form.s.post(BASE + DOOR + "/signin", timeout=20,
                data={"signin": SIGNIN, "nonce": form.h["CSRF-Token"]})
check(refused(r) and form.who() is None, "sent as a form and not as JSON: the one answer")
r = form.s.get(BASE + DOOR + "/signin", timeout=20, allow_redirects=False)
check(r.status_code in (404, 405), f"and the address answers nothing to a GET: {r.status_code}")

# ---------------------------------------------------------------------------
print("\n== every refusal is the one answer ==")
stranger = Browser()
for label, body in (
        ("a value nobody registered", {"signin": secrets.token_hex(32)}),
        ("the value in capitals", {"signin": SIGNIN.upper()}),
        ("the value with a space after it", {"signin": SIGNIN + " "}),
        ("the value cut short", {"signin": SIGNIN[:63]}),
        ("the value with one more character", {"signin": SIGNIN + "0"}),
        ("the token itself, which is not what is sent", {"signin": SHOWN}),
        ("the stored hash", {"signin": hashlib.sha256(bytes.fromhex(SIGNIN)).hexdigest()}),
        ("an empty value", {"signin": ""}),
        ("a number", {"signin": 12}),
        ("a list", {"signin": [SIGNIN]}),
        ("no value", {}),
        ("the value under another name", {"token": SIGNIN})):
    check(refused(stranger.signin(body.get("signin")) if set(body) == {"signin"}
                  else stranger.post("/signin", json=body)), label)
check(refused(stranger.s.post(BASE + DOOR + "/signin", headers=stranger.h,
                              data="[1, 2]", timeout=20)), "a body that is a list")
check(refused(stranger.s.post(BASE + DOOR + "/signin", headers=stranger.h,
                              data="{", timeout=20)), "a body that is not JSON")
check(stranger.who() is None, "and none of that signed anybody in")

api("PATCH", f"/users/{ME['id']}", json={"banned": True})
banned = Browser()
check(refused(banned.signin(SIGNIN)) and banned.who() is None,
      "a banned account: the one answer, and not a word about why")
api("PATCH", f"/users/{ME['id']}", json={"banned": False})
check(banned.signin(SIGNIN).status_code == 200, "unbanned: it opens again")

# ---------------------------------------------------------------------------
print("\n== coming back is not throttled ==")
hammer = Browser()
answers = {hammer.signin(secrets.token_hex(32)).status_code for _i in range(320)}
check(answers == {403}, "320 wrong sign-in values in a row are 320 refusals")
check(hammer.signin(SIGNIN).status_code == 200, "and the right one still opens, at once")
room = Browser()
check(room.post("/code", json={"code": CODE}).status_code == 200,
      "and the room typing its code was never shut out by them")

# ---------------------------------------------------------------------------
print("\n== an account with no name cannot type one ==")
mine = Browser()
mine.signin(SIGNIN)
page = mine.s.get(BASE + "/settings", timeout=20, allow_redirects=False)
check(page.status_code == 302 and page.headers["Location"].endswith(NEXT),
      f"/settings leads back in: {page.status_code} {page.headers.get('Location')}")
home = mine.s.get(BASE + NEXT, timeout=20).text
check('href="/settings"' not in home and 'href="/logout"' in home,
      "the navbar has no Settings entry, and still has the way out")
for label, body in (("a name", {"name": "Jean Dupont"}),
                    ("an e-mail", {"email": "jean.dupont@example.com"}),
                    ("a password", {"password": "motdepasse", "confirm": "motdepasse"}),
                    ("a website", {"website": "https://example.com/jean"}),
                    ("nothing at all", {})):
    r = mine.s.patch(BASE + "/api/v1/users/me", headers=mine.h, json=body, timeout=20)
    check(r.status_code == 403 and r.json() == {"success": False}, f"saving {label} is refused")
seen = api("GET", f"/users/{ME['id']}").json()["data"]
check(seen["name"] == ME["name"] and seen["email"].endswith("@token.invalid")
      and not seen.get("website"), "and the account is as it was made")
check(mine.me().status_code == 200, "reading the profile still works")
s = requests.Session()
s.post(BASE + "/login", timeout=20,
       data={"name": ME["name"], "password": "motdepasse",
             "nonce": nonce(s, "/login?staff=1"), "_submit": "Submit"})
check(s.get(BASE + "/api/v1/users/me", timeout=20, allow_redirects=False).status_code != 200,
      "no password was set: core's own form does not open the account")
check(admin.get(BASE + "/settings", timeout=20, allow_redirects=False).status_code == 200
      and 'href="/settings"' in admin.get(BASE + "/workshop", timeout=20).text,
      "staff keep their settings page and the link to it")

# ---------------------------------------------------------------------------
print("\n== the database holds no token and no sign-in value ==")
export = admin.get(BASE + "/admin/export", timeout=300)
archive = zipfile.ZipFile(io.BytesIO(export.content))
tables = {n: archive.read(n).decode("utf-8", "replace")
          for n in archive.namelist() if n.startswith("db/")}
everything = "\n".join(tables.values())
check(export.status_code == 200 and "db/workshop_token_account.json" in tables
      and "db/users.json" in tables, f"core's export holds every table ({len(tables)})")
stored = hashlib.sha256(bytes.fromhex(SIGNIN)).hexdigest()
check(stored in tables.get("db/workshop_token_account.json", ""),
      "the account's row holds the SHA-256 of the sign-in value")
check(SIGNIN not in everything, "the sign-in value is in no table")
check(SHOWN not in everything and SHOWN.replace("-", "") not in everything
      and RAW.hex() not in everything, "neither is the token, in any spelling")
check("Jean Dupont" not in everything and "jean.dupont" not in everything,
      "nor anything the refused requests tried to save")

# ---------------------------------------------------------------------------
print("\n== with the plugin switched off ==")
switch("off")
try:
    off = Browser()
    r = off.s.post(BASE + DOOR + "/signin", headers=off.h, json={"signin": SIGNIN}, timeout=20)
    check(r.status_code == 404, f"the door is not there: {r.status_code}")
    check("data-ws-door" not in off.s.get(BASE + "/login", timeout=20).text,
          "and the login page is core's again")
    check((mine.who() or {}).get("id") == ME["id"], "an account already in stays in")
    page = mine.s.get(BASE + "/settings", timeout=20, allow_redirects=False)
    check(page.status_code == 302, "it still has no settings page")
    r = mine.s.patch(BASE + "/api/v1/users/me", headers=mine.h,
                     json={"name": "Jean Dupont"}, timeout=20)
    check(r.status_code == 403, "and still cannot type a name")
finally:
    switch("on")
check(plugin_on() and Browser().signin(SIGNIN).status_code == 200,
      "switched back on: the token opens the account again")

print("\n" + ("ALL GREEN" if not fails else f"FAILURES: {fails}"))
sys.exit(1 if fails else 0)
