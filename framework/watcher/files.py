"""A campaign's files: the records it keeps, its pictures, and the tree under each of
its roots."""

import glob
import html
import json
import os
import re
import urllib.parse

from .workspace import LAB_DIR, file_mtime, workspace


# Files worth opening while a run is in flight. Anything else in the workspace is
# listed but not offered as a tab: run directories, caches, figures.
# ANNOUNCEMENTS.md is not among them: what is on it is in the chat.
READABLE = ("LOGBOOK.md", "findings.md", "JOURNAL.md", "REVIEWS.md", "results.jsonl",
            "jobs.jsonl")
# What a record that does not exist yet means.
NOT_YET = {"jobs.jsonl": "no jobs submitted yet", "results.jsonl": "no results yet"}
TAIL_BYTES = 400_000        # of a file view; the log is followed from an offset instead
# Browsing the whole workspace, as against the records above. Caches are listed by the
# filesystem but are never what was being looked for.
TREE_SKIP = {"__pycache__", ".git", ".ipynb_checkpoints", ".mypy_cache"}
SNIFF_BYTES = 8192          # enough to decide whether a file is text


def is_binary(path):
    """Decided from the bytes rather than from the name, so a format nobody thought to
    list is still not spilled into the pane as mojibake."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(SNIFF_BYTES)
    except OSError:
        return False
    if b"\0" in chunk:
        return True
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        # A multi-byte character split by the read boundary is not a binary file, so
        # the tail is dropped before calling it one.
        try:
            chunk[:-3].decode("utf-8")
        except UnicodeDecodeError:
            return True
    return False

try:                        # in requirements.txt; without it records are plain text
    import markdown as _markdown
except Exception:
    _markdown = None
HAS_MARKDOWN = _markdown is not None


def tree_roots(campaign):
    """The two places a campaign's files live: the workspace its runs write into, and
    the campaign directory it was started from. A workspace copied without its campaign
    has only the first, so the page is told which roots exist rather than assuming. A
    workspace named other than its campaign, such as one of several under a group, names
    its campaign in its runs' meta.json."""
    roots = {"ws": workspace(campaign)}
    names = [campaign]
    for m in glob.glob(os.path.join(workspace(campaign), "runs", "*", "meta.json")):
        try:
            with open(m) as f:
                names.append(json.load(f).get("campaign") or "")
        except (OSError, ValueError):
            continue
        break
    for name in names:
        # The meta's name becomes a path: only a campaign directory's own name may.
        if os.path.normpath(name).startswith((".", "/")):
            continue
        defn = os.path.join(LAB_DIR, "campaigns", name)
        if name and os.path.isdir(defn):
            roots["camp"] = defn
            break
    return roots


def root_path(campaign, root, rel):
    """Resolve `rel` under one of the campaign's roots, or None if it lands outside it.
    Resolved before the check, so neither a path from the page nor a symlink under the
    root reaches the rest of the filesystem."""
    base = tree_roots(campaign).get(root)
    if base is None:
        return None
    base = os.path.realpath(base)
    path = os.path.realpath(os.path.join(base, rel))
    if path != base and not path.startswith(base + os.sep):
        return None
    return path


PICTURE_DIRS = ("outputs", "figures")
PICTURE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


def pictures(campaign):
    """Every image under the workspace's outputs/ (what jobs returned) and figures/ (what
    the agents drew), newest first, each with a caption: for a job's output, the job and
    what it was given, from jobs.jsonl; otherwise its name."""
    ws = workspace(campaign)
    jobs = {}
    try:
        with open(os.path.join(ws, "jobs.jsonl")) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("event") in ("submit", "local_submit"):
                    local = "local_" if r["event"] == "local_submit" else ""
                    jobs[(r.get("run"), f"{local}{r.get('job_id')}")] = (
                        r.get("key") or json.dumps(r.get("args"))[:200])
    except OSError:
        pass
    out = []
    for top in PICTURE_DIRS:
        for d, _, names in os.walk(os.path.join(ws, top)):
            for n in names:
                if not n.lower().endswith(PICTURE_TYPES):
                    continue
                path = os.path.join(d, n)
                rel = os.path.relpath(path, ws)
                parts = rel.split(os.sep)
                caption = n
                # outputs/<run>/job_<id>/<name>
                if len(parts) == 4 and parts[0] == "outputs" and parts[2].startswith("job_"):
                    job = parts[2][4:]
                    caption = f"job {job}"
                    if (parts[1], job) in jobs:
                        caption += " \u00b7 " + jobs[(parts[1], job)]
                out.append({"path": rel, "caption": caption, "mtime": file_mtime(path)})
    out.sort(key=lambda p: -p["mtime"])
    return out


def campaign_tabs(campaign):
    """The record tabs a campaign asks for (campaign.json: "watch": {"tabs": [...]}),
    in its order, or None for the usual ones. A name is a file in the workspace, or
    "pictures"."""
    camp = tree_roots(campaign).get("camp")
    try:
        with open(os.path.join(camp, "campaign.json")) as f:
            tabs = (json.load(f).get("watch") or {}).get("tabs")
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(tabs, list):
        return None
    return [str(n) for n in tabs if n == "pictures"
            or (n and not os.path.normpath(str(n)).startswith((".", "/")))]


def listing(campaign, root, rel):
    """One directory. Unsorted: the page orders it, so switching between name and date
    costs no round trip."""
    path = root_path(campaign, root, rel)
    if path is None or not os.path.isdir(path):
        return {"dirs": [], "files": [], "error": "no such directory"}
    dirs, files = [], []
    try:
        for e in os.scandir(path):
            if e.name in TREE_SKIP:
                continue
            try:
                st = e.stat()
                if e.is_dir():
                    dirs.append({"name": e.name, "mtime": int(st.st_mtime)})
                else:
                    files.append({"name": e.name, "size": st.st_size,
                                  "mtime": int(st.st_mtime)})
            except OSError:          # vanished mid-scan, or unreadable
                continue
    except OSError as e:
        return {"dirs": [], "files": [], "error": str(e)}
    return {"dirs": dirs, "files": files}


def tree(campaign, keys):
    """Every directory the page currently has open, in one answer. Keys are
    `root:relative/path`. One request per redraw rather than one per node, so an
    expanded tree costs a single round trip."""
    roots = tree_roots(campaign)
    out = {"roots": [r for r in ("ws", "camp") if r in roots], "nodes": {}}
    for k in keys:
        root, _, rel = k.partition(":")
        if root in roots:
            out["nodes"][k] = listing(campaign, root, rel)
    return out


def render_markdown(text, campaign):
    """A record as its author meant it to read -- headings, tables, figures. Falls back
    to the text itself if anything goes wrong: a viewer that shows nothing is worse than
    one that shows the file."""
    try:
        html_out = _markdown.markdown(text, extensions=["tables", "fenced_code"])
    except Exception:
        return "<pre>" + html.escape(text) + "</pre>"
    # Figures are referenced relative to the workspace, which only this server can read,
    # and the workspace is the campaign's -- so the route is told which one. These go
    # into attributes of the HTML below, so the separator is written as an entity.
    owner = "&amp;c=" + urllib.parse.quote(campaign)

    def _img(m):
        src = urllib.parse.quote(m.group("src"))
        alt = m.group(0)
        alt = re.search(r'alt="([^"]*)"', alt)
        return (f'<a href="/image?name={src}{owner}" target="_blank">'
                f'<img src="/image?name={src}{owner}" alt="{alt.group(1) if alt else ""}"></a>')

    html_out = re.sub(r'<img[^>]*?src="(?!https?:|/)(?P<src>[^"]+)"[^>]*/?>', _img, html_out)

    # A record may link a figure rather than embed it. The link is relative to the
    # workspace, which only this server can read, so point it at the same route.
    return re.sub(
        r'<a href="(?!https?:|/)(?P<href>[^"]+\.(?:png|jpg|jpeg|gif|svg|webp))"',
        lambda m: '<a target="_blank" href="/image?name='
                  f'{urllib.parse.quote(m.group("href"))}{owner}"',
        html_out, flags=re.I)
