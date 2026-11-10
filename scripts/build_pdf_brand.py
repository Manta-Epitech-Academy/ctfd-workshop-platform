#!/usr/bin/env python3
"""Build plugins/workshop/assets/pdf-brand.json: the brand, as a PDF takes it.

The token door hands the personal code out as a PDF written in the browser
(assets/pdf.js, PLAN.md §60.4). For that page to look like the platform it
needs the platform's three typefaces and the wordmark, and a PDF takes neither
a woff2 nor an SVG. This script derives both from the files the plugin already
vendors, so there is still one source for each:

  assets/fonts/*.woff2                      -> four TrueType subsets, base64
  assets/brand/EPITECH-LOGO-BLANC-2025.svg  -> its outline, as path operators

A font is cut down to what the page can write: the characters of WinAnsi (the
encoding pdf.js writes in), with no hinting and no layout tables, which a PDF
does not use. The monospace keeps only what a personal code is made of. Each
comes with the numbers its PDF font descriptor needs and the advance width of
every character, which pdf.js uses to wrap and centre text.

Run it again when a font or the logo changes, and commit the result:

    pip install fonttools brotli
    python scripts/build_pdf_brand.py

Nothing at runtime needs fonttools: the page fetches the JSON.
"""
import base64
import io
import json
import pathlib
import re
import sys

from fontTools import subset
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "plugins" / "workshop" / "assets"
OUT = ASSETS / "pdf-brand.json"

# WinAnsi (Windows-1252) leaves five codes undefined.
WINANSI = {}
for _code in range(32, 256):
    try:
        WINANSI[_code] = ord(bytes([_code]).decode("cp1252"))
    except UnicodeDecodeError:
        pass

# A personal code: Crockford's alphabet is inside these, and the dash.
CODE_CHARACTERS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-"

# role in pdf.js -> (file, weight for a variable font, PostScript name, kept characters)
FONTS = {
    "display": ("anton-latin-400-normal.woff2", None, "Anton-Regular", None),
    "regular": ("ibm-plex-sans-latin-wght-normal.woff2", 400, "IBMPlexSans", None),
    "bold": ("ibm-plex-sans-latin-wght-normal.woff2", 600, "IBMPlexSans-SemiBold", None),
    "mono": ("space-mono-latin-700-normal.woff2", None, "SpaceMono-Bold", CODE_CHARACTERS),
}


def cut(font, characters):
    """The font with only these characters, as TrueType bytes."""
    options = subset.Options()
    options.hinting = False
    options.layout_features = []
    options.name_IDs = []
    options.notdef_outline = True
    options.glyph_names = False
    options.drop_tables += ["GSUB", "GPOS", "GDEF", "kern", "STAT", "meta", "DSIG"]
    cutter = subset.Subsetter(options)
    cutter.populate(unicodes=characters)
    cutter.subset(font)
    font.flavor = None
    out = io.BytesIO()
    font.save(out)
    return out.getvalue()


def build_font(role, index):
    file, weight, name, kept = FONTS[role]
    font = TTFont(ASSETS / "fonts" / file)
    if weight is not None:
        font = instancer.instantiateVariableFont(font, {"wght": weight})
    characters = [ord(c) for c in kept] if kept else sorted(set(WINANSI.values()))
    data = cut(font, characters)

    font = TTFont(io.BytesIO(data))
    per_em = 1000 / font["head"].unitsPerEm
    cmap = font.getBestCmap()
    advance = font["hmtx"].metrics
    widths = []
    for code in range(32, 256):
        glyph = cmap.get(WINANSI.get(code))
        widths.append(round(advance[glyph][0] * per_em) if glyph else 0)
    head, os2 = font["head"], font["OS/2"]
    fixed = len({w for w in widths if w}) == 1
    return {
        # A subset's name starts with six letters and a plus (PDF 32000, 9.6.4).
        "name": "WSDOO%s+%s" % (chr(ord("A") + index), name),
        "widths": widths,
        "box": [round(v * per_em) for v in (head.xMin, head.yMin, head.xMax, head.yMax)],
        "ascent": round(font["hhea"].ascent * per_em),
        "descent": round(font["hhea"].descent * per_em),
        "cap": round(getattr(os2, "sCapHeight", 0) * per_em) or round(font["hhea"].ascent * per_em),
        # No table holds the stem width; this is the usual guess from the weight.
        "stem": round(50 + (os2.usWeightClass / 65) ** 2),
        # Nonsymbolic, and fixed pitch where it is.
        "flags": 32 + (1 if fixed else 0),
        "bytes": len(data),
        "data": base64.b64encode(data).decode("ascii"),
    }


def build_logo():
    """The wordmark's outline: [operator, numbers...], y up, from its own corner."""
    svg = (ASSETS / "brand" / "EPITECH-LOGO-BLANC-2025.svg").read_text()
    pen = RecordingPen()
    for tag in re.findall(r"<path\b[^>]*>", svg):
        d = re.search(r'\bd="([^"]*)"', tag).group(1)
        move = re.search(r'transform="translate\(([-\d.]+)[ ,]+([-\d.]+)\)"', tag)
        dx, dy = (float(move.group(1)), float(move.group(2))) if move else (0.0, 0.0)
        if "transform=" in tag and not move:
            sys.exit("a transform this script does not read: " + tag[:120])
        parse_path(d, TransformPen(pen, (1, 0, 0, 1, dx, dy)))
    points = [p for _, args in pen.value for p in args]
    left = min(x for x, _ in points)
    right = max(x for x, _ in points)
    top = min(y for _, y in points)
    bottom = max(y for _, y in points)

    def place(point):
        return [round(point[0] - left, 1), round(bottom - point[1], 1)]

    ops = []
    for kind, args in pen.value:
        if kind == "moveTo":
            ops.append(["m"] + place(args[0]))
        elif kind == "lineTo":
            ops.append(["l"] + place(args[0]))
        elif kind == "curveTo" and len(args) == 3:
            ops.append(["c"] + [v for point in args for v in place(point)])
        elif kind in ("closePath", "endPath"):
            ops.append(["h"])
        else:
            sys.exit("a path segment this script does not write: %s %r" % (kind, args))
    return {"width": round(right - left, 1), "height": round(bottom - top, 1), "ops": ops}


def main():
    brand = {
        "fonts": {role: build_font(role, i) for i, role in enumerate(FONTS)},
        "logo": build_logo(),
    }
    OUT.write_text(json.dumps(brand, separators=(",", ":"), sort_keys=True) + "\n")
    for role, font in brand["fonts"].items():
        print("%-8s %-28s %6d bytes" % (role, font["name"], font["bytes"]))
    print("logo     %sx%s, %d segments" % (
        brand["logo"]["width"], brand["logo"]["height"], len(brand["logo"]["ops"])))
    print("%s: %d bytes" % (OUT.relative_to(ASSETS.parent.parent.parent), OUT.stat().st_size))


if __name__ == "__main__":
    main()
