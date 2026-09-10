from __future__ import annotations

import csv as csvmod
import io
import re
from dataclasses import dataclass

from fallback.aggregate import RawRow


@dataclass(frozen=True, slots=True)
class BsrPage:
    stock_id: str
    rows: list[RawRow]


def _int(cell: str) -> int:
    cell = (cell or "").strip().replace(",", "")
    return int(cell) if cell.lstrip("-").isdigit() else 0


def _split_broker(cell: str) -> tuple[str, str]:
    cell = (cell or "").strip()
    if len(cell) < 4:
        return "", ""
    bid = cell[:4]
    # names pad with full-width spaces (U+3000); collapse any whitespace run
    # (regular or full-width) to a single ASCII space and strip the ends.
    name = " ".join(cell[4:].split())
    return bid, name


def parse_bsr_csv(text: str) -> BsrPage:
    lines = text.splitlines()
    stock_id = ""
    body_start = len(lines)
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        cells = next(csvmod.reader([line]))
        joined = "".join(cells)
        if "股票代碼" in joined and len(cells) >= 2:
            m = re.search(r"[0-9A-Za-z]{3,}", cells[1])
            stock_id = m.group(0) if m else cells[1].strip()
        elif cells and cells[0] == "序號":
            body_start = i + 1
            break

    rows: list[RawRow] = []
    for cells in csvmod.reader(io.StringIO("\n".join(lines[body_start:]))):
        for half in (cells[0:5], cells[6:11]):
            if len(half) < 5:
                continue
            bid, bname = _split_broker(half[1])
            if not bid:
                continue
            rows.append(RawRow(bid, bname, _int(half[3]), _int(half[4])))
    return BsrPage(stock_id, rows)
