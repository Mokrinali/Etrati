"""File types that can become a PDF (kept apart: the window must not load converters)."""
IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp", ".jp2", ".heic", ".heif"}
WORD = {".doc", ".docx", ".rtf", ".odt", ".docm"}
EXCEL = {".xls", ".xlsx", ".xlsm", ".ods", ".csv"}
SLIDES = {".ppt", ".pptx", ".pptm", ".odp"}
WEB = {".html", ".htm"}
TEXT = {".txt", ".md", ".log"}
OTHER = {".xps", ".epub", ".svg", ".fb2", ".cbz"}
SUPPORTED = IMAGES | WORD | EXCEL | SLIDES | WEB | TEXT | OTHER | {".pdf"}
OOXML = {".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm"}  # external links can be cut
