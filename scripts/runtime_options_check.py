#!/usr/bin/env python3
"""Do a subject's start-up options reach the address of its runtime?

    python3 scripts/runtime_options_check.py

No instance needed: plugins/workshop/runtime_options.py is pure, and this
script **is** its test suite (PLAN.md §48). What it holds the module to:

  - what an author writes to turn the REPL off, in every spelling YAML allows,
    becomes `repl=off`;
  - a value the runtime does not take is dropped and reported, never forwarded;
  - nothing but the allowlisted options is ever written into an address, the
    token secret least of all.

Shaped like the other checks: `check(cond, label)`, `ALL GREEN` or
`FAILURES: [...]`, non-zero exit on any failure.
"""
import importlib.util
import sys
from pathlib import Path

import yaml

path = Path(__file__).resolve().parent.parent / "plugins/workshop/runtime_options.py"
spec = importlib.util.spec_from_file_location("runtime_options", path)
ro = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ro)

fails = []
SRC = "/runtime/tic80/abc1234/"


def check(cond, label):
    print(("  OK   " if cond else "  FAIL ") + label)
    if not cond:
        fails.append(label)


print("-- off, as an author writes it")
for text in ("off", "false", "no", '"off"', "none", "OFF", "~"):
    params = yaml.safe_load(f"repl: {text}")
    got = ro.with_start_options(SRC, "tic80", params)
    check(got == SRC + "?repl=off", f"repl: {text:<6} -> {got}")

print("-- a language")
for text, want in (("lua", "lua"), ("py", "py"), ("python", "py"), ("js", "js"),
                   ("javascript", "js"), ("Python", "py")):
    got = ro.with_start_options(SRC, "tic80", {"repl": text})
    check(got == f"{SRC}?repl={want}", f"repl: {text:<10} -> {got}")

print("-- nothing asked, nothing added")
check(ro.with_start_options(SRC, "tic80", {}) == SRC, "no repl: the address is untouched")
check(ro.with_start_options(SRC, "tic80", None) == SRC, "no params at all: untouched")
check(ro.with_start_options("/runtime/v86/x/", "v86", {"repl": "off"}) == "/runtime/v86/x/",
      "another runtime takes no `repl`")

print("-- what must not get through")
got = ro.with_start_options(SRC, "tic80", {"repl": "off", "token_secret": "s3cret",
                                             "exercises": {"1": "a"}})
check(got == SRC + "?repl=off", f"only the allowlisted option is written: {got}")
options, rejected = ro.start_options("tic80", {"repl": "ruby"})
check(options == {} and rejected == [("repl", "ruby")], f"an unknown value is dropped and reported: {rejected}")
check(ro.with_start_options(SRC, "tic80", {"repl": "ruby"}) == SRC, "and the address is untouched")
options, rejected = ro.start_options("tic80", {"repl": True})
check(options == {} and rejected == [("repl", True)], "`repl: true` names no language: reported, default kept")

print("-- an address that already has a query")
got = ro.with_start_options(SRC + "?lang=en&repl=lua", "tic80", {"repl": "off"})
check(got == SRC + "?lang=en&repl=off", f"other parameters kept, the option replaced: {got}")
once = ro.with_start_options(SRC, "tic80", {"repl": "py"})
check(ro.with_start_options(once, "tic80", {"repl": "py"}) == once, "applying it twice changes nothing")

print("-- params that are not a mapping")
for wrong in (["repl"], "repl", 3, [("repl", "off")]):
    try:
        got = (ro.start_options("tic80", wrong), ro.with_start_options(SRC, "tic80", wrong))
    except Exception as error:  # the page would answer 500 on every load
        got = repr(error)
    check(got == (({}, []), SRC), f"`params: {wrong!r}` reads as no option, and does not raise: {got}")

print("ALL GREEN" if not fails else "FAILURES: " + repr(fails))
sys.exit(1 if fails else 0)
