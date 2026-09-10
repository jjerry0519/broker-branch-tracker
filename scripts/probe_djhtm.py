"""CI reachability probe: can a GitHub Actions runner IP fetch the SysJust
(.djhtm) broker-branch aggregator without being IP-blocked / rate-limited?

Hits MoneyDJ + Fubon-eBrokerDJ mirrors with plain httpx (no browser). Prints
per-request status and a parsed row count. Exit 0 if >=80% of requests return
parseable data.
"""
import re
import sys
import time

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126 Safari/537.36")

HOSTS = [
    "https://www.moneydj.com",
    "https://fubon-ebrokerdj.fbs.com.tw",
]
# (stock, branch) pairs across TWSE + TPEx
PAIRS = [("2330", "9200"), ("2330", "1440"), ("2317", "9800"),
         ("2454", "1470"), ("6488", "9200"), ("3711", "1440"),
         ("2603", "5850"), ("3008", "8880")]
DATE_FROM, DATE_TO = "2025-06-01", "2025-09-05"


def cells(raw: bytes) -> list[str]:
    t = raw.decode("big5", "replace")
    t = re.sub(r"(?is)<script.*?</script>", " ", t)
    t = re.sub(r"<[^>]+>", "|", t)
    return [x.strip() for x in t.split("|") if x.strip()]


def main() -> int:
    ok = 0
    total = 0
    with httpx.Client(headers={"User-Agent": UA}, timeout=30,
                      follow_redirects=True) as c:
        for host in HOSTS:
            for stock, branch in PAIRS:
                total += 1
                url = (f"{host}/z/zc/zco/zco0/zco0.djhtm?A={stock}"
                       f"&BHID={branch}&b={branch}&C=1"
                       f"&D={DATE_FROM}&E={DATE_TO}")
                try:
                    r = c.get(url)
                    cs = cells(r.content)
                    rows = [x for x in cs if re.fullmatch(r"20\d\d/\d\d/\d\d", x)]
                    good = r.status_code == 200 and len(rows) > 0
                    ok += good
                    print(f"{host.split('//')[1]:32} {stock}/{branch} "
                          f"{r.status_code} rows={len(rows):3} "
                          f"{'OK' if good else 'FAIL'}")
                except Exception as e:  # noqa: BLE001
                    print(f"{host.split('//')[1]:32} {stock}/{branch} "
                          f"EXC {type(e).__name__}: {e}")
                time.sleep(1.0)
    print(f"\n=== {ok}/{total} requests returned parseable data ===")
    return 0 if ok >= total * 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
