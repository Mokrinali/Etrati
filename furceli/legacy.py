"""Old Georgian fonts (AcadNusx and similar) store Latin codes; map them to Unicode."""
from __future__ import annotations

import re

FONT = re.compile(r"acad(nusx|mtavr|emiuri|emi)|lit(nusx|mtavr)|^(.*\+)?acad", re.I)
TABLE = dict(zip("abgdevzTiklmnopJrstufqRySCcZwWxjh",
                 "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ"))


def is_legacy(font_name: str) -> bool:
    return bool(FONT.search(font_name or ""))


def to_unicode(ch: str) -> str:
    return TABLE.get(ch, ch)
