"""Checks a packed ETRATI from the inside: build it with the same recipe and this as the start
script (see ETRATI-selftest.spec), then

    ETRATI-selftest.exe <folder with sample files> <empty work folder>

Only copies in the work folder are used; the samples are never changed. Results go to
<work>/results.txt as one line per check, without any text of the documents.
"""
import json
import multiprocessing
import os
import shutil
import sys
import time
import traceback

if not getattr(sys, "frozen", False):  # from the sources: the program is next to scripts/
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def georgian(text):
    return sum("ა" <= c <= "ჿ" for c in text)


def pick(folder, work, log):
    import pymupdf
    names = sorted(os.listdir(folder))
    pdf = None
    for n in names:
        if n.lower().endswith(".pdf") and os.path.getsize(os.path.join(folder, n)) < 20_000_000:
            try:
                d = pymupdf.open(os.path.join(folder, n))
                if not d.needs_pass and d.page_count and georgian(d[0].get_text()) > 200:
                    pdf = os.path.join(work, "sample.pdf")
                    shutil.copyfile(os.path.join(folder, n), pdf)
                    break
            except Exception:
                continue
    def first(ext):
        for n in names:
            if n.lower().endswith(ext) and not n.startswith("~$"):
                out = os.path.join(work, "sample" + ext)
                shutil.copyfile(os.path.join(folder, n), out)
                return out
    photo, most = None, 0  # phone photos of documents: the folder that has the most of them
    for place in [os.path.join(folder, n) for n in names if os.path.isdir(os.path.join(folder, n))]:
        jpgs = sorted(n for n in os.listdir(place) if n.lower().endswith((".jpg", ".jpeg")))
        if len(jpgs) > most:
            photo, most = os.path.join(place, jpgs[0]), len(jpgs)
    log("samples", bool(pdf and photo), f"pdf={bool(pdf)} docx={bool(first('.docx'))} "
        f"xlsx={bool(first('.xlsx'))} photo={bool(photo)}")
    return pdf, os.path.join(work, "sample.docx"), os.path.join(work, "sample.xlsx"), photo


def main():
    if len(sys.argv) > 3 and sys.argv[1] == "--run-child":  # as ETRATI.exe does for converters
        from furceli.__main__ import run_child
        return run_child(sys.argv[2], sys.argv[3])
    folder, work = sys.argv[1], sys.argv[2]
    os.makedirs(work, exist_ok=True)
    out = open(os.path.join(work, "results.txt"), "w", encoding="utf-8")

    def log(name, ok, detail=""):
        out.write(json.dumps({"check": name, "ok": bool(ok), "detail": str(detail)},
                             ensure_ascii=False) + "\n")
        out.flush()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["FURCELI_SETTINGS"] = os.path.join(work, "settings.ini")
    from PySide6.QtWidgets import QApplication
    app = QApplication([])
    import pymupdf
    from furceli import files, theme
    from furceli.client import EngineClient, TaskError, run_task
    from furceli.helper import convert_first
    log("frozen", getattr(sys, "frozen", False), sys.executable)
    pdf, docx, xlsx, photo = pick(folder, work, log)

    def check(name, fn):
        t = time.time()
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e} | {traceback.format_exc(limit=3)[-300:]}"
        log(name, ok, f"{detail} ({time.time() - t:.1f} s)")

    # -- theme, fonts, icons ---------------------------------------------------------------
    def themes():
        from PySide6.QtGui import QFontDatabase
        theme.apply_theme(app, "dark")
        dark = theme.color("surface-100").name()
        theme.apply_theme(app, "light")
        icon = not theme.icon("save").pixmap(20, 20).isNull()
        fonts = "Noto Sans Georgian" in QFontDatabase.families()
        return dark == "#1c1a18" and icon and fonts, f"dark={dark} icon={icon} fonts={fonts}"
    check("theme switch, icons, fonts", themes)

    # -- password, edit, save ----------------------------------------------------------------
    locked = os.path.join(work, "locked.pdf")
    d = pymupdf.open(pdf)
    d.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="test123", owner_pw="own456")
    d.close()
    client = EngineClient()

    def password():
        try:
            run_task(client, "open", (locked, "wrong"))
            return False, "wrong password accepted"
        except TaskError as e:
            wrong = str(e)
        info = run_task(client, "open", (locked, "test123"))
        return wrong == "wrong_password" and len(info["sizes"]) > 0, f"wrong={wrong} pages={len(info['sizes'])}"
    check("password PDF", password)

    saved = files.outbox(os.path.join(work, "saved.pdf"))  # the engine writes only there

    def walled():
        try:
            run_task(client, "save", (os.path.join(work, "outside.pdf"),))
        except TaskError as e:
            return str(e) == "outside" and not os.path.exists(os.path.join(work, "outside.pdf")), str(e)
        return False, "the engine wrote outside its folder"
    check("engine writes only into its folder", walled)

    def few_rights():
        from furceli import sandbox
        level = sandbox.integrity(client._proc.pid)
        return level == 0x1000 and not sandbox.failed, f"integrity={level:#x} failed={sandbox.failed!r}"
    check("engine runs at low integrity", few_rights)

    def save():
        run_task(client, "apply", (("rotate", [0], 90),))
        run_task(client, "save", (saved,))
        r = pymupdf.open(saved, filetype="pdf")
        ok = r.needs_pass and r.authenticate("test123") and r[0].rotation == 90
        return ok, f"rotation={r[0].rotation}"
    check("edit and save", save)
    client.shutdown()

    # -- OCR (Georgian) ------------------------------------------------------------------------
    def ocr():
        src = pymupdf.open(pdf)
        pic = pymupdf.open()
        page = pic.new_page(width=src[0].rect.width, height=src[0].rect.height)
        page.insert_image(page.rect, pixmap=src[0].get_pixmap(dpi=200))
        scan = os.path.join(work, "picture.pdf")
        pic.save(scan)
        c = EngineClient()
        try:
            run_task(c, "open", (scan, ""))
            words = run_task(c, "ocr_words", (0, "kat+eng", False))
        finally:
            c.shutdown()
        geo = sum(georgian(w[4]) > 2 for w in words)
        return geo >= 20, f"words={len(words)} georgian_words={geo}"
    check("OCR (Georgian)", ocr)

    # -- scan clean-up from a HEIC photo ---------------------------------------------------------
    def heic():
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        h = os.path.join(work, "photo.heic")
        with Image.open(photo) as im:
            im.save(h)
        c = EngineClient()
        try:
            n = run_task(c, "scan_count", (h, ""))["pages"]
            o = run_task(c, "scan_analyse", (h, 0, "", "kat+eng"))
            part = files.outbox(os.path.join(work, "part.pdf"))
            words = run_task(c, "scan_build", (h, 0, dict(o, mode="gray", languages="kat+eng"), part))
            clean = files.outbox(os.path.join(work, "clean.pdf"))
            run_task(c, "scan_join", ([part], clean))
        finally:
            c.shutdown()
        text = pymupdf.open(clean)[0].get_text()
        return n == 1 and words > 10 and georgian(text) > 30, f"words={words} turn={o['turn']}"
    check("scan clean-up (HEIC)", heic)

    # -- Office to PDF, PDF to Word -------------------------------------------------------------
    for name, src in (("Word to PDF", docx), ("Excel to PDF", xlsx)):
        def office(src=src):
            if not os.path.exists(src):
                return False, "no sample"
            c = EngineClient()
            try:
                ready = convert_first(c, [src], False, "", None)  # Office runs in the helper
                r = run_task(c, "make_pdf", (ready, files.outbox(src + ".pdf")))
            finally:
                c.shutdown()
            return r["pages"] > 0, f"pages={r['pages']}"
        check(name, office)

    def to_word():
        c = EngineClient()
        target = files.outbox(os.path.join(work, "out.docx"))
        try:
            run_task(c, "open", (pdf, ""))
            note = run_task(c, "export_as", ("docx", target, [0]))
        finally:
            c.shutdown()
        import docx as d
        text = " ".join(p.text for p in d.Document(target).paragraphs)
        return georgian(text) > 50 and note != "simple", f"note={note!r} georgian={georgian(text)}"
    check("PDF to Word", to_word)

    # -- printing (Qt's PDF output instead of paper) ------------------------------------------------
    def printing():
        from PySide6.QtPrintSupport import QPrinter
        from furceli import printing as pr
        from furceli.window import MainWindow
        win = MainWindow()
        win.open_file(pdf)
        end = time.time() + 30
        while time.time() < end and not (win.doc and win.doc.loaded):
            app.processEvents()
            time.sleep(.05)
        p = QPrinter(QPrinter.PrinterMode.HighResolution)
        p.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        target = os.path.join(work, "printed.pdf")
        p.setOutputFileName(target)
        done = pr.print_pages(win.doc, p, [0], win)
        win.close_tab(0, ask=False)
        win.close()
        n = pymupdf.open(target).page_count if os.path.exists(target) else 0
        return done and n == 1, f"pages={n}"
    check("printing", printing)

    # -- certificate signature ----------------------------------------------------------------
    def sign():
        c = EngineClient()
        pfx = files.outbox(os.path.join(work, "test.pfx"))
        signed = files.outbox(os.path.join(work, "signed.pdf"))
        plain = os.path.join(work, "plain.pdf")
        shutil.copyfile(pdf, plain)
        try:
            run_task(c, "make_certificate", (pfx, "pw", "Test"))
            run_task(c, "sign", (plain, signed, "", pfx, "pw", {}))
            res = run_task(c, "verify", (signed, ""))
        finally:
            c.shutdown()
        return bool(res), f"signatures={len(res)} first={str(res[0])[:120] if res else ''}"
    check("certificate signature", sign)

    # -- AI panel (no request is sent) ---------------------------------------------------------------
    def ai_panel():
        import anthropic
        from furceli import ai
        anthropic.Anthropic(api_key="sk-test")  # the library loads; nothing is sent
        return hasattr(ai, "AiPanel") and len(ai.MODELS) > 0, f"anthropic={anthropic.__version__}"
    check("AI panel loads", ai_panel)
    out.close()
    return 0


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
