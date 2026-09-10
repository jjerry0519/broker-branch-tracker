from __future__ import annotations

import re

_ALNUM5 = re.compile(r"^[A-Za-z0-9]{5}$")
_ocr = None


def looks_valid(text: str) -> bool:
    return bool(_ALNUM5.match(text or ""))


def solve(png_bytes: bytes) -> str:
    global _ocr
    if _ocr is None:
        import ddddocr
        _ocr = ddddocr.DdddOcr(show_ad=False)
    raw = _ocr.classification(png_bytes)
    return re.sub(r"[^A-Za-z0-9]", "", raw).upper()
