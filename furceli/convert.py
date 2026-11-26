"""Conversion to and from PDF. Runs in an engine process, never in the window."""
from __future__ import annotations

import html
import io
import os
import re
import subprocess
import time
import unicodedata
import zipfile

import pymupdf

from . import files
from .convert_names import EXCEL, IMAGES, OOXML, OTHER, SLIDES, TEXT, WEB, WORD
from .engine import EngineError, _write

# A wrong password makes Office fail at once instead of waiting in an invisible prompt.
NO_PASSWORD = "⁠no-password⁠"


# Set by the worker: tells the window which helper programs were started, so that they
# can be ended if this process is killed half-way.
notify = None


def _tell(status, value):
    if notify is not None:
        notify((status, value))


MAX_UNPACKED = 1024 * 1024 * 1024    # an Office file, unpacked
MAX_INLINE = 200 * 1024 * 1024       # pictures and styles of one HTML page
BLOCKED = "file:///C:/furceli-blocked"


def _clean_rels(data: bytes) -> bytes:
    """One relationships part with everything outside the file pointing nowhere."""
    from lxml import etree
    try:
        root = etree.fromstring(data, etree.XMLParser(
            resolve_entities=False, load_dtd=False, no_network=True, huge_tree=False))
    except (etree.XMLSyntaxError, ValueError):
        raise EngineError("untrusted")
    if root.getroottree().docinfo.doctype:  # entities could hide anything
        raise EngineError("untrusted")
    for el in root.iter():
        if not isinstance(el.tag, str) or etree.QName(el).localname != "Relationship":
            continue
        kind = el.get("Type", "").rstrip("/").rsplit("/", 1)[-1].lower()
        target = el.get("Target", "").strip()
        outside = (el.get("TargetMode", "").strip().lower() == "external"
                   or re.match(r"[a-zA-Z][a-zA-Z0-9+.\-]*:|[\\/]{2}", target))
        if kind == "afchunk" or (outside and kind != "hyperlink"):
            el.set("Target", BLOCKED)
            el.set("TargetMode", "External")
        elif kind == "hyperlink" and target[:2] in ("\\\\", "//"):
            el.set("Target", BLOCKED)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _no_refresh(data: bytes) -> bytes:
    """Excel data connections and pivots must not refresh on opening."""
    from lxml import etree
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True))
    except (etree.XMLSyntaxError, ValueError):
        raise EngineError("untrusted")
    for el in root.iter():
        for key in ("refreshOnLoad", "refreshOnOpen"):
            if isinstance(el.tag, str) and key in el.attrib:
                el.set(key, "0")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SHEET_NS = ("http://schemas.openxmlformats.org/spreadsheetml/2006/main",
            "http://purl.oclc.org/ooxml/spreadsheetml/main")


def _part(data: bytes):
    from lxml import etree
    try:
        root = etree.fromstring(data, etree.XMLParser(
            resolve_entities=False, load_dtd=False, no_network=True, huge_tree=True))
    except (etree.XMLSyntaxError, ValueError):
        raise EngineError("untrusted")
    if root.getroottree().docinfo.doctype:
        raise EngineError("untrusted")
    return root


def _settled(data: bytes) -> bytes:
    """Word fields locked, so that opening asks nothing and fetches nothing.

    A field marked as out of date (or a document asking for all fields to be updated) makes
    Word fetch linked pictures and texts on opening, or stop at a security question that
    nobody sees. A locked field keeps the result saved in the file.
    """
    from lxml import etree
    if b"fld" not in data and b"updateFields" not in data:
        return data
    root = _part(data)
    w = "{%s}" % WORD_NS
    for el in list(root.iter(w + "fldChar", w + "fldSimple", w + "updateFields")):
        if el.tag == w + "updateFields":
            el.getparent().remove(el)
        elif el.tag == w + "fldSimple" or el.get(w + "fldCharType") == "begin":
            el.attrib.pop(w + "dirty", None)
            el.set(w + "fldLock", "1")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


FETCHES = re.compile(r"(?<![\w.])(?:_xlfn\.|_xlws\.)?(?:WEBSERVICE|IMAGE|STOCKHISTORY|RTD|CUBE[A-Z]*)"
                     r"\s*\(", re.I)


def _fetches(formula: str) -> bool:
    return bool(FETCHES.search(re.sub(r'"(?:[^"]|"")*"', '""', formula)))  # not in texts


def _no_fetch(data: bytes) -> bytes:
    """Formulas that fetch from outside (WEBSERVICE, IMAGE, RTD, ...) lose the formula and keep
    the value saved with them; everything else is still worked out as usual."""
    from lxml import etree
    if not FETCHES.search(data.decode("utf-8", "replace")):
        return data
    root = _part(data)
    ns = etree.QName(root).namespace
    if ns not in SHEET_NS:
        return data
    f, gone = "{%s}f" % ns, set()
    cells = [el for el in root.iter(f)]
    for el in cells:
        if _fetches(el.text or ""):
            if el.get("t") == "shared" and el.get("si") is not None:
                gone.add(el.get("si"))
            el.getparent().remove(el)
    for el in cells:  # cells that share a removed formula
        if el.getparent() is not None and el.get("t") == "shared" and el.get("si") in gone:
            el.getparent().remove(el)
    for el in root.iter("{%s}definedName" % ns):
        if _fetches(el.text or ""):
            el.text = "#N/A"
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def without_links(src, folder):
    """A copy of an Office file whose external pictures, objects and templates lead nowhere.

    Office fetches those on opening, which would tell a stranger's server (or send a Windows
    password hash) that the file was opened. Ordinary web hyperlinks are left alone.
    """
    out = os.path.join(folder, "source" + os.path.splitext(src)[1].lower())
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        if len({i.filename.lower() for i in zin.infolist()}) != len(zin.infolist()):
            raise EngineError("untrusted")  # two parts under one name: which would Office read?
        if sum(i.file_size for i in zin.infolist()) > MAX_UNPACKED:
            raise EngineError("too_big")
        for item in zin.infolist():
            data, name = zin.read(item), item.filename.lower()
            if name.endswith(".rels"):
                data = _clean_rels(data)
            elif name.endswith("connections.xml") or "pivotcachedefinition" in name:
                data = _no_refresh(data)
            elif name.endswith(".xml") and name.startswith("word/"):
                data = _settled(data)
            elif name.endswith(".xml") and name.startswith("xl/"):
                data = _no_fetch(data)
            zout.writestr(item.filename, data)
    try:  # keep the mark of a downloaded file: Office is stricter with those
        with open(src + ":Zone.Identifier", "rb") as f:
            mark = f.read()
        with open(out + ":Zone.Identifier", "wb") as f:
            f.write(mark)
    except OSError:
        pass
    return out


def _safe_source(src, folder, trusted=False):
    if os.path.splitext(src)[1].lower() in OOXML:
        if not zipfile.is_zipfile(src):  # not what its name says, and nobody was asked about it
            raise EngineError("untrusted")
        return without_links(src, folder)
    if not trusted:
        raise EngineError("untrusted")  # old formats cannot be cleaned: the user decides
    return src


class _Com:
    """An Office program of our own, announced to the window while it lives."""

    def __init__(self, name):
        self.name, self.helper = name, None

    def __enter__(self):
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            self.app = win32com.client.DispatchEx(self.name)
        except Exception:
            raise EngineError("no_office")
        return self

    def own(self, hwnd):
        """Remember the program behind one of its windows: that is certainly ours."""
        import psutil
        import win32process
        try:
            pid = win32process.GetWindowThreadProcessId(int(hwnd))[1]
            self.helper = (pid, psutil.Process(pid).create_time())
        except Exception:
            return
        _tell("started", self.helper)

    def __exit__(self, *exc):
        try:
            self.quit()
        except Exception:
            pass
        if self.helper:
            _tell("ended", self.helper)
        return False

    def quit(self):
        self.app.Quit()


def _lock_fields(doc):
    """Linked content must not be fetched while exporting: body, headers, notes, text boxes."""
    for story in doc.StoryRanges:
        while story is not None:
            story.Fields.Locked = True
            story = story.NextStoryRange


def _word(src, out, trusted=False):
    with files.work_dir() as tmp, _Com("Word.Application") as com:
        app = com.app
        app.Visible = False
        app.DisplayAlerts = 0
        app.AutomationSecurity = 3  # macros never run
        # Before any file is opened, so that a hang on opening can be ended too.
        blank = app.Documents.Add()
        com.own(app.ActiveWindow.Hwnd)
        blank.Close(False)
        doc = app.Documents.Open(_safe_source(src, tmp, trusted), ConfirmConversions=False,
                                 ReadOnly=True, AddToRecentFiles=False,
                                 PasswordDocument=NO_PASSWORD)
        try:
            _lock_fields(doc)
            doc.ExportAsFixedFormat(out, 17)
        finally:
            doc.Close(False)


def _excel(src, out, trusted=False):
    with files.work_dir() as tmp, _Com("Excel.Application") as com:
        app = com.app
        com.own(app.Hwnd)
        app.Visible = False
        app.DisplayAlerts = False
        app.AutomationSecurity = 3
        app.AskToUpdateLinks = False
        app.EnableEvents = False
        book = app.Workbooks.Open(_safe_source(src, tmp, trusted), UpdateLinks=0, ReadOnly=True,
                                  Password=NO_PASSWORD, AddToMru=False)
        try:
            book.ExportAsFixedFormat(0, out)
        finally:
            book.Close(False)


def _slides(src, out, trusted=False):
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:  # PowerPoint has one instance per user: it may be the user's own
        win32com.client.GetActiveObject("PowerPoint.Application")
        running = True
    except Exception:
        running = False
    com = _Com("PowerPoint.Application")
    if running:
        com.quit = lambda: None
    # Never announced as a helper: the user may open a presentation in it meanwhile,
    # and a forced end would take that with it.
    with files.work_dir() as tmp, com:
        app = com.app
        old = app.AutomationSecurity
        try:
            app.AutomationSecurity = 3
            show = app.Presentations.Open(_safe_source(src, tmp, trusted), ReadOnly=True,
                                          Untitled=False, WithWindow=False)
            try:
                show.SaveAs(out, 32)
            finally:
                show.Close()
        finally:
            app.AutomationSecurity = old
            if not running and app.Presentations.Count:
                com.quit = lambda: None  # the user opened something meanwhile


def _edge_path():
    import winreg
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            return winreg.QueryValue(
                root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe")
        except OSError:
            continue
    return None


CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src data:; "
       "base-uri 'none'; form-action 'none'")
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
DROP_TAGS = {"script", "iframe", "frame", "frameset", "object", "embed", "applet", "base", "portal",
             "audio", "video", "source", "track", "noscript", "template", "fencedframe"}
RAW_TAGS = {"xmp", "plaintext", "noembed", "noframes", "listing"}
KEEP_ATTRS = {"class", "id", "style", "width", "height", "align", "valign", "colspan", "rowspan",
              "border", "cellpadding", "cellspacing", "bgcolor", "color", "face", "size", "alt",
              "title", "lang", "dir", "start", "type", "value", "span", "nowrap", "name", "scope",
              "charset", "content", "reversed", "open", "hidden"}


def _inline(url, folder, types, limit=20 * 1024 * 1024):
    """Bytes of a file inside the page's own folder; None for anything else."""
    from urllib.parse import unquote, urlsplit
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.netloc or parts.scheme not in ("", "file"):
        return None
    rel = unquote(parts.path)
    if not rel or rel.startswith(("//", "\\\\")) or "\0" in rel:
        return None
    if parts.scheme == "file":
        rel = rel.lstrip("/")
    path = os.path.realpath(os.path.join(folder, rel.replace("/", os.sep)))
    home = os.path.realpath(folder)
    try:
        if os.path.commonpath([path, home]) != home:
            return None
        ext = os.path.splitext(path)[1].lower()
        if ext not in types or not os.path.isfile(path) or os.path.getsize(path) > limit:
            return None
        with open(path, "rb") as f:
            return ext, f.read()
    except (OSError, ValueError):
        return None


def harmless_html(data: bytes, folder: str) -> bytes:
    """The page rebuilt from what is safe: text, styles, and pictures from its own folder.

    Everything that could run, load or navigate is removed; the first line then forbids it
    once more to the browser.
    """
    import base64
    from lxml import html as lx
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        data = data.decode("utf-16", "replace").encode("utf-8")
        data = b"\xef\xbb\xbf" + data
    encoding = "utf-8" if data.startswith(b"\xef\xbb\xbf") else None
    if encoding is None:
        m = re.search(rb"charset\s*=\s*[\"']?\s*([\w\-]+)", data[:4096], re.I)
        encoding = m.group(1).decode("ascii") if m else "utf-8"
        try:
            text = data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            text = data.decode("utf-8", "replace")
    else:
        text = data.decode("utf-8-sig", "replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)
    try:
        root = lx.document_fromstring(text or "<p></p>")
    except Exception:
        raise EngineError("convert_failed")

    total = 0

    def css(t):
        # Browsers read styles inside svg and math as markup: there must be none in them.
        t = re.sub(r"<!--|-->", "", t or "")

        def char(m):  # an escape may spell the words that are looked for below
            n = int(m.group(1), 16) if m.group(1) else ord(m.group(2))
            return chr(n) if 32 <= n < 0x110000 and not 0xD800 <= n < 0xE000 and n != 92 else " "
        t = re.sub(r"\\(?:([0-9a-fA-F]{1,6})\s?|(.|\n))", char, t).replace("\\", " ")
        t = re.sub(r"(image-set|\bsrc)(\s*\()", r"x-\1\2", t, flags=re.I)
        t = t.replace("<", "\\3c ")
        t = re.sub(r"@import[^;]*;?", "", t, flags=re.I)
        return re.sub(r"url\s*\((?!\s*[\"']?data:image/)[^)]*\)", "none", t, flags=re.I)
    for el in list(root.iter()):
        if not isinstance(el.tag, str):  # comments, processing instructions
            if el.getparent() is not None:
                el.drop_tree()
            continue
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag in RAW_TAGS:  # their text would be written as it is
            el.tag = tag = "pre"
        if tag in DROP_TAGS or (tag == "meta" and el.get("http-equiv")):
            if el.getparent() is not None:
                el.drop_tree()
            continue
        if tag == "link":
            got = (_inline(el.get("href", ""), folder, {".css"}, 2 * 1024 * 1024)
                   if "stylesheet" in (el.get("rel") or "").lower() else None)
            if got:
                style = lx.Element("style")
                style.text = css(got[1].decode("utf-8", "replace"))
                el.addprevious(style)
            el.drop_tree()
            continue
        if tag == "style":
            el.text = css(el.text)
        for key in list(el.attrib):
            low = key.lower()
            if low == "style":
                el.set(key, css(el.get(key)))
            elif tag == "img" and low == "src":
                url = el.get(key).strip()
                if not re.match(r"data:image/(png|jpe?g|gif|webp|bmp);", url, re.I):
                    got = _inline(url, folder, IMAGE_TYPES)
                    total += len(got[1]) if got else 0
                    if got and total <= MAX_INLINE:
                        el.set(key, f"data:{IMAGE_TYPES[got[0]]};base64,"
                               + base64.b64encode(got[1]).decode())
                    else:
                        del el.attrib[key]
            elif tag == "a" and low == "href":
                if not re.match(r"\s*(https?:|mailto:|#)", el.get(key), re.I):
                    del el.attrib[key]
            elif low not in KEEP_ATTRS and not low.startswith(("data-", "aria-")):
                # SVG drawing attributes are harmless, references and events are not
                if low.startswith("on") or "href" in low or "src" in low or low in (
                        "action", "formaction", "background", "poster", "data", "manifest", "ping",
                        "codebase", "archive", "longdesc", "cite", "profile", "usemap", "srcdoc"):
                    del el.attrib[key]
                elif "url" in el.get(key).lower() and "#" not in el.get(key):
                    del el.attrib[key]
    body = lx.tostring(root, encoding="unicode")
    return (f'<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" '
            f'content="{CSP}">' + body).encode("utf-8")


def _web(src, out):
    edge = _edge_path()
    if not edge or not os.path.exists(edge):
        raise EngineError("no_edge")
    import psutil
    from urllib.request import pathname2url
    with files.work_dir() as tmp:
        with open(src, "rb") as f:
            data = f.read()
        page = os.path.join(tmp, "page.html")
        with open(page, "wb") as f:
            f.write(harmless_html(data, os.path.dirname(src)))
        try:
            proc = subprocess.Popen(
                [edge, "--headless=new", "--disable-gpu", "--no-first-run", "--disable-extensions",
                 "--host-resolver-rules=MAP * ~NOTFOUND",
                 "--proxy-server=127.0.0.1:9", f"--user-data-dir={os.path.join(tmp, 'profile')}",
                 "--no-pdf-header-footer", f"--print-to-pdf={out}", "file:" + pathname2url(page)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            raise EngineError("no_edge")
        try:
            helper = (proc.pid, psutil.Process(proc.pid).create_time())
        except psutil.Error:
            helper = None
        if helper:
            _tell("started", helper)
        try:
            proc.wait(120)
            for _ in range(50):
                if os.path.exists(out) and os.path.getsize(out):
                    return
                time.sleep(.2)
            raise EngineError("convert_failed")
        except subprocess.TimeoutExpired:
            raise EngineError("convert_failed")
        finally:
            try:
                p = psutil.Process(proc.pid)
                for q in [*p.children(recursive=True), p]:
                    q.kill()
            except psutil.Error:
                pass
            if helper:
                _tell("ended", helper)


def _text(src, out):
    with open(src, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8",
                "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode("utf-8", "replace")
    body = "".join(f"<p>{html.escape(ln) or '&#160;'}</p>" for ln in text.splitlines())
    story = pymupdf.Story(body or "<p>&#160;</p>",
                          user_css="p{font-family:sans-serif;font-size:11px;margin:0 0 3px 0;}")
    writer = pymupdf.DocumentWriter(out)
    page = pymupdf.paper_rect("a4")
    more = 1
    while more:
        dev = writer.begin_page(page)
        more, _ = story.place(page + (56, 56, -56, -56))
        story.draw(dev)
        writer.end_page()
    writer.close()


def _image(src, out):
    from PIL import Image, ImageOps
    from .scan import _open_image
    doc = pymupdf.open()
    with _open_image(src) as im:
        for k in range(getattr(im, "n_frames", 1)):
            im.seek(k)
            frame = ImageOps.exif_transpose(im)
            dpi = frame.info.get("dpi", (96, 96))[0] or 96
            dpi = float(dpi) if 50 <= float(dpi) <= 1200 else 96.0
            w, h = frame.width * 72 / dpi, frame.height * 72 / dpi
            k2 = min(1.0, 1400 / max(w, h))
            page = doc.new_page(width=max(w * k2, 20), height=max(h * k2, 20))
            buf = io.BytesIO()
            if frame.mode in ("RGBA", "LA", "P"):
                frame = frame.convert("RGBA")
                flat = Image.new("RGB", frame.size, "white")
                flat.paste(frame, mask=frame.split()[-1])
                frame = flat
            if im.format == "JPEG" and frame.mode in ("RGB", "L"):
                frame.save(buf, "JPEG", quality=95)
            else:
                (frame if frame.mode in ("1", "L", "RGB") else frame.convert("RGB")).save(buf, "PNG")
            page.insert_image(page.rect, stream=buf.getvalue())
    doc.save(out, garbage=3, deflate=True)
    doc.close()


def _one(src, out, trusted=False):
    ext = os.path.splitext(src)[1].lower()
    try:
        if ext in IMAGES:
            _image(src, out)
        elif ext in WORD:
            _word(src, out, trusted)
        elif ext in EXCEL:
            _excel(src, out, trusted)
        elif ext in SLIDES:
            _slides(src, out, trusted)
        elif ext in WEB:
            _web(src, out)
        elif ext in TEXT:
            _text(src, out)
        elif ext in OTHER:
            with pymupdf.open(src) as d:
                data = d.convert_to_pdf()
            with open(out, "wb") as f:
                f.write(data)
        else:
            raise EngineError("bad_type")
    except EngineError:
        raise
    except Exception:
        raise EngineError("convert_failed")


def make_pdf(paths, out, trusted=False):
    """Convert files to one PDF. Error codes end with the number of the bad file."""
    merged = pymupdf.open()
    with files.work_dir() as tmp:
        for i, src in enumerate(paths):
            src = os.path.abspath(src)
            try:
                if src.lower().endswith(".pdf"):
                    part = pymupdf.open(src)
                    if part.needs_pass:
                        raise EngineError("needs_password")
                else:
                    piece = os.path.join(tmp, f"{i}.pdf")
                    _one(src, piece, trusted)
                    part = pymupdf.open(piece)
                merged.insert_pdf(part)
                part.close()
            except EngineError as e:
                raise EngineError(f"{e}#{i}")
            except Exception:
                raise EngineError(f"convert_failed#{i}")
    if not merged.page_count:
        raise EngineError("no_pages")
    _write(merged, out)
    n = merged.page_count
    merged.close()
    return {"pages": n}


# ---------------------------------------------------------------------------
# from PDF. `eng` is the engine with the document, edits included.
# ---------------------------------------------------------------------------
def _plain(eng, folder):
    path = os.path.join(folder, "in.pdf")
    copy = eng._snapshot()
    copy.save(path, garbage=1, encryption=pymupdf.PDF_ENCRYPT_NONE)
    copy.close()
    return path


def _docx_child(src, out, pages):
    """Runs alone in a program of its own: pdf2docx can take forever on some pages."""
    import logging
    import warnings
    warnings.simplefilter("ignore")
    logging.disable(logging.CRITICAL)
    from pdf2docx import Converter
    cv = Converter(src)
    try:
        cv.convert(out, pages=list(pages))
    finally:
        cv.close()


def _run_child(name, args, seconds) -> bool:
    """Run one function of this module in a program of its own, for at most so long."""
    import sys
    import psutil
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (f"import sys; sys.path.insert(0, {root!r}); from {__package__}.convert import {name}; "
            f"{name}(*{args!r})")
    proc = subprocess.Popen([sys.executable, "-X", "utf8", "-c", code], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        helper = (proc.pid, psutil.Process(proc.pid).create_time())
        _tell("started", helper)
    except psutil.Error:
        helper = None
    try:
        proc.wait(min(seconds, 600))
    except subprocess.TimeoutExpired:
        pass
    finally:
        if proc.poll() is None:
            try:
                for q in psutil.Process(proc.pid).children(recursive=True):
                    q.kill()
            except psutil.Error:
                pass
            proc.kill()
            proc.wait(10)
        if helper:
            _tell("ended", helper)
    return proc.returncode == 0


def to_docx(eng, out, pages):
    """Word file. Returns "simple" when only text could be carried over."""
    with files.work_dir() as tmp:
        src, made = _plain(eng, tmp), os.path.join(tmp, "out.docx")
        if _run_child("_docx_child", (src, made, list(pages)),
                      DOCX_BASE + DOCX_PER_PAGE * len(pages)) and os.path.exists(made):
            with open(made, "rb") as f, open(out, "wb") as g:
                g.write(f.read())
            return ""
    _simple_docx(eng, out, pages)
    return "simple"


DOCX_BASE, DOCX_PER_PAGE = 45, 12  # seconds
XLSX_BASE, XLSX_PER_PAGE = 40, 10


def _simple_docx(eng, out, pages):
    import docx
    from docx.shared import Pt
    doc = docx.Document()
    for n, p in enumerate(pages):
        if n:
            doc.add_page_break()
        page = eng.doc.load_page(p)
        lines = eng.chars(p)
        if not lines:  # a picture of a page is better than an empty one
            pix = page.get_pixmap(dpi=110)
            sec = doc.sections[0]
            doc.add_picture(io.BytesIO(pix.tobytes("png")),
                            width=sec.page_width - sec.left_margin - sec.right_margin)
            continue
        for ln in sorted(lines, key=lambda ln: (round(ln[0][1] / 3), ln[0][0])):
            text = re.sub("[\\x00-\\x08\\x0b-\\x1f]", "", "".join(c[4] for c in ln)).rstrip()
            if text.strip():
                par = doc.add_paragraph()
                run = par.add_run(text)
                run.font.size = Pt(min(max(round((ln[0][3] - ln[0][1]) * .78), 6), 36))
                par.paragraph_format.space_after = Pt(1)
    doc.save(out)


def _cells(eng, pno, table):
    """Cell texts in true reading order (the library's own extraction turns -5 into "5 -")."""
    lines = eng.chars(pno)
    out = []
    for row in table.rows:
        got = []
        for box in row.cells:
            if box is None:
                got.append("")
                continue
            x0, y0, x1, y1 = box
            parts = []
            for ln in lines:
                inside = [c for c in ln if x0 <= (c[0] + c[2]) / 2 <= x1
                          and y0 <= (c[1] + c[3]) / 2 <= y1]
                if inside:
                    parts.append(((inside[0][1] + inside[0][3]) / 2, inside[0][0],
                                  "".join(c[4] for c in inside)))
            # same text line first by height (rounded), then left to right
            parts.sort(key=lambda t: (round(t[0] / 4), t[1]))
            got.append(" ".join(t[2].strip() for t in parts))
        out.append(got)
    # Double border lines look like columns: drop the hair-thin ones that are always empty.
    width = max(len(r) for r in out)
    thin = []
    for c in range(width):
        boxes = [row.cells[c] for row in table.rows if c < len(row.cells) and row.cells[c]]
        if boxes and max(b[2] - b[0] for b in boxes) < 5 and not any(
                r[c] for r in out if c < len(r)):
            thin.append(c)
    return [[v for c, v in enumerate(r) if c not in thin] for r in out]


def _sheet_filler():
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

    def fill(sheet, rows):
        for r, row in enumerate(rows, 1):
            for c, v in enumerate(row, 1):
                v = ILLEGAL_CHARACTERS_RE.sub("", " ".join(str(v or "").split()))
                cell = sheet.cell(r, c, v)
                if v[:1] in tuple("=+-@"):  # text, even when somebody edits the cell later
                    cell.data_type = "s"
                    cell.quotePrefix = True
    return fill


def _xlsx_child(src, out, pages):
    """Runs alone: finding tables can take forever on drawings with very many lines."""
    from openpyxl import Workbook
    from .features import Features
    eng = Features()
    eng.open(src)
    book, fill, tables = Workbook(), _sheet_filler(), 0
    book.remove(book.active)
    for p in pages:
        page = eng.doc.load_page(p)  # table boxes come in displayed space, like chars()
        for k, t in enumerate(page.find_tables().tables):
            fill(book.create_sheet(f"გვ{p + 1}-{k + 1}"), _cells(eng, p, t))
            tables += 1
    if tables:
        book.save(out)


def _text_rows(eng, pno):
    """Text of a page as rows; a wide gap starts a new column."""
    rows: dict = {}
    for ln in eng.chars(pno):
        rows.setdefault(round((ln[0][1] + ln[0][3]) / 2 / 5), []).append(ln)
    out = []
    for key in sorted(rows):
        cells, end = [], None
        for x0, y0, x1, y1, c in sorted((c for ln in rows[key] for c in ln), key=lambda c: c[0]):
            if end is None or x0 - end > max(y1 - y0, 4) * 1.5:
                cells.append("")
            cells[-1] += c
            end = x1
        out.append(cells)
    return out


def to_xlsx(eng, out, pages):
    """Excel file: number of sheets with tables, or "simple" when there is only text."""
    from openpyxl import Workbook, load_workbook
    with files.work_dir() as tmp:
        src, made = _plain(eng, tmp), os.path.join(tmp, "out.xlsx")
        done = _run_child("_xlsx_child", (src, made, list(pages)),
                          XLSX_BASE + XLSX_PER_PAGE * len(pages))
        if done and os.path.exists(made):
            with open(made, "rb") as f:
                data = f.read()
            with open(out, "wb") as g:
                g.write(data)
            # from memory: an open workbook would keep the temporary copy on the disk
            return len(load_workbook(io.BytesIO(data)).sheetnames)
    book, fill = Workbook(), _sheet_filler()
    sheet = book.active
    sheet.title = "ტექსტი"
    fill(sheet, [row for p in pages for row in _text_rows(eng, p)])
    book.save(out)
    return "simple"


def to_text(eng, out, pages):
    parts = []
    for p in pages:
        parts.append("\n".join("".join(c[4] for c in ln) for ln in eng.chars(p)))
    with open(out, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n\n\f".join(parts))


def to_html(eng, out, pages):
    def page(p):
        own = eng.doc.load_page(p).get_text("xhtml")
        def plain(text):
            text = unicodedata.normalize("NFKC", text.replace("\u00ad", "-"))
            return sorted("".join(text.split()))
        if plain(html.unescape(re.sub("<[^>]*>", "", own))) == plain(
                "".join(c[4] for ln in eng.chars(p) for c in ln)):
            return own
        # Old Georgian fonts: chars() reads them, the library's own HTML does not.
        rows = re.findall(r"<img[^>]*>", own)
        for ln in eng.chars(p):
            text = html.escape("".join(c[4] for c in ln).rstrip())
            size = min(max(round(ln[0][3] - ln[0][1]) * .78, 6), 48)
            rows.append(f'<p style="font-size:{size:.0f}pt">{text}</p>')
        return "\n".join(rows)
    body = "\n<hr>\n".join(page(p) for p in pages)
    title = html.escape((eng.doc.metadata or {}).get("title") or "")
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>{title}</title></head>"
                f"<body>{body}</body></html>")


def to_pptx(eng, out, pages):
    from pptx import Presentation
    from pptx.util import Emu
    show = Presentation()
    first = eng.doc.load_page(pages[0]).rect
    k = min(56 * 72 / max(first.width, first.height), 1.0)  # slides are 1 to 56 inches
    k = max(k, 72 / min(first.width, first.height)) if min(first.width, first.height) * k < 72 else k
    show.slide_width = Emu(min(int(first.width * k * 12700), 51206400))
    show.slide_height = Emu(min(int(first.height * k * 12700), 51206400))
    for p in pages:
        page = eng.doc.load_page(p)
        limit = 72 * (20_000_000 / (page.rect.width * page.rect.height)) ** .5
        pix = page.get_pixmap(dpi=int(min(150, limit)))
        slide = show.slides.add_slide(show.slide_layouts[6])
        k = min(show.slide_width / page.rect.width, show.slide_height / page.rect.height)
        w, h = int(page.rect.width * k), int(page.rect.height * k)
        slide.shapes.add_picture(io.BytesIO(pix.tobytes("png")), (show.slide_width - w) // 2,
                                 (show.slide_height - h) // 2, w, h)
    show.save(out)


def to_images(eng, targets, dpi):
    """targets = [(page, path), ...]; the extension picks the format. All or nothing."""
    made = []
    try:
        for p, path in targets:
            page = eng.doc.load_page(p)
            limit = 72 * (60_000_000 / (page.rect.width * page.rect.height)) ** .5
            pix = page.get_pixmap(dpi=max(int(min(dpi, limit)), 8))
            stem, ext = os.path.splitext(path)
            tmp = files.unique_path(stem + ".furceli" + ext)
            made.append((tmp, path))
            if ext.lower() in (".jpg", ".jpeg"):
                pix.save(tmp, jpg_quality=92)
            else:
                pix.save(tmp)
        done = []
        for tmp, path in made:
            os.rename(tmp, path)  # the names were free: never over somebody's file
            done.append(path)
    except Exception:
        for path in [t for t, _ in made] + (done if "done" in locals() else []):
            if os.path.exists(path):
                os.remove(path)
        raise EngineError("save_failed")
    return len(made)


def export_as(eng, kind, out, pages):
    fn = {"docx": to_docx, "xlsx": to_xlsx, "txt": to_text, "html": to_html,
          "pptx": to_pptx}[kind]
    return fn(eng, out, list(pages))
