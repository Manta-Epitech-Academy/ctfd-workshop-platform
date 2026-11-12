"""The certificate: what a participant did here, signed, under the name nobody typed.

An account made at the token door has no name and no e-mail (PLAN.md §60, §61),
so nothing outside this instance knows what « Renard-Vert-42 » did on it. The
certificate is how that leaves, and it leaves in the participant's hands: a
button on the workshop pages, a PDF, and nothing sent anywhere (§63).

The PDF says what a certificate says, to a person: who, which workshop, when,
where, how far. It also carries the same facts as one line of **signed text**,
in the file and in its QR code. Whoever is later given the file can check the
signature against this platform's public key and know the numbers were not
typed by the person holding it.

**Who draws what.** This module decides every fact and every sentence, signs,
and answers JSON. The browser draws the page (assets/certificate.js, with the
writer the token door already uses, assets/pdf.js). So there is no PDF library
in the image and no headless browser beside it.

**The signed text** is a JWS in its compact form (RFC 7515), signed with
Ed25519 (`"alg": "EdDSA"`, RFC 8037): three base64url parts and two dots,
which any JOSE library reads. Its payload:

    v         1
    iss       this instance's address
    sub       the account's name on it: « Renard-Vert-42 »
    iat       when it was signed, in seconds
    jti       a random serial: two downloads are two certificates
    contents  [{slug, title, xp, done, total}]: one entry per content. One
              today, the content this instance serves; a list so that the
              contents an instance served before can be added (issue #73)
    event     {name, campus, date} from the registration code the account
              came in on, each only when the code says it

Counts only, not the list of steps. `xp`, `done` and `total` are progress.py's,
the numbers the page shows.

**Downloading again is always possible**, and gives a newer certificate of
the same account. Nothing is stored here: no serial, no count. Telling a newer
one from an older one, and one holder from another, belongs to whoever takes
the file in: `iss` and `sub` together name one account, `iat` orders its
certificates.

**The key.** One Ed25519 key signs for every instance, so that one public key
checks every certificate: `WORKSHOP_CERTIFICATE_KEY` in the environment, a
secret given at deploy. It is the 32-byte seed in base64, or a PEM private key.
An instance deployed without it makes its own and keeps it in its config, so a
test instance still hands out certificates; they check against that instance's
key only. The public half is served as a JWK set at
`/api/v1/workshop/certificate/key`, and the `kid` in every certificate says
which key signed it.

**Only for an account made at the token door.** Its name is one the platform
chose. Any other account's name was typed by a person, and this file must not
be the place a real name lands in.
"""
import base64
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import Blueprint, jsonify, request
from flask_babel import format_date
from flask_babel import gettext as _
from flask_babel import ngettext

from CTFd.utils import get_config, set_config
from CTFd.utils.decorators import authed_only
from CTFd.utils.user import get_current_user

from .codes import CONFIG_CONTENT, RegistrationCode, TokenAccount
from .page import instance_title
from .progress import points_for_user, progress_for_user
from .tokendoor import token_account

workshop_certificate = Blueprint("workshop_certificate", __name__)

ENV_KEY = "WORKSHOP_CERTIFICATE_KEY"
# The key an instance makes for itself when the environment gives none.
CONFIG_KEY = "workshop_certificate_key"
VERSION = 1

_KEY = None


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _read_key(text):
    """A private key from the 32-byte seed in base64, or from a PEM."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("-----"):
        key = serialization.load_pem_private_key(text.encode("ascii"), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("not an Ed25519 key")
        return key
    padded = text.replace("-", "+").replace("_", "/")
    seed = base64.b64decode(padded + "=" * (-len(padded) % 4))
    return Ed25519PrivateKey.from_private_bytes(seed)


def signing_key():
    """The key this process signs with, read once.

    The environment's, or the one this instance made for itself. A key in the
    environment that cannot be read is an error to see at the first
    certificate, not a reason to sign quietly with another one.
    """
    global _KEY
    if _KEY is None:
        given = os.environ.get(ENV_KEY)
        if given and given.strip():
            _KEY = _read_key(given)
        else:
            key = _read_key(get_config(CONFIG_KEY))
            if key is None:
                seed = secrets.token_bytes(32)
                set_config(CONFIG_KEY, base64.b64encode(seed).decode("ascii"))
                # Two workers can both get here. Whatever the config holds
                # after that is the key: read it back.
                key = _read_key(get_config(CONFIG_KEY))
            _KEY = key
    return _KEY


def public_jwk(key=None):
    """The public half as a JWK. Its `kid` is the start of its thumbprint
    (RFC 7638): the same key has the same id on every instance."""
    raw = (key or signing_key()).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    x = _b64(raw)
    canonical = '{"crv":"Ed25519","kty":"OKP","x":"%s"}' % x
    kid = _b64(hashlib.sha256(canonical.encode("ascii")).digest())[:16]
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": kid,
            "alg": "EdDSA", "use": "sig"}


def sign(payload, key=None):
    """`payload` as a compact JWS."""
    key = key or signing_key()
    header = {"alg": "EdDSA", "kid": public_jwk(key)["kid"]}

    def part(value):
        return _b64(json.dumps(value, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8"))

    signed = part(header) + "." + part(payload)
    return signed + "." + _b64(key.sign(signed.encode("ascii")))


def certificate_state():
    """What the page does with the button: nothing ("") for an account that
    gets no certificate, "wait" until a first step is done, then "ready"."""
    if not token_account():
        return ""
    done, _total = progress_for_user(get_current_user().id)
    return "ready" if done else "wait"


def _cohort(user_id):
    """The registration code this account came in on, or None."""
    return (RegistrationCode.query
            .join(TokenAccount, TokenAccount.code_id == RegistrationCode.id)
            .filter(TokenAccount.user_id == user_id).first())


def _campus(text):
    """« Lille » from « Lille », « Epitech Lille » or « epitech lille »: the
    sentence says Epitech itself."""
    return re.sub(r"^\s*epitech\b[\s,:-]*", "", text or "", flags=re.I).strip()


def _sentence(title, day, campus, points, done, total):
    """What the participant did, as the certificate says it under their name."""
    if day and campus:
        first = _("took part in the workshop “%(title)s” on %(date)s at Epitech %(campus)s,",
                  title=title, date=day, campus=campus)
    elif day:
        first = _("took part in the workshop “%(title)s” on %(date)s,",
                  title=title, date=day)
    elif campus:
        first = _("took part in the workshop “%(title)s” at Epitech %(campus)s,",
                  title=title, campus=campus)
    else:
        first = _("took part in the workshop “%(title)s”,", title=title)
    second = ngettext(
        "and earned %(points)s XP by completing %(num)s step out of %(total)s.",
        "and earned %(points)s XP by completing %(num)s steps out of %(total)s.",
        done, points=points, total=total)
    return first + " " + second


@workshop_certificate.route("/api/v1/workshop/certificate", methods=["GET"])
@authed_only
def certificate():
    """Everything the page needs to draw this account's certificate, now."""
    if not token_account():
        return jsonify({"success": False}), 403
    user = get_current_user()
    done, total = progress_for_user(user.id)
    # A note read is not a step done (progress.py): nothing to certify yet.
    if not done:
        return jsonify({"success": False}), 409
    points = points_for_user(user.id)
    title = instance_title()
    row = _cohort(user.id)
    campus = _campus(row.campus) if row else ""
    event = {}
    if row and (row.event or "").strip():
        event["name"] = row.event.strip()
    if campus:
        event["campus"] = campus
    if row and row.event_date:
        event["date"] = row.event_date.isoformat()

    now = int(time.time())
    address = request.host_url.rstrip("/")
    payload = {
        "v": VERSION,
        "iss": address,
        "sub": user.name,
        "iat": now,
        "jti": secrets.token_hex(8).upper(),
        "contents": [{
            "slug": str(get_config(CONFIG_CONTENT) or ""),
            "title": title,
            "xp": points,
            "done": done,
            "total": total,
        }],
    }
    if event:
        payload["event"] = event

    today = format_date(datetime.utcfromtimestamp(now), format="long")
    day = format_date(row.event_date, format="long") if row and row.event_date else ""
    return jsonify({"success": True, "data": {
        "signed": sign(payload),
        "name": user.name,
        # Epitech's certificates say it in English, on every one of them.
        "institute": "European Institute of Information Technology",
        "heading": _("Certificate of participation"),
        "attribution": _("is hereby awarded this certificate"),
        "sentence": _sentence(title, day, campus, points, done, total),
        "association": _("Non-profit association (French law of 1901)"),
        "place": (_("Issued in %(campus)s, on %(date)s", campus=campus, date=today)
                  if campus else _("Issued on %(date)s", date=today)),
        "proof": _("Digitally signed by %(address)s",
                   address=re.sub(r"^https?://", "", address)),
        "serial": _("Certificate no. %(serial)s", serial="-".join(re.findall("....", payload["jti"]))),
        "file": _("certificate-%(name)s.pdf", name=user.name),
    }})


@workshop_certificate.route("/api/v1/workshop/certificate/key", methods=["GET"])
def key():
    """The public key, for whoever checks a certificate. Open to everybody:
    it is the half that is meant to be known."""
    return jsonify({"keys": [public_jwk()]})


def load_certificate(app):
    app.register_blueprint(workshop_certificate)
    # templates/workshop_certificate.html asks whether to draw the button.
    app.jinja_env.globals["certificate_state"] = certificate_state
