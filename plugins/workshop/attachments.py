"""A file to download, linked from the prose (convention §3.8c).

The author writes an ordinary link to a file of the repo:

    Télécharge [le kit de départ](fichiers/kit.zip) et décompresse-le.

The sync uploaded the file as it does an image — once, content-hashed, under
`/files/ws-<subject>/<stem>-<sha1[:8]>.<ext>` — and rewrote the link's target
(ws_parser.rewrite_asset_refs). On GitHub the link opens the file in the repo;
here it would be a bare blue word. So every link to one of those uploads is
turned into a download card: an icon for the kind of file, the author's text,
then the file's name and size. `download=` restores the name the author gave
the file, without the content hash the location carries.

Two sizes of card. A link inside a sentence gets a compact one, which flows
with the text. A link that stands alone in its paragraph — the author put it
on its own line — gets a block card, wider, with a « Télécharger » affordance:
that is the author's option, chosen by layout, and it reads the same on GitHub.

The size is read from the upload folder when the instance keeps its files on
disk (CTFd's default uploader); with another provider the card simply has no
size. It is cached per location, since a location is content-addressed and
never changes its bytes.

Pure helper, hooked where every rendered surface passes
(external_links.load_external_links), like scene.py.
"""
import html
import os
import re

from flask import current_app, has_app_context

# Only the sync's uploads: an author's own `/files/...` link is left alone.
_UPLOAD = re.compile(r"<a\b([^>]*?)\bhref\s*=\s*\"(/files/ws-[^\"]+)\"([^>]*)>(.*?)</a>",
                     re.IGNORECASE | re.S)
# A card that is its paragraph's whole content: `<p>` + one anchor + `</p>`.
_ALONE = re.compile(r"<p>\s*(<a class=\"ws-attachment\")(.*?</a>)\s*</p>", re.S)
_HASHED = re.compile(r"^(.*)-[0-9a-f]{8}(\.[^.]+)?$")
_ATTRS_TO_DROP = re.compile(r"""\s*\b(?:class|download)\s*=\s*(?:"[^"]*"|'[^']*')""",
                            re.IGNORECASE)

_ICONS = (
    ("📦", {"zip", "tar", "gz", "tgz", "7z", "rar", "xz", "bz2"}),
    ("📄", {"pdf"}),
    ("📝", {"txt", "md", "rtf", "doc", "docx", "odt"}),
    ("📊", {"csv", "xls", "xlsx", "ods", "json", "yaml", "yml", "xml"}),
    ("🖼️", {"png", "jpg", "jpeg", "gif", "webp", "svg", "bmp"}),
    ("🎵", {"mp3", "wav", "ogg", "flac", "m4a"}),
    ("🎬", {"mp4", "webm", "mov", "mkv"}),
    ("🧊", {"glb", "gltf", "obj", "stl", "vox"}),
    ("💻", {"lua", "py", "js", "ts", "c", "h", "cpp", "java", "rs", "go", "sh", "html",
            "css", "ino", "tic", "wasm"}),
)
_DEFAULT_ICON = "📎"

_sizes = {}


def _icon(ext):
    for icon, exts in _ICONS:
        if ext in exts:
            return icon
    return _DEFAULT_ICON


def _original_name(location):
    """`kit-1a2b3c4d.zip` -> `kit.zip`: the name without the sync's content hash."""
    name = location.rsplit("/", 1)[-1]
    m = _HASHED.match(name)
    return (m.group(1) + (m.group(2) or "")) if m else name


def _size(location):
    """The file's size in bytes, or None when it cannot be read here."""
    if location in _sizes:
        return _sizes[location]
    size = None
    if has_app_context():
        folder = current_app.config.get("UPLOAD_FOLDER")
        if folder and current_app.config.get("UPLOAD_PROVIDER", "filesystem") == "filesystem":
            try:
                size = os.path.getsize(os.path.join(folder, location))
            except OSError:
                size = None
    _sizes[location] = size
    return size


def human_size(size):
    """`12 Ko`, `3,4 Mo`: French units, one decimal above a kilobyte when it matters."""
    if size is None:
        return ""
    if size < 1024:
        return f"{size} o"
    for unit in ("Ko", "Mo", "Go"):
        size /= 1024
        if size < 1024 or unit == "Go":
            break
    text = f"{size:.1f}".replace(".", ",") if size < 10 else f"{size:.0f}"
    return f"{text.rstrip('0').rstrip(',')} {unit}"


def _card(match):
    before, href, after, inner = match.groups()
    location = href[len("/files/"):]
    name = _original_name(location)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    attrs = _ATTRS_TO_DROP.sub("", before + after).strip()
    meta = html.escape(name)
    size = human_size(_size(location))
    if size:
        meta += f" · {size}"
    return (f'<a class="ws-attachment" href="{href}" download="{html.escape(name, quote=True)}"'
            f'{(" " + attrs) if attrs else ""}>'
            f'<span class="ws-attachment-icon" aria-hidden="true">{_icon(ext)}</span>'
            f'<span class="ws-attachment-text"><span class="ws-attachment-label">{inner}</span>'
            f'<span class="ws-attachment-meta">{meta}</span></span></a>')


def _block(match):
    return (f'<p class="ws-attachment-alone">{match.group(1).replace("ws-attachment", "ws-attachment ws-attachment-block")}'
            f'{match.group(2)[:-len("</a>")]}'
            f'<span class="ws-attachment-action" aria-hidden="true">Télécharger ⬇</span></a></p>')


def place_attachments(rendered):
    """Turn every link to a synced upload into a download card."""
    text = str(rendered or "")
    if "/files/ws-" not in text:
        return text
    return _ALONE.sub(_block, _UPLOAD.sub(_card, text))
