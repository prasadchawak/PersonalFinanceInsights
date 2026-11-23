"""Re-create docs/screenshots from made-up demo data.

    python tools/make_demo.py /tmp/demo-data
    PFI_DATA_DIR=/tmp/demo-data python -m app        # in another terminal (PFI_NO_BROWSER=1)
    python tools/take_screenshots.py                 # needs: pip install playwright pillow; playwright install chromium

Images are quantised to 256 colours to keep the repository small.
"""
import os
import sqlite3
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

B = os.environ.get("PFI_URL", "http://127.0.0.1:8765")
OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
DATA = Path(os.environ.get("PFI_DATA_DIR", "/tmp/demo-data"))


def main():
    db = sqlite3.connect(DATA / "finance.db")
    one = lambda sql: db.execute(sql).fetchone()[0]
    food = one("SELECT id FROM categories WHERE name = 'Food'")
    split_tx = one("SELECT transaction_id FROM splits LIMIT 1")
    savings_cat = one("SELECT id FROM categories WHERE name = 'Paid from savings fund'")
    grocery_card = one("SELECT id FROM accounts WHERE name = 'Grocery Card'")
    rotating_card = one("SELECT id FROM accounts WHERE name = 'Rotating 5% Card'")
    main_card = one("SELECT id FROM accounts WHERE name = 'Rewards Card'")
    exe = os.environ.get("CHROME")
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        pg = b.new_context(viewport={"width": 1280, "height": 860}).new_page()
        pg.on("dialog", lambda d: d.accept())

        def go(u):
            pg.goto(B + u)
            pg.wait_for_load_state("networkidle")

        def shot(name, full=False, clip_h=None):
            kw = {"full_page": True, "clip": {"x": 0, "y": 0, "width": 1280, "height": clip_h}} if clip_h else {"full_page": full}
            pg.screenshot(path=str(OUT / name), **kw)

        go("/"); shot("overview.png", full=True)
        go("/transactions"); pg.locator(".rowcb").nth(1).check(); pg.locator(".rowcb").nth(2).check()
        pg.select_option("#bulk select[name=category_id]", index=3); shot("transactions.png")
        go(f"/transactions/{split_tx}/split"); shot("split.png", clip_h=620)
        go("/import"); pg.set_input_files("input[type=file]", str(Path(__file__).resolve().parent.parent / "tests/samples/checking_single.csv"))
        pg.locator("form[action='/import'] button").click(); pg.wait_for_load_state("networkidle"); shot("import-mapping.png", full=True)
        go("/rules"); pg.locator("table.rules tr", has_text="MARKETPLACE").first.locator("details.ruleedit summary").click()
        pg.wait_for_timeout(200); shot("rules.png")
        go("/budgets"); shot("budgets.png", full=True)
        go("/reports?report=spending&period=6m&view=pie"); shot("report-spending-pie.png", full=True)
        go(f"/reports?report=spending&period=6m&within={food}&view=bar"); shot("report-subcategories-bar.png", full=True)
        go("/reports?report=cashflow&period=6m"); shot("report-income-vs-expenses.png", clip_h=1180)
        go("/reports?report=merchants&period=6m&view=bar"); shot("report-merchants.png", clip_h=1050)
        go("/categories"); row = pg.locator(f"#cat{savings_cat}"); row.scroll_into_view_if_needed()
        pg.evaluate(f"window.scrollBy(0, {row.bounding_box()['y']} - 330)"); pg.wait_for_timeout(100)
        pg.screenshot(path=str(OUT / "categories.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 560})
        go("/orgs"); shot("organisations.png", clip_h=560)
        go("/rewards?period=ytd"); shot("rewards.png", full=True)
        go(f"/accounts/{grocery_card}/rewards"); shot("card-setup.png", full=True)
        go(f"/accounts/{rotating_card}/rewards"); pg.locator("details.rowedit summary").first.click(); pg.wait_for_timeout(150)
        shot("card-rotating-rule.png")
        go(f"/transactions?account={main_card}&q=FRESHMART"); shot("transactions-rewards.png", clip_h=700)
        b.close()
    for f in OUT.glob("*.png"):
        Image.open(f).convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT,
                                              dither=Image.Dither.NONE).save(f, optimize=True)
    print(f"Saved {len(list(OUT.glob('*.png')))} screenshots to {OUT}")


if __name__ == "__main__":
    sys.exit(main())
