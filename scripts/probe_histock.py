"""CI probe: can patchright reach + pass HiStock's Cloudflare challenge from a
GitHub Actions runner IP, and load several 分點 pages in one browser session?"""
import sys, time
from patchright.sync_api import sync_playwright

PAIRS = [("9268", "2330"), ("1440", "2330"), ("9800", "2454"),
         ("9200", "2317"), ("5850", "2603"), ("8880", "3008")]
URL = "https://histock.tw/stock/brokertrace.aspx?bno={b}&no={s}"

def main() -> int:
    ok = 0
    with sync_playwright() as pw:
        br = pw.chromium.launch(headless=False)
        pg = br.new_page()
        t0 = time.time()
        try:
            pg.goto(URL.format(b="9268", s="2330"), wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"FIRST goto FAILED: {e!r}")
            print("=> likely a hard IP block (ERR_ABORTED) at Cloudflare edge")
            return 2
        for i in range(45):
            if "Just a moment" not in pg.title():
                break
            time.sleep(1)
        first_ok = "買進均價" in pg.content()
        print(f"first page: {i}s to clear, title={pg.title()!r}, has_table={first_ok}")
        if not first_ok:
            print("=> challenge did NOT clear on a GH IP")
            return 3
        for b, s in PAIRS[1:]:
            tp = time.time()
            try:
                pg.goto(URL.format(b=b, s=s), wait_until="domcontentloaded", timeout=45000)
                for _ in range(20):
                    if "Just a moment" not in pg.title():
                        break
                    time.sleep(1)
                html = pg.content()
                good = "買進均價" in html
                ndays = html.count("/2026") + html.count("/2025")
                ok += good
                print(f"  {b}/{s}: {'OK' if good else 'BLOCKED'} {time.time()-tp:.1f}s days~{ndays}")
            except Exception as e:
                print(f"  {b}/{s}: EXC {e!r}")
        br.close()
    print(f"=== {ok+1}/{len(PAIRS)} pages OK in {time.time()-t0:.0f}s ===")
    return 0 if ok >= len(PAIRS) - 2 else 4

if __name__ == "__main__":
    sys.exit(main())
