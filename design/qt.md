# Qt იმპლემენტაცია

როგორ გადავიდეს ეს სისტემა PySide6 აპში. ნაბიჯ-ნაბიჯ ინსტრუქცია მეორე Claude-ისთვის არის `D:\Acrobat\DESIGN_HANDOFF.md`-ში; აქ არის ტექნიკური რუკა.

## ფაილები

- `furceli/theme.py` — ტოკენები `design/tokens.json`-იდან. **გადაწყვეტილება:** ფაილს `theme.py` გაშვებისას კითხულობს, exe-ს build-ში კი ეს ფაილი თან მიჰყვება (`--add-data "design	okens.json;design"`), ორი თემა, `apply_theme(app, "light"|"dark"|"system")`, `color(name)` / `size(name)` ფუნქციები კოდისთვის.
- `furceli/assets/etrati.qss` — სტილები `{token}` ჩანაცვლებით.
- `furceli/assets/fonts/` — NotoSansGeorgian, NotoSans (OFL) + `OFL.txt`.

## QPalette (Fusion)

| როლი | ტოკენი |
|---|---|
| Window | `surface-100` |
| Base | `surface-raised` |
| AlternateBase | `surface-200` |
| Mid (canvas, view.py) | `canvas` |
| Dark / Shadow | `line-strong` / `line` |
| Text, WindowText, ButtonText | `ink` |
| PlaceholderText | `ink-muted` |
| Button | `surface-raised` |
| Highlight | `brand` |
| HighlightedText | `on-brand` |
| Link | `brand-text` |
| ToolTipBase / ToolTipText | `ink` / `surface-100` |
| Disabled → Text, WindowText, ButtonText | `ink-disabled` |

## QSS ნიმუში

```css
* { font-family: "Noto Sans Georgian", "Noto Sans", "Segoe UI"; font-size: 13px; }
QMainWindow, QDialog { background: {surface-100}; }
QMenuBar { background: {surface-100}; padding: 2px 8px; }
QMenuBar::item { padding: 4px 8px; border-radius: 4px; }
QMenuBar::item:selected { background: {surface-200}; }
QToolBar { background: {surface-100}; border: 0; border-bottom: 1px solid {line}; spacing: 2px; padding: 0 8px; min-height: 44px; }
QToolBar::separator { width: 1px; background: {line}; margin: 12px 4px; }
QToolButton { border: 0; border-radius: 6px; padding: 6px; }
QToolButton:hover { background: {surface-200}; }
QToolButton:checked { background: {brand-soft}; }
QPushButton { font-weight: 600; min-height: 30px; padding: 0 12px; border: 1px solid {line-strong}; border-radius: 6px; background: {surface-raised}; }
QPushButton:hover { background: {surface-200}; }
QPushButton:disabled { background: {surface-200}; border-color: {line}; color: {ink-disabled}; }
QPushButton[variant="primary"] { background: {brand}; border-color: {brand}; color: {on-brand}; }
QPushButton[variant="primary"]:hover { background: {brand-hover}; border-color: {brand-hover}; }
QPushButton[variant="primary"]:pressed { background: {brand-pressed}; }
QPushButton[variant="danger"] { color: {danger}; border-color: {danger}; }
QPushButton[variant="ghost"] { background: transparent; border-color: transparent; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit { min-height: 30px; padding: 0 8px; border: 1px solid {line-strong}; border-radius: 6px; background: {surface-raised}; selection-background-color: {mark-selection}; selection-color: #231f1b; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus { border: 2px solid {focus}; }
QLineEdit[error="true"] { border-color: {danger}; }
QToolBar QSpinBox, QToolBar QComboBox { min-height: 26px; font-family: "Cascadia Mono", Consolas; font-size: 12px; }
QTabBar::tab { height: 30px; padding: 0 8px 0 12px; color: {ink-muted}; background: transparent; border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:hover { background: {surface-100}; color: {ink}; }
QTabBar::tab:selected { background: {surface-100}; color: {ink}; font-weight: 600; border-top: 2px solid {brand}; }
QTabWidget::pane { border: 0; }
QMenu { background: {surface-raised}; border: 1px solid {line}; border-radius: 8px; padding: 4px; }
QMenu::item { padding: 6px 24px 6px 8px; border-radius: 4px; }
QMenu::item:selected { background: {brand-soft}; color: {brand-text}; }
QMenu::item:disabled { color: {ink-disabled}; }
QMenu::separator { height: 1px; background: {line}; margin: 4px 8px; }
QStatusBar { background: {surface-200}; border-top: 1px solid {line}; color: {ink-muted}; min-height: 26px; font-size: 12px; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; border: 1px solid {line-strong}; background: {surface-raised}; }
QCheckBox::indicator { border-radius: 4px; }
QRadioButton::indicator { border-radius: 8px; }
QCheckBox::indicator:checked { background: {brand}; border-color: {brand}; image: url({check}); }
QRadioButton::indicator:checked { border: 5px solid {brand}; }
QListWidget, QTreeWidget, QTableWidget, QListView { background: {surface-raised}; border: 1px solid {line}; border-radius: 6px; }
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected { background: {brand-soft}; color: {ink}; }
QHeaderView::section { background: {surface-200}; color: {ink-muted}; font-size: 11px; font-weight: 500; padding: 6px 12px; border: 0; border-bottom: 1px solid {line}; }
QProgressBar { height: 6px; border: 0; border-radius: 3px; background: {surface-200}; text-align: center; }
QProgressBar::chunk { background: {brand}; border-radius: 3px; }
QScrollBar:vertical { width: 10px; background: transparent; margin: 2px; }
QScrollBar:horizontal { height: 10px; background: transparent; margin: 2px; }
QScrollBar::handle { background: {line-strong}; border-radius: 3px; min-height: 32px; min-width: 32px; }
QScrollBar::handle:hover { background: {ink-muted}; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { width: 0; height: 0; background: transparent; }
QSplitter::handle { background: {line}; }
QSplitter::handle:horizontal { width: 1px; }
QToolTip { background: {ink}; color: {surface-100}; border: 0; border-radius: 4px; padding: 4px 8px; font-size: 12px; }
```

QSS-ში `{token}` ჩაანაცვლე `theme.py`-მ; rgba მნიშვნელობები QSS-ში `rgba(r,g,b,a)` სახით (a 0–255 ან 0–1, Qt-ის ვერსიის მიხედვით შეამოწმე).

## მყარად ჩაწერილი ფერები კოდში → ტოკენები

| ფაილი:ხაზი (2026-11-26) | ახლა | ტოკენი |
|---|---|---|
| view.py `tool_color`, window.py settings default | `#d02020` | `ink-pen` |
| view.py ძიება | `#fff07a` / `#ff9d3b` | `mark-highlight` / `mark-current` |
| view.py მონიშვნა | `#a9cdfb` | `mark-selection` |
| view.py რედაქტირებადი ბლოკი | `#b8f0b0` | `mark-edit` |
| view.py ფორმის ველი | `#e3ecff` | `mark-field` |
| view.py დასაფარი | `#c00000` + შავი 70 | `mark-redact` |
| view.py ჩამოჭრა | `#2f7de1` | `mark-crop` |
| view.py ვერდახატული გვერდი | `#b00020` | `mark-error` |
| view.py ფონი | `QPalette.Mid` | `canvas` (Mid-ში ჩაწერე) |
| scan_dialog.py CornerView | `#2f7de1`, `QColor(47,125,225,28)` | `mark-crop`, `mark-crop-fill` |
| forms.py ხელმოწერა | `#10206a` | `ink-signature` |
| ai.py `_say` | `"gray"`, `#2f7de1`, `#c03030` | AiPanel-ის შეტყობინების ტიპები: note / me / error |
| thumbs.py | `Qt.GlobalColor.gray` ჩარჩო | `line-strong` |
| window.py ფონის ფერი | `#fffbe8` | ფაილში იწერება — დატოვე |

ფერები, რომლებიც PDF-ში იწერება (ანოტაცია, ხელმოწერა, წყლის ნიშანი, ფონის ფერი), თემასთან არ იცვლება — მხოლოდ ეკრანზე ხატვის ფერები (UI) მოდის თემიდან.

## წესები ახალი კოდისთვის

- ფერი, ზომა, შრიფტი — მხოლოდ `theme.color()/size()`-დან ან QSS-დან; `QColor("#...")` ახალ UI კოდში არა.
- ღილაკის ტიპი — `btn.setProperty("variant", "primary"|"ghost"|"danger")`; დიალოგში ერთი primary.
- ლეიბლი ველის ზემოთ (QVBoxLayout), არა QFormLayout-ის მარცხენა სვეტში, სადაც ტექსტი გრძელია.
- თემის შეცვლისას: `apply_theme` → ყველა `glyph()` აიკონის თავიდან დახატვა (WindowText ფერით) და `view.update()`.
