# Etrati: rules for code changes

- New UI code takes colours, sizes and fonts only from `furceli/theme.py` (`theme.color()`, `theme.size()`) or from the style sheet `furceli/assets/etrati.qss`. Do not use `QColor("#...")`; colours that are written into a PDF are the only exception.
- New widgets and dialogs follow `design/components` (rules in each `README.md`, the look in `preview.html`). Labels go above fields; a dialog has one primary button (`theme.variant(button, "primary")`), last in the row. Irreversible actions use `"danger"`.
- The design tokens live in `design/tokens.json` (two themes: `light` and `dark`); `theme.py` reads that file at run time, so a build must ship it (`--add-data "design\tokens.json;design"`).
