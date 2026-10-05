"""Options a runtime reads when it starts (PLAN.md §48).

`runtime.params` in a subject.yaml reaches the runtime in the `init` message
(docs/RUNTIME_PROTOCOL.md), which is after it has started. That is the right
time for data: which bundle to boot, which exercise is which. It is too late
for anything that decides what the runtime *is* when it comes up: a panel that
must not be there would be drawn and then taken away, after its interpreter
had begun to download.

So a few named params are **start-up options**: the platform also writes them
into the address of the frame, where the runtime reads them before it draws
anything. The address is the one channel a web app has that is there from the
first line it runs, and it is the same one somebody uses to open the runtime
on its own, with no platform around it.

An allowlist, per runtime, with the values each option takes. `params` also
carries things that must never be in an address (the token secret, §21), so
"forward everything" is not on the table; and a value nobody recognises is
dropped rather than passed on, so a typo in a subject gives the runtime's
default and a line in the log instead of an address the runtime has to guess
at.

Pure, and it imports nothing from CTFd: the page and the declaration both
call it, and scripts/runtime_options_check.py runs it with no instance.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# YAML 1.1 reads a bare `off`, `no` or `false` as the boolean False, and that
# is exactly what an author will write to turn something off. So every option
# is normalised from what YAML made of it, not from what was typed.
_OFF = (False, None, "off", "none", "false", "no", "0", 0)

# option -> {accepted value: what is written in the address}
START_OPTIONS = {
    "tic80": {
        # The panel under the code editor (tic80-web-editor_runtime,
        # src/layout/replConfig.ts): a REPL in one of three languages, or none.
        "repl": {"lua": "lua", "py": "py", "python": "py", "js": "js",
                 "javascript": "js", "off": "off"},
    },
}


def _normalise(value):
    if isinstance(value, bool) or value is None:
        return "off" if value in (False, None) else None
    text = str(value).strip().lower()
    return "off" if text in _OFF else text


def start_options(runtime_id, params):
    """`(options, rejected)` for one runtime declaration.

    `options` is what goes in the address, `rejected` the `(name, value)` pairs
    a subject set that this runtime does not take.
    """
    known = START_OPTIONS.get(runtime_id) or {}
    options, rejected = {}, []
    # `params: [repl]` is a mistake in a subject.yaml, and this runs on every
    # page of the instance: it reads as no option, it does not raise.
    if not isinstance(params, dict):
        params = {}
    for name, accepted in known.items():
        if name not in params:
            continue
        value = _normalise(params[name])
        if value in accepted:
            options[name] = accepted[value]
        else:
            rejected.append((name, params[name]))
    return options, rejected


def with_start_options(src, runtime_id, params):
    """`src` with this runtime's start-up options in its query string.

    An option already in the address is replaced, so applying this twice, or to
    a `src` an author wrote by hand, cannot say two things at once.
    """
    options, _ = start_options(runtime_id, params)
    if not options:
        return src
    parts = urlsplit(src)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in options]
    query += sorted(options.items())
    return urlunsplit(parts._replace(query=urlencode(query)))
