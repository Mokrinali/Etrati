"""Main window: tabs, menus, toolbar, recent files."""
from __future__ import annotations

import math
import os
import time

from PySide6.QtCore import QSettings, QSize, Qt, QUrl
from PySide6.QtGui import (QAction, QActionGroup, QColor, QDesktopServices, QFont, QIcon, QKeySequence,
                           QPainter, QPalette, QPixmap)
from PySide6.QtWidgets import (QColorDialog, QComboBox, QFileDialog, QFormLayout, QInputDialog, QDialog, QDialogButtonBox,
                               QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
                               QPushButton, QSpinBox, QStackedWidget, QTabWidget, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from . import APP_NAME, DATA_ID, __version__, files, theme
from .ai import AiPanel
from .batch import BatchDialog
from .client import EngineClient, TaskError, finish_all, run_task
from .convert_names import EXCEL, OOXML, SLIDES, SUPPORTED, WEB, WORD
from .dialogs import MergeDialog, error_text
from .scan_dialog import ScanDialog
from .welcome import DropFrame, WelcomeScreen
from .docview import DocView
from .printing import PrintError, choose_and_print

MAX_RECENT = 12
ZOOMS = [25, 50, 75, 100, 125, 150, 200, 300, 400]


def make_settings() -> QSettings:
    path = os.environ.get("FURCELI_SETTINGS")
    if path:
        return QSettings(path, QSettings.Format.IniFormat)
    return QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, DATA_ID, DATA_ID)


def glyph(code: int, color: QColor) -> QIcon:
    """Icon from the Windows symbol font (Windows 11, then Windows 10 name)."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    f = QFont()
    f.setFamilies(["Segoe Fluent Icons", "Segoe MDL2 Assets"])
    f.setPixelSize(44)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setFont(f)
    p.setPen(color)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, chr(code))
    p.end()
    return QIcon(pix)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = make_settings()
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 860)
        self.setAcceptDrops(True)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._sync)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._welcome())
        self.stack.addWidget(self.tabs)
        self.setCentralWidget(self.stack)
        self.drop_frame = DropFrame(self)
        self._actions()
        self._toolbar()
        self._menus()
        self.link_label = QLabel("")
        self.link_label.setTextFormat(Qt.TextFormat.PlainText)  # addresses come from documents
        self.statusBar().addPermanentWidget(self.link_label)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        self._refresh_recent()
        self._sync()

    # -- building ----------------------------------------------------------
    def _act(self, text, slot, key=None, icon=None, checkable=False, needs_doc=True):
        a = QAction(text, self)
        if checkable:
            a.setCheckable(True)
            a.toggled.connect(slot)
        else:
            a.triggered.connect(lambda *_: slot())
        if key:
            keys = key if isinstance(key, list) else [key]
            a.setShortcuts([QKeySequence(k) for k in keys])
            a.setToolTip(f"{text} ({QKeySequence(keys[0]).toString()})")
        if icon:
            a.setIcon(glyph(icon, self.palette().color(QPalette.ColorRole.WindowText)))
            self._glyphs.append((a, icon))
        if needs_doc:
            self._doc_actions.append(a)
        self.addAction(a)
        return a

    def _edit_action(self, text, fn, key=None, icon=None):
        a = self._act(text, lambda: self.doc and fn(self.doc), key, icon)
        self._edit_actions.append(a)
        return a

    def _actions(self):
        self._glyphs: list[tuple[QAction, int]] = []
        self._edit_actions: list[QAction] = []
        self._doc_actions: list[QAction] = []
        d = self._with_view
        self.a_open = self._act("გახსნა…", self.choose_files, "Ctrl+O", 0xE8E5, needs_doc=False)
        self.a_print = self._act("ბეჭდვა…", self.print_doc, "Ctrl+P", 0xE749)
        self.a_close = self._act("ჩანართის დახურვა", lambda: self.close_tab(
            self.tabs.currentIndex()), "Ctrl+W")
        self.a_quit = self._act("გასვლა", self.close, "Ctrl+Q", needs_doc=False)
        self.a_props = self._act("ფაილის თვისებები", self.properties, "Ctrl+D", 0xE946)
        self.a_copy = self._act("კოპირება", lambda: self.doc.copy(), "Ctrl+C")
        self.a_find = self._act("ძიება", lambda: self.doc.open_search(), "Ctrl+F", 0xE721)
        self.a_next = self._act("შემდეგი შედეგი", lambda: self.doc.next_hit(1), "F3")
        self.a_prev = self._act("წინა შედეგი", lambda: self.doc.next_hit(-1), "Shift+F3")
        self.a_side = self._act("გვერდითი პანელი", self._toggle_side, "F4", 0xE8A0)
        self.a_in = self._act("გადიდება", d(lambda v: v.set_zoom(v.zoom * 1.2)),
                              ["Ctrl++", "Ctrl+="], 0xE8A3)
        self.a_out = self._act("დაპატარავება", d(lambda v: v.set_zoom(v.zoom / 1.2)),
                               "Ctrl+-", 0xE71F)
        self.a_100 = self._act("რეალური ზომა (100%)", d(lambda v: v.set_zoom(1.0)), "Ctrl+1")
        self.a_width = self._act("სიგანეზე მორგება", d(lambda v: v.set_fit("width")), "Ctrl+2")
        self.a_page = self._act("მთელი გვერდი", d(lambda v: v.set_fit("page")), "Ctrl+0")
        self.a_rot = self._act("ხედის მობრუნება", d(lambda v: v.rotate()), "Ctrl+R", 0xE7AD)
        self.a_two = self._act("ორი გვერდი გვერდიგვერდ", self._two_up, "Ctrl+Shift+2", 0xE89A,
                               checkable=True)
        self.a_full = self._act("სრული ეკრანი", self._full, "F11", 0xE740, needs_doc=False)
        self.a_pgup = self._act("წინა გვერდი", d(lambda v: v.step(-1)), "Ctrl+PgUp", 0xE70E)
        self.a_pgdn = self._act("შემდეგი გვერდი", d(lambda v: v.step(1)), "Ctrl+PgDown", 0xE70D)
        self.a_goto = self._act("გვერდზე გადასვლა", self._focus_page, "Ctrl+G")
        self.a_about = self._act("პროგრამის შესახებ", self.about, needs_doc=False)
        e = self._edit_action
        self.a_save = self._act("შენახვა", lambda: self.doc.save(), "Ctrl+S", 0xE74E)
        self.a_save_as = self._act("შენახვა როგორც…", lambda: self.doc.save_as(), "Ctrl+Shift+S")
        self.a_merge = self._act("ფაილების გაერთიანება…", self.merge, "Ctrl+M", needs_doc=False)
        self.a_split = self._act("ფაილის დაყოფა…", lambda: self.doc.split())
        self.a_backups = self._act("სარეზერვო ასლების საქაღალდე", lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(files.backup_dir())), needs_doc=False)
        self.a_undo = self._act("გაუქმება", lambda: self.doc.undo(), "Ctrl+Z", 0xE7A7)
        self.a_redo = self._act("გამეორება", lambda: self.doc.redo(), ["Ctrl+Y", "Ctrl+Shift+Z"],
                                0xE7A6)
        self.a_org = self._act("გვერდების მართვა", self._organize, "Ctrl+E", 0xE80A, checkable=True)
        self.a_rr = e("მობრუნება მარჯვნივ", lambda d: d.rotate_pages(90), "Ctrl+Shift+R", 0xE7AD)
        self.a_rl = e("მობრუნება მარცხნივ", lambda d: d.rotate_pages(-90), "Ctrl+Shift+L", 0xE7A7)
        self.a_del = e("წაშლა", lambda d: d.delete_pages(), None, 0xE74D)
        self.a_dup = e("დუბლირება", lambda d: d.duplicate_pages(), None, 0xE8C8)
        self.a_ins = e("ჩასმა ფაილიდან…", lambda d: d.insert_file(), None, 0xE710)
        self.a_blank = e("ცარიელი გვერდის ჩასმა", lambda d: d.insert_blank(), None, 0xE7C3)
        self.a_up = e("წინ გადატანა", lambda d: d.shift_pages(-1), "Ctrl+Shift+Up")
        self.a_down = e("უკან გადატანა", lambda d: d.shift_pages(1), "Ctrl+Shift+Down")
        self.a_crop = e("ჩამოჭრა…", lambda d: d.start_crop(), None, 0xE7A8)
        self.a_uncrop = e("ჩამოჭრის მოხსნა", lambda d: d.uncrop())
        self.a_ext = self._act("ამოღება ახალ ფაილად…", self.extract, icon=0xE8A7)
        self.page_actions = [self.a_rl, self.a_rr, self.a_del, self.a_dup, None, self.a_ins,
                             self.a_blank, self.a_ext, None, self.a_crop, self.a_uncrop, None,
                             self.a_up, self.a_down]
        self._tool_actions()
        # With a Georgian keyboard layout active Ctrl+letter may not match above.
        self._by_vk = {ord("O"): self.a_open, ord("P"): self.a_print, ord("W"): self.a_close,
                       ord("Q"): self.a_quit, ord("D"): self.a_props, ord("C"): self.a_copy,
                       ord("F"): self.a_find, ord("R"): self.a_rot, ord("G"): self.a_goto,
                       ord("S"): self.a_save, ord("Z"): self.a_undo, ord("Y"): self.a_redo,
                       ord("E"): self.a_org, ord("M"): self.a_merge}
        self._by_vk_shift = {ord("S"): self.a_save_as, ord("Z"): self.a_redo,
                             ord("R"): self.a_rr, ord("L"): self.a_rl}

    TOOLS = [("select", "მონიშვნა", 0xE8B0), ("highlight", "მარკერი", 0xE7E6),
             ("underline", "ხაზგასმა", 0xE8DC), ("strikeout", "გადახაზვა", 0xEDE0),
             ("note", "შენიშვნა", 0xE90A), ("text", "ტექსტი", 0xE8D2), ("ink", "ხატვა", 0xED63),
             ("rect", "ოთხკუთხედი", 0xE7FB), ("ellipse", "ელიფსი", 0xEA3A),
             ("line", "ხაზი", 0xE738), ("arrow", "ისარი", 0xE72A), ("stamp", "შტამპი", 0xE8FB),
             ("image", "სურათი", 0xEB9F), ("edit", "ტექსტის შეცვლა", 0xE70F),
             ("redact", "დასაფარის მონიშვნა", 0xE75C), ("field", "ფორმის ველი", 0xE9D5)]

    def _tool_actions(self):
        self.tool_group = QActionGroup(self)
        self.tool_acts = {}
        for name, text, icon in self.TOOLS:
            a = self._act(text, lambda on, n=name: on and self._use_tool(n), None, icon,
                          checkable=True)
            a.setToolTip(text)
            self.tool_group.addAction(a)
            self.tool_acts[name] = a
        self.tool_acts["select"].setChecked(True)
        d = lambda fn: (lambda: self.doc and fn(self.doc))  # noqa: E731
        act = self._act
        self.a_color = act("ფერი…", self._pick_color, icon=0xE790)
        self.a_sigpic = act("ხელმოწერის სურათი…", d(lambda x: x.place_signature()), icon=0xE929)
        self.a_flat = act("კომენტარების და ველების გაერთიანება გვერდთან…", d(lambda x: x.flatten()))
        self.a_author = act("კომენტარის ავტორის სახელი…", self._set_author, needs_doc=False)
        self.a_water = act("წყლის ნიშანი…", d(lambda x: x.watermark()))
        self.a_head = act("კოლონტიტული და ნუმერაცია…", d(lambda x: x.header()))
        self.a_bates = act("Bates ნუმერაცია…", d(lambda x: x.header(True)))
        self.a_back = act("ფონის ფერი…", self._background)
        self.a_ocr = act("ტექსტის ამოცნობა (OCR)…", d(lambda x: x.ocr()), "Ctrl+Shift+O")
        self.a_redsel = act("მონიშნული ტექსტის დასაფარად მონიშვნა", d(lambda x: x.redact_selection()))
        self.a_redfind = act("ნაპოვნის დასაფარად მონიშვნა", d(lambda x: x.redact_search()))
        self.a_redclear = act("მონიშვნების მოხსნა", d(lambda x: x.clear_marks()))
        self.a_redgo = act("მონიშნულის სამუდამოდ წაშლა…", d(lambda x: x.apply_redactions()))
        self.a_scrub = act("ფარული მონაცემების წაშლა…", d(lambda x: x.scrub()))
        self.a_protect = act("პაროლი და შეზღუდვები…", d(lambda x: x.protect()))
        self.a_compress = act("ფაილის შემცირება…", d(lambda x: x.compress()))
        self.a_sign = act("ციფრული ხელმოწერა სერტიფიკატით…", d(lambda x: x.sign()))
        self.a_signbox = act("ხილული ციფრული ხელმოწერა…", d(lambda x: (
            x.set_tool("signbox"), x.status.emit("დახატეთ ჩარჩო ხელმოწერისთვის"))))
        self.a_verify = act("ხელმოწერების შემოწმება", d(lambda x: x.verify()))
        self.a_compare = act("შედარება სხვა ვერსიასთან…", d(lambda x: x.compare_with()))
        self.a_create = act("PDF სხვა ფაილებისგან…", self.create_pdf, "Ctrl+N", 0xE8A5,
                            needs_doc=False)
        self.a_scan = act("სკანის გასუფთავება…", self.scan, "Ctrl+Shift+N", 0xE8FE,
                          needs_doc=False)
        self.a_batch = act("სერიული დამუშავება…", lambda: BatchDialog(self).exec(),
                           needs_doc=False)
        self.a_exports = [act(t + "…", d(lambda x, k=k: x.export_as(k))) for k, t in (
            ("docx", "Word"), ("xlsx", "Excel"), ("pptx", "PowerPoint"), ("txt", "ტექსტი"),
            ("html", "HTML"))]
        self.a_img = act("სურათები…", self._export_images)
        self.a_pdfa = act("PDF/A (არქივი)…", d(lambda x: x.export_pdfa()))
        self.a_ai = act("AI ასისტენტი", self._toggle_ai, "Ctrl+Shift+A", 0xE99A, checkable=True)
        self.a_gray_by = [self.a_water, self.a_head, self.a_bates, self.a_back, self.a_ocr,
                          self.a_redgo, self.a_scrub, self.a_compress, self.a_flat,
                          self.tool_acts["edit"], self.tool_acts["redact"],
                          self.tool_acts["field"], self.tool_acts["image"], self.a_sigpic]

    def _use_tool(self, name):
        if self.doc:
            self.doc.set_tool(name)

    def _pick_color(self):
        c = QColorDialog.getColor(self.doc.view.tool_color, self, "ფერი")
        if c.isValid():
            for d in self._docs():
                d.view.tool_color = c
            self.settings.setValue("color", c.name())

    def _background(self):
        c = QColorDialog.getColor(QColor("#fffbe8"), self, "ფონის ფერი")
        if c.isValid() and self.doc:
            self.doc.background(c)

    def _set_author(self):
        name, ok = QInputDialog.getText(self, "ავტორი", "სახელი, რომელიც კომენტარებს დაერთვება:",
                                        text=self.settings.value("author", ""))
        if ok:
            self.settings.setValue("author", name.strip())
            for d in self._docs():
                d.author = name.strip()

    def _export_images(self):
        fmt, ok = QInputDialog.getItem(self, "სურათები", "ფორმატი:", [
            "PNG 150 dpi", "PNG 300 dpi", "JPG 150 dpi", "JPG 300 dpi"], 0, False)
        if ok:
            kind, dpi, _ = fmt.split()
            self.doc.export_images(kind.lower(), int(dpi))

    def _toggle_ai(self, on):
        doc = self.doc
        if not doc:
            return
        if getattr(doc, "ai", None) is None and on:
            def texts():
                try:
                    return run_task(doc.client, "page_texts", (), "ტექსტი მზადდება…", doc)
                except TaskError as e:
                    QMessageBox.warning(self, "AI", error_text(str(e)))
                    return None
            doc.ai = AiPanel(doc, self.settings, texts)
            doc.ai.setMinimumWidth(300)
            doc.splitter.addWidget(doc.ai)
            doc.splitter.setStretchFactor(1, 1)
        if getattr(doc, "ai", None) is not None:
            doc.ai.setVisible(on)

    def scan(self):
        doc = self.doc
        paths = []
        if doc and doc.loaded and not doc._busy and not doc.dirty:
            lines = doc._quiet("chars", doc.view.page)
            if lines is not None and sum(map(len, lines)) < 15:  # the open file is a scan itself
                paths = [doc.path]
        dlg = ScanDialog(self, paths)
        if dlg.exec() and dlg.result_path:
            self.statusBar().showMessage(f"შეიქმნა; ამოცნობილია {dlg.words} სიტყვა", 8000)
            self.open_file(dlg.result_path)

    def create_pdf(self):
        kinds = " ".join(f"*{e}" for e in sorted(SUPPORTED))
        paths, _ = QFileDialog.getOpenFileNames(self, "რისგან შევქმნა PDF", "",
                                                f"ფაილები ({kinds})")
        if paths:
            self.make_pdf(paths)

    def make_pdf(self, paths):
        stem = os.path.splitext(paths[0])[0]
        out, _ = QFileDialog.getSaveFileName(self, "სად შევინახო PDF", files.unique_path(
            stem + ".pdf"), "PDF ფაილები (*.pdf)")
        if not out:
            return
        if not out.lower().endswith(".pdf"):
            out = files.unique_path(out + ".pdf")
        if any(os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(out))
               for p in paths):
            QMessageBox.warning(self, "PDF", "აირჩიეთ სხვა სახელი: ეს ერთ-ერთი საწყისი ფაილია.")
            return
        def ext(p):
            return os.path.splitext(p)[1].lower()
        web = [p for p in paths if files.from_internet(p) and ext(p) in WORD | EXCEL | SLIDES | WEB]
        old = [p for p in paths if ext(p) in (WORD | EXCEL | SLIDES) - OOXML]
        if web or old:
            names = "\n".join("• " + os.path.basename(p) for p in dict.fromkeys(web + old))
            why = ("ნაწილი ინტერნეტიდან არის ჩამოტვირთული. " if web else "") + (
                "ძველი ფორმატის ფაილებში გარე ბმულებს ვერ ვჭრი: გახსნისას Office შეიძლება ქსელს "
                "დაუკავშირდეს. " if old else "")
            if QMessageBox.question(self, "PDF", f"ეს ფაილები შესამოწმებელია:\n\n{names}\n\n{why}"
                                    "გააგრძელეთ, თუ წყაროს ენდობით.") != QMessageBox.StandardButton.Yes:
                return
        client = EngineClient(self)
        tmp = files.temp_name(out)
        try:
            run_task(client, "make_pdf", (paths, tmp, bool(old)), "PDF იქმნება…", self)
            files.put_in_place(tmp, out)
        except TaskError as e:
            code, _, n = str(e).partition("#")
            name = os.path.basename(paths[int(n)]) + ": " if n.isdigit() else ""
            QMessageBox.warning(self, "PDF", name + error_text(code))
            return
        except OSError:
            QMessageBox.warning(self, "PDF", error_text("no_access"))
            return
        finally:
            client.shutdown()
            client.deleteLater()
            files.discard(tmp)
        self.open_file(out)

    def _toolbar(self):
        tb = QToolBar("ხელსაწყოები")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)
        self.toolbar = tb
        tb.addActions([self.a_side, self.a_open, self.a_save, self.a_print])
        tb.addSeparator()
        tb.addActions([self.a_undo, self.a_redo, self.a_org])
        tb.addSeparator()
        tb.addActions([self.a_pgup, self.a_pgdn])
        self.page_box = QSpinBox()
        self.page_box.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.page_box.setKeyboardTracking(False)
        self.page_box.setMinimumWidth(56)
        self.page_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_box.valueChanged.connect(self._page_typed)
        self.page_total = QLabel(" / 0 ")
        tb.addWidget(self.page_box)
        tb.addWidget(self.page_total)
        tb.addSeparator()
        tb.addAction(self.a_out)
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.zoom_box.addItems([f"{z}%" for z in ZOOMS])
        self.zoom_box.setMinimumWidth(84)
        self.zoom_box.activated.connect(lambda *_: self._zoom_typed())
        self.zoom_box.lineEdit().returnPressed.connect(self._zoom_typed)
        tb.addWidget(self.zoom_box)
        tb.addAction(self.a_in)
        tb.addActions([self.a_width, self.a_page])
        tb.addSeparator()
        tb.addActions([self.a_two, self.a_rot, self.a_find, self.a_ai])
        self.addToolBarBreak()
        t2 = QToolBar("კომენტარი და რედაქტირება")
        t2.setMovable(False)
        t2.setIconSize(QSize(18, 18))
        self.addToolBar(t2)
        self.toolbar2 = t2
        for name, _, _ in self.TOOLS:
            if name in ("note", "ink", "stamp", "edit"):
                t2.addSeparator()
            t2.addAction(self.tool_acts[name])
        t2.addSeparator()
        t2.addActions([self.a_sigpic, self.a_color])

    def _menus(self):
        mb = self.menuBar()
        f = mb.addMenu("ფაილი")
        f.addAction(self.a_open)
        self.recent_menu = f.addMenu("ბოლოს გახსნილი")
        f.addActions([self.a_save, self.a_save_as])
        f.addSeparator()
        f.addActions([self.a_create, self.a_scan, self.a_merge, self.a_split, self.a_batch])
        ex = f.addMenu("ექსპორტი")
        ex.addActions(self.a_exports + [self.a_img, self.a_pdfa])
        f.addAction(self.a_compress)
        f.addSeparator()
        f.addActions([self.a_print, self.a_props, self.a_backups])
        f.addSeparator()
        f.addActions([self.a_close, self.a_quit])
        e = mb.addMenu("რედაქტირება")
        e.addActions([self.a_undo, self.a_redo])
        e.addSeparator()
        e.addAction(self.a_copy)
        e.addSeparator()
        e.addActions([self.a_find, self.a_next, self.a_prev])
        g = mb.addMenu("გვერდები")
        g.addAction(self.a_org)
        g.addSeparator()
        self._fill_page_menu(g)
        c = mb.addMenu("კომენტარი")
        for name in ("highlight", "underline", "strikeout", "note", "text", "ink", "rect",
                     "ellipse", "line", "arrow", "stamp"):
            c.addAction(self.tool_acts[name])
        c.addSeparator()
        c.addActions([self.a_sigpic, self.a_color, self.a_author, self.a_flat])
        i = mb.addMenu("შიგთავსი")
        i.addActions([self.tool_acts["edit"], self.tool_acts["text"], self.tool_acts["image"],
                      self.tool_acts["field"]])
        i.addSeparator()
        i.addActions([self.a_ocr, self.a_water, self.a_head, self.a_bates, self.a_back])
        z = mb.addMenu("უსაფრთხოება")
        z.addActions([self.tool_acts["redact"], self.a_redsel, self.a_redfind, self.a_redclear,
                      self.a_redgo])
        z.addSeparator()
        z.addActions([self.a_scrub, self.a_protect])
        z.addSeparator()
        z.addActions([self.a_sign, self.a_signbox, self.a_verify])
        t = mb.addMenu("ხელსაწყოები")
        t.addActions([self.a_scan, self.a_compare, self.a_ai])
        v = mb.addMenu("ხედი")
        v.addActions([self.a_in, self.a_out, self.a_100, self.a_width, self.a_page])
        v.addSeparator()
        v.addActions([self.a_two, self.a_rot, self.a_side, self.a_full])
        v.addSeparator()
        v.addActions([self.a_pgup, self.a_pgdn, self.a_goto])
        v.addSeparator()
        menu = v.addMenu("თემა")
        group = QActionGroup(self)
        self.theme_actions = {}
        now = self.settings.value("theme", "light")
        for mode, text in (("light", "ქაღალდი"), ("dark", "ღამე"), ("system", "სისტემის მიხედვით")):
            a = QAction(text, self, checkable=True)
            a.setChecked(mode == now)
            a.triggered.connect(lambda _=False, m=mode: self.set_theme(m))
            group.addAction(a)
            menu.addAction(a)
            self.theme_actions[mode] = a
        mb.addMenu("დახმარება").addAction(self.a_about)

    def set_theme(self, mode):
        """Puts a theme on the program and remembers it."""
        from PySide6.QtWidgets import QApplication
        self.settings.setValue("theme", mode)
        if mode in self.theme_actions:
            self.theme_actions[mode].setChecked(True)
        theme.apply_theme(QApplication.instance(), mode)
        self.repaint_theme()

    def repaint_theme(self):
        ink = self.palette().color(QPalette.ColorRole.WindowText)
        for a, code in self._glyphs:
            a.setIcon(glyph(code, ink))
        self.welcome.recolor()
        for d in self._docs():
            d.view.viewport().update()
            for w in (getattr(d, "list", None), getattr(d, "grid", None)):
                if w is not None:
                    w.viewport().update()

    def _fill_page_menu(self, menu):
        for a in self.page_actions:
            if a is None:
                menu.addSeparator()
            else:
                menu.addAction(a)

    def _page_menu(self, pos):
        menu = QMenu(self)
        self._fill_page_menu(menu)
        menu.exec(pos)

    def _organizer_bar(self, doc):
        for a in (self.a_rl, self.a_rr, self.a_del, self.a_dup, self.a_ins, self.a_blank,
                  self.a_ext, self.a_crop):
            b = QToolButton()
            b.setDefaultAction(a)
            b.setAutoRaise(True)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            doc.org_bar.addWidget(b)
        doc.org_bar.addStretch()
        done = QPushButton("დასრულება")
        done.clicked.connect(lambda: doc.organize(False))
        doc.org_bar.addWidget(done)

    def _organize(self, on):
        if self.doc:
            self.doc.organize(on)

    def merge(self):
        paths = [d.path for d in self._docs() if d.loaded]
        if any(d.dirty for d in self._docs()):
            QMessageBox.information(self, "გაერთიანება", "გაერთიანება შენახულ ფაილებს იყენებს. "
                                    "შეუნახავი ცვლილებები არ მოხვდება.")
        dlg = MergeDialog(self, paths)
        if dlg.exec() and dlg.result_path:
            self.open_file(dlg.result_path)

    def extract(self):
        out = self.doc.extract_pages()
        if out:
            self.statusBar().showMessage(f"შეიქმნა: {out}", 8000)
            self.open_file(out)

    def _docs(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def _welcome(self):
        self.welcome = WelcomeScreen([
            ("ფაილის გახსნა…", "Ctrl+O", 0xE8E5, self.choose_files),
            ("PDF სხვა ფაილებისგან…", "Word, Excel, სურათები", 0xE8A5, self.create_pdf),
            ("ფოტოს ან სკანის გასუფთავება…", "JPG, PNG, HEIC, სურათოვანი PDF", 0xE8FE,
             lambda: self.scan()),
            ("ფაილების გაერთიანება…", "Ctrl+M", 0xE8F1, self.merge)])
        self.welcome.chosen.connect(self.open_file)
        self.recent_label, self.recent_list = self.welcome.recent_label, self.welcome.recent_list
        return self.welcome

    # -- documents ---------------------------------------------------------
    @property
    def doc(self) -> DocView | None:
        w = self.tabs.currentWidget()
        return w if isinstance(w, DocView) else None

    def _with_view(self, fn):
        def run():
            if self.doc:
                fn(self.doc.view)
        return run

    def choose_files(self):
        start = self.settings.value("dir", "")
        paths, _ = QFileDialog.getOpenFileNames(self, "PDF-ის გახსნა", start,
                                                "PDF ფაილები (*.pdf);;ყველა ფაილი (*)")
        for p in paths:
            self.open_file(p)

    def open_file(self, path):
        path = os.path.abspath(path)
        for i in range(self.tabs.count()):
            if os.path.normcase(self.tabs.widget(i).path) == os.path.normcase(path):
                self.tabs.setCurrentIndex(i)
                return
        try:
            start = int(self._positions().get(os.path.normcase(path), 0))
        except (TypeError, ValueError):  # a damaged settings file
            start = 0
        doc = DocView(path)
        doc.start_page = start
        doc.is_open_elsewhere = lambda p: any(
            d is not doc and os.path.normcase(d.path) == os.path.normcase(p) for d in self._docs())
        doc.ready.connect(lambda: self._ready(doc))
        doc.closed.connect(lambda msg: self._open_failed(doc, msg))
        doc.status.connect(lambda m: self.statusBar().showMessage(m, 8000))
        doc.view.pageChanged.connect(lambda *_: self._sync())
        doc.view.zoomChanged.connect(lambda *_: self._sync())
        doc.view.hovered.connect(lambda t: self.link_label.setText(
            t if len(t) <= 120 else t[:120] + "…"))
        doc.changed.connect(self._sync)
        doc.pageMenu.connect(self._page_menu)
        doc.openRequested.connect(self.open_file)
        doc.author = self.settings.value("author", "")
        doc.view.tool_color = QColor(self.settings.value("color", theme.color("ink-pen").name()))
        self._organizer_bar(doc)
        i = self.tabs.addTab(doc, os.path.basename(path))
        self.tabs.setTabToolTip(i, path)
        self.tabs.setCurrentIndex(i)
        self.stack.setCurrentIndex(1)

    def _ready(self, doc):
        self.settings.setValue("dir", os.path.dirname(doc.path))
        times = self._opened_times()
        times[os.path.normcase(doc.path)] = int(time.time())
        keep = {os.path.normcase(p) for p in [doc.path] + self._recent()[:MAX_RECENT]}
        self.settings.setValue("opened", {k: v for k, v in times.items() if k in keep})
        self._set_recent([doc.path] + [p for p in self._recent()
                                       if os.path.normcase(p) != os.path.normcase(doc.path)])
        self._sync()

    def _open_failed(self, doc, msg):
        if msg:
            QMessageBox.warning(self, os.path.basename(doc.path), msg)
        i = self.tabs.indexOf(doc)
        if i >= 0:
            self.close_tab(i, ask=False)

    def _may_close(self, doc) -> bool:
        if not doc.dirty and not doc._busy:
            if not doc.view.marks:
                return True
            self.tabs.setCurrentWidget(doc)
            return doc.marks_ok("დახურვისას მონიშვნები დაიკარგება. დავხურო?")
        self.tabs.setCurrentWidget(doc)
        box = QMessageBox(QMessageBox.Icon.Question, "შეუნახავი ცვლილებები",
                          f"„{os.path.basename(doc.path)}“-ში შეუნახავი ცვლილებებია. შევინახო?",
                          parent=self)
        yes = box.addButton("შენახვა", QMessageBox.ButtonRole.AcceptRole)
        no = box.addButton("არ შეინახო", QMessageBox.ButtonRole.DestructiveRole)
        theme.variant(no, "danger")
        box.addButton("გაუქმება", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(yes)
        box.exec()
        if box.clickedButton() is yes:
            doc.wait_idle()  # a running edit belongs to what is saved
            return doc.save()
        return box.clickedButton() is no

    def close_tab(self, i, ask=True):
        doc = self.tabs.widget(i)
        if doc is None or (ask and not self._may_close(doc)):
            return
        self._remember(doc)
        self.tabs.removeTab(i)
        doc.close_document()
        doc.deleteLater()
        if not self.tabs.count():
            self.stack.setCurrentIndex(0)
        self._sync()

    # -- settings ----------------------------------------------------------
    def _recent(self):
        v = self.settings.value("recent", [])
        return [v] if isinstance(v, str) else list(v or [])

    def _set_recent(self, paths):
        self.settings.setValue("recent", paths[:MAX_RECENT])
        self._refresh_recent()

    def _refresh_recent(self):
        paths = [p for p in self._recent() if p]
        self.welcome.set_recent(paths, self._opened_times())
        self.recent_menu.clear()
        for p in paths:
            self.recent_menu.addAction(p, lambda p=p: self.open_file(p))
        if paths:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("სიის გასუფთავება", lambda: self._set_recent([]))
        self.recent_menu.setEnabled(bool(paths))

    def _opened_times(self) -> dict:
        v = self.settings.value("opened", {})
        return v if isinstance(v, dict) else {}

    def _positions(self) -> dict:
        v = self.settings.value("positions", {})
        return v if isinstance(v, dict) else {}

    def _remember(self, doc):
        if not doc.loaded:
            return
        pos = self._positions()
        key = os.path.normcase(doc.path)
        pos.pop(key, None)
        pos[key] = doc.view.page
        while len(pos) > 100:
            pos.pop(next(iter(pos)))
        self.settings.setValue("positions", pos)

    # -- state -------------------------------------------------------------
    def _sync(self, *_):
        doc = self.doc
        on = bool(doc and doc.loaded)
        for a in self._doc_actions:
            a.setEnabled(on or (a is self.a_close and doc is not None))
        self.page_box.setEnabled(on)
        self.zoom_box.setEnabled(on)
        self.page_box.blockSignals(True)
        self.a_two.blockSignals(True)
        for i, d in enumerate(self._docs()):
            self.tabs.setTabText(i, ("● " if d.dirty else "") + os.path.basename(d.path))
            self.tabs.setTabToolTip(i, d.path)
        self.a_org.blockSignals(True)
        self.a_org.setChecked(bool(on and doc.organizing))
        self.a_org.blockSignals(False)
        for a in self._edit_actions:
            a.setEnabled(bool(on and doc.can_edit))
        tool = doc.view.tool if on else "select"
        if tool in self.tool_acts and not self.tool_acts[tool].isChecked():
            self.tool_acts[tool].blockSignals(True)
            self.tool_acts[tool].setChecked(True)
            self.tool_acts[tool].blockSignals(False)
        ai = getattr(doc, "ai", None) if on else None
        self.a_ai.blockSignals(True)
        self.a_ai.setChecked(ai is not None and not ai.isHidden())
        self.a_ai.blockSignals(False)
        if on:
            for a in self.a_gray_by:
                a.setEnabled(doc.info["can_modify"])
            for name in ("highlight", "underline", "strikeout", "note", "text", "ink", "rect",
                         "ellipse", "line", "arrow", "stamp"):
                self.tool_acts[name].setEnabled(doc.info["can_annotate"])
            self.a_redgo.setEnabled(doc.info["can_modify"] and bool(doc.view.marks))
            self.a_redclear.setEnabled(bool(doc.view.marks))
            self.a_sign.setEnabled(not doc.dirty)
            self.a_signbox.setEnabled(not doc.dirty)
            self.a_org.setEnabled(doc.info["can_assemble"])
            self.a_undo.setEnabled(doc.can_undo)
            self.a_redo.setEnabled(doc.can_redo)
            self.a_save.setEnabled(doc.dirty)
            self.a_print.setEnabled(doc.info["can_print"])
            self.page_box.setRange(1, len(doc.sizes))
            self.page_box.setValue(doc.view.page + 1)
            self.page_total.setText(f" / {len(doc.sizes)} ")
            self.zoom_box.setEditText(f"{round(doc.view.zoom * 100)}%")
            self.a_two.setChecked(doc.view.two_up)
            mark = "● " if doc.dirty else ""
            self.setWindowTitle(f"{mark}{os.path.basename(doc.path)} — {APP_NAME}")
        else:
            self.page_total.setText(" / 0 ")
            self.zoom_box.setEditText("")
            self.setWindowTitle(APP_NAME)
        self.page_box.blockSignals(False)
        self.a_two.blockSignals(False)

    def _page_typed(self, value):
        if self.doc:
            self.doc.view.go_to(value - 1)
            self.doc.view.setFocus()

    def _zoom_typed(self):
        if not self.doc:
            return
        try:
            z = float(self.zoom_box.currentText().replace("%", "").replace(",", ".").strip())
            if math.isfinite(z):
                self.doc.view.set_zoom(z / 100)
        except ValueError:
            pass
        self._sync()
        self.doc.view.setFocus()

    def _focus_page(self):
        self.page_box.setFocus()
        self.page_box.selectAll()

    def _two_up(self, on):
        if self.doc:
            self.doc.view.set_two_up(on)

    def _toggle_side(self):
        self.doc.sidebar.setVisible(not self.doc.sidebar.isVisible())

    def _full(self):
        full = not self.isFullScreen()
        self.toolbar.setVisible(not full)
        self.toolbar2.setVisible(not full)
        self.menuBar().setVisible(not full)
        self.statusBar().setVisible(not full)
        if full:
            self._was_max = self.isMaximized()
            self.showFullScreen()
        elif getattr(self, "_was_max", False):
            self.showMaximized()
        else:
            self.showNormal()

    def print_doc(self):
        if not self.doc.marks_ok("დაბეჭდილზე მათი შიგთავსი გამოჩნდება. გავაგრძელო?"):
            return
        try:
            if choose_and_print(self.doc, self):
                self.statusBar().showMessage("გაიგზავნა დასაბეჭდად", 6000)
        except PrintError as e:
            QMessageBox.warning(self, "ბეჭდვა", str(e))

    def properties(self):
        doc = self.doc
        m = doc.info["metadata"]
        w, h = doc.sizes[doc.view.page]
        size = doc.info["bytes"]
        rows = [("ფაილი", doc.path), ("სათაური", m.get("title")), ("ავტორი", m.get("author")),
                ("თემა", m.get("subject")), ("საკვანძო სიტყვები", m.get("keywords")),
                ("შექმნილია პროგრამით", m.get("creator")), ("PDF გენერატორი", m.get("producer")),
                ("ფორმატი", m.get("format")), ("დაშიფვრა", m.get("encryption") or "არ აქვს"),
                ("გვერდები", str(len(doc.sizes))),
                ("გვერდის ზომა", f"{w / 72 * 25.4:.0f} × {h / 72 * 25.4:.0f} მმ"),
                ("ფაილის ზომა", f"{size / 1048576:.2f} მბ" if size > 1048576
                 else f"{size / 1024:.0f} კბ"),
                ("ბეჭდვა", "ნებადართულია" if doc.info["can_print"] else "აკრძალულია"),
                ("კოპირება", "ნებადართულია" if doc.info["can_copy"] else "აკრძალულია")]
        dlg = QDialog(self)
        dlg.setWindowTitle("ფაილის თვისებები")
        form = QFormLayout(dlg)
        for k, v in rows:
            if v:
                lab = QLabel(str(v))
                lab.setTextFormat(Qt.TextFormat.PlainText)
                lab.setWordWrap(True)
                lab.setMinimumWidth(380)
                lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                form.addRow(k + ":", lab)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        dlg.exec()

    def about(self):
        QMessageBox.about(self, APP_NAME,
                          f"{APP_NAME} {__version__}\n\nPDF ძრავა: MuPDF (AGPL-3.0)\n"
                          "OCR: Tesseract · Word: pdf2docx · ხელმოწერა: pyHanko\n"
                          "ინტერფეისი: Qt / PySide6 (LGPL-3.0)")

    # -- events ------------------------------------------------------------
    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self._full()
            return
        ctrl, shift = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier
        if e.modifiers() in (ctrl, ctrl | shift):
            keys = self._by_vk_shift if e.modifiers() & shift else self._by_vk
            a = keys.get(e.nativeVirtualKey())
            if a is not None and a.isEnabled():
                a.trigger()
                return
        super().keyPressEvent(e)

    def dragEnterEvent(self, e):
        if any(os.path.splitext(u.toLocalFile())[1].lower() in SUPPORTED
               for u in e.mimeData().urls()):
            e.acceptProposedAction()
            self.drop_frame.setGeometry(self.centralWidget().geometry())
            self.drop_frame.raise_()
            self.drop_frame.show()

    def dragLeaveEvent(self, e):
        self.drop_frame.hide()

    def dropEvent(self, e):
        self.drop_frame.hide()
        other = []
        for u in e.mimeData().urls():
            path = u.toLocalFile()
            if path.lower().endswith(".pdf"):
                self.open_file(path)
            elif os.path.splitext(path)[1].lower() in SUPPORTED:
                other.append(path)
        if other:
            self.make_pdf(other)

    def closeEvent(self, e):
        for doc in self._docs():
            if not self._may_close(doc):
                e.ignore()
                return
        self.settings.setValue("geometry", self.saveGeometry())
        while self.tabs.count():
            self.close_tab(0, ask=False)
        self.settings.sync()
        finish_all()  # no engine, Office or Edge may outlive the program
        e.accept()
