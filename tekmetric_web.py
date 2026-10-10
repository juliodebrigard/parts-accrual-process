"""Read-only access to the Tekmetric web app, for details the API doesn't give
(part order date, vendor invoice #, received status).

Tekmetric's login has a reCAPTCHA, so a person signs in once by hand
(`python tekmetric_web.py signin` opens a visible Chrome window). The session
lives in a private Chrome profile under ~/.config/tunerite-accrual/ and later
runs reuse it headlessly. No password is stored. This module only opens pages
and reads them; it never clicks anything that changes Tekmetric.
"""
import os
from datetime import timedelta
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

CONFIG_DIR = Path.home() / ".config/tunerite-accrual"
PROFILE_DIR = CONFIG_DIR / "chrome-profile"
BASE = "https://shop.tekmetric.com"
SHOPS = {"boca": 2545, "davie": 3937}


class NotSignedIn(RuntimeError):
    pass


def _on_login_page(page):
    return page.query_selector("input[name=password]") is not None


class TekmetricWeb:
    def __init__(self, visible=False):
        self.visible = visible

    def __enter__(self):
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(CONFIG_DIR, 0o700)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), channel="chrome", headless=not self.visible,
            viewport={"width": 1600, "height": 1000})
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *exc):
        self._ctx.close()
        self._pw.stop()

    def check_signed_in(self):
        self.page.goto(BASE + "/", wait_until="networkidle", timeout=60000)
        if _on_login_page(self.page):
            raise NotSignedIn("Tekmetric session expired. Run: python tekmetric_web.py signin")

    def wait_for_manual_sign_in(self, minutes=10):
        self.page.goto(BASE + "/", wait_until="networkidle", timeout=60000)
        deadline = minutes * 60 * 1000
        waited = 0
        while _on_login_page(self.page) and waited < deadline:
            self.page.wait_for_timeout(2000)
            waited += 2000
        if _on_login_page(self.page):
            raise NotSignedIn("Gave up waiting for sign-in")
        self.page.wait_for_timeout(5000)  # let the app finish saving its session

    def open_ro(self, shop, ro_id, wait_until="networkidle"):
        """Open an RO's Estimate tab by its internal id (not the RO number)."""
        url = f"{BASE}/admin/shop/{SHOPS[shop]}/repair-orders/{ro_id}/estimate"
        self.page.goto(url, wait_until=wait_until, timeout=60000)
        return self.page

    def _estimate(self, shop, ro_id):
        with self.page.expect_response(
                lambda r: r.url.split("?")[0].endswith(f"/repair-order/{ro_id}/estimate"),
                timeout=60000) as resp:
            # some RO pages keep polling and never go network-idle, so don't wait for that
            self.open_ro(shop, ro_id, wait_until="domcontentloaded")
        return resp.value.json()

    def parts_purchased(self, shop, start, end):
        """Tekmetric's Parts Purchased report (Reports > Parts > Parts Purchased) for
        [start, end): every purchase and return with RO #, PO #, vendor, invoice #, date, cost.
        Read the same way the report page reads it, with the page's own sign-in token."""
        token = {}

        def grab(req):
            if "/reports/parts-purchased/" in req.url and "x-auth-token" in req.headers:
                token["h"] = req.headers["x-auth-token"]
        self.page.on("request", grab)
        self.page.goto(f"{BASE}/admin/shop/{SHOPS[shop]}/reports/parts/purchased",
                       wait_until="domcontentloaded", timeout=60000)
        for _ in range(30):
            if token:
                break
            self.page.wait_for_timeout(1000)
        self.page.remove_listener("request", grab)
        if not token:
            raise NotSignedIn("Couldn't open the Parts Purchased report (sign-in expired?)")
        last = end - timedelta(milliseconds=1)
        rows, page_no = [], 0
        while True:
            r = self.page.request.get(
                f"{BASE}/api/shop/{SHOPS[shop]}/reports/parts-purchased/results",
                params={"start": start.isoformat(timespec="milliseconds"), "end": last.isoformat(timespec="milliseconds"),
                        "shopId": str(SHOPS[shop]), "timeZone": "America/New_York", "size": "1000", "page": str(page_no)},
                headers={"x-auth-token": token["h"], "accept": "application/json"})
            if r.status != 200:
                raise RuntimeError(f"Parts Purchased report: HTTP {r.status}")
            data = r.json()
            rows += data["content"]
            if data.get("last", True):
                return rows
            page_no += 1

    def ro_parts(self, shop, ro_id):
        return self.ro_items(shop, ro_id)[0]

    def ro_items(self, shop, ro_id):
        """(parts, sublets) on an RO, as loaded by the RO page itself
        (/api/repair-order/<id>/estimate). Parts carry their purchase details; sublets
        only have vendor, cost and approval (Tekmetric keeps no order/bill date for them)."""
        for attempt in range(3):
            try:
                estimate = self._estimate(shop, ro_id)
                break
            except Exception:
                if attempt == 2:
                    raise
                self.page.wait_for_timeout(5000)
        parts = []
        for job in estimate.get("jobs") or []:
            for p in job.get("parts") or []:
                parts.append({
                    "job": job.get("name"),
                    "authorized": job.get("authorized"),
                    "name": p.get("name"),
                    "partNumber": p.get("partNumber"),
                    "vendor": p.get("vendorName"),
                    "invoice": p.get("invoiceNumber"),
                    # QUOTED = a PartsTech quote, never bought; orderDate is then the quote date
                    "status": ((p.get("orderPartOrderPartStatus") or p.get("orderStatus") or {}).get("code")),
                    "jobStatus": p.get("jobStatus"),
                    "orderDate": p.get("orderDate"),
                    "ordered": p.get("orderPartOrdered"),
                    "received": p.get("orderPartReceived"),
                    "returned": p.get("orderPartReturned"),
                    "cost": (p.get("orderPartCost") or 0) / 100,
                })
        sublets = []
        for s in estimate.get("sublets") or []:
            items = s.get("items") or []
            cost = sum(i.get("cost") or 0 for i in items) if items else (s.get("cost") or 0)
            vendor = s.get("vendor")
            sublets.append({
                "id": s.get("id"),
                "name": s.get("name"),
                "vendor": vendor.get("name") if isinstance(vendor, dict) else vendor,
                "authorized": s.get("authorized"),
                "authorizedDate": s.get("authorizedDate"),
                "status": s.get("status"),
                "complete": all(i.get("complete") for i in items) if items else None,
                "cost": cost / 100,
            })
        return parts, sublets


if __name__ == "__main__" and sys.argv[1:] == ["signin"]:
    with TekmetricWeb(visible=True) as tw:
        print("A Chrome window is open: sign in to Tekmetric there.", flush=True)
        tw.wait_for_manual_sign_in()
        print("Signed in. Session saved; the window closes by itself.", flush=True)
