"""Monthly Parts AJE for one shop: builds the closing month's tab from last month's.

    .venv/bin/python accrual.py --shop boca --month 2026-09            # dry run: print the plan only
    .venv/bin/python accrual.py --shop boca --month 2026-09 --apply    # write the new tab + report

Steps (see README.md for the rules behind each one):
  1. Duplicate last month's tab, rename it and its title to the closing month.
  2. Drop ROs posted in the closing month (or earlier) and rows noted "remove when starting".
  3. For ROs that stay, add parts bought and sublets added in the closing month (returns are ignored).
  4. Add ROs posted between the 1st of the new month and today that had such costs in the closing month.
  5. Add ROs still open (WIP, Estimate, Complete, Saved for Later) that had such costs in the closing month.
Parts and returns come from Tekmetric's Parts Purchased report (one pull per shop per month);
RO statuses, posted dates and sublets from the local API sync.
Anything that needs a person (one-off vendors, ROs that vanished, ROs with no parts left) is
NOT written to the tab; it goes to the report's questions instead.
"""
import argparse
import json
from datetime import datetime

import tekmetric_db
from config import MIN_RO_AMOUNT, RUNS_DIR, SHOPS, SNAPSHOT_DIR, TZ
from sheets import Sheet
from tekmetric_web import TekmetricWeb
from vendors import vendor_type

RIDE_SUBLETS = ("lyft", "uber")   # sublets that are customer rides, never accrued

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


def month_bounds(year, month):
    start = datetime(year, month, 1, tzinfo=TZ)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=TZ)
    return start, end


def tab_name(dt):
    return f"{MONTHS[dt.month - 1]} {dt.year}"


def col_letter(i):
    return chr(ord("A") + i)


def money(x):
    return f"{x:.2f}".rstrip("0").rstrip(".")


def amount_terms(v):
    """The numbers added up in an amount cell, e.g. '=35+87.8+96.14' -> {35.0, 87.8, 96.14}."""
    if isinstance(v, (int, float)):
        return {round(float(v), 2)}
    terms = set()
    for t in str(v or "").lstrip("=").split("+"):
        try:
            terms.add(round(float(t), 2))
        except ValueError:
            pass
    return terms


def is_ro_number(v):
    return isinstance(v, (int, float)) or (isinstance(v, str) and v.strip().isdigit())


# ---------- reading last month's tab ----------

def parse_tab(grid):
    """Find the title cell, the header row and the RO rows of an AJE tab."""
    header = None
    for r, row in enumerate(grid):
        for c, cell in enumerate(row):
            if isinstance(cell["v"], str) and cell["v"].strip().lower() == "ro number":
                header = (r, c)
                break
        if header:
            break
    if not header:
        raise RuntimeError("couldn't find the 'RO Number' header")
    hr, cr = header
    title = None
    for r in range(hr):
        for c, cell in enumerate(grid[r]):
            if isinstance(cell["v"], str) and "parts aje" in cell["v"].lower():
                title = (r, c, cell["v"])
    cell = lambda r, c: grid[r][c]["v"] if r < len(grid) and c < len(grid[r]) else None
    rows, r = [], hr + 1
    while is_ro_number(cell(r, cr)):
        rows.append({"ro": int(float(cell(r, cr))), "desc": cell(r, cr + 1) or "",
                     "amount": cell(r, cr + 2), "note": cell(r, cr + 4) or ""})
        r += 1
    trailing = 0
    while isinstance(cell(r + trailing, cr + 3), str) and str(cell(r + trailing, cr + 3)).startswith("="):
        trailing += 1
    return {"header_row": hr, "ro_col": cr, "title": title, "rows": rows, "trailing": trailing}


# ---------- Tekmetric ----------

def sublet_ids(month_start, shop):
    """Sublet ids on open ROs in the month-end snapshot taken at the end of the month
    starting at month_start, or None if that snapshot doesn't exist."""
    path = SNAPSHOT_DIR / month_start.strftime("%Y-%m") / f"{shop}.json"
    if not path.exists():
        return None
    ros = json.loads(path.read_text())["ros"]
    return {s["id"] for r in ros for s in r.get("sublets") or [] if s.get("id")}


def sublet_dater(shop, start, prev_start):
    """How to tell if a sublet belongs to the closing month. Tekmetric has no order or bill
    date for sublets, so: on the closing month's snapshot but not on the one before = added
    that month. Without both snapshots, fall back on the approval date (a human action)."""
    now_ids, before_ids = sublet_ids(start, shop), sublet_ids(prev_start, shop)
    if now_ids is not None and before_ids is not None:
        return "snapshot", lambda s, end: s["id"] in now_ids and s["id"] not in before_ids
    return "approval date", lambda s, end: bool(s["authorizedDate"]) and start <= tekmetric_db.local(s["authorizedDate"]) < end


PURCHASE_TYPES = {"RO_ORDER", "INV_ORDER"}   # bought for an RO / pulled from stock to an RO
# Returns are ignored on purpose: marking a part "returned" in Tekmetric is a human action and
# doesn't mean the credit came back. Credits wash out in QBO when the money is received (Julio, 2026-10-10).


def month_activity(report, shop, by_number):
    """Group the Parts Purchased report's purchases by RO number (falling back on the PO #,
    which the shops set to the RO number). Returns are skipped; see PURCHASE_TYPES."""
    out, skipped = {}, []
    for x in report:
        if x["orderType"] not in PURCHASE_TYPES:
            continue
        n = x.get("repairOrderNumber")
        if n is None and str(x.get("orderNumber") or "").strip().isdigit():
            n = int(str(x["orderNumber"]).strip())
        if n not in by_number:
            skipped.append(x)          # stock orders, unknown types, RO not found
            continue
        when = tekmetric_db.local(x["processedDate"])
        out.setdefault(n, []).append({
            "part": x.get("name") or x.get("title") or "(no name)", "vendor": (x.get("vendorName") or "").strip(),
            "invoice": x.get("operationNumber"), "ordered": when.strftime("%m/%d/%y"),
            "status": "PURCHASED",
            "amount": round((x.get("totalCost") or 0) / 100, 2),   # cost without core charges (Julio, 2026-10-10)
            "type": vendor_type(shop, x.get("vendorName"))})
    return out, skipped


def ro_sublets(ro, dater, end):
    """Sublets added in the closing month, from the API sync (Tekmetric keeps no bill date)."""
    out = []
    for s in ro["payload"].get("sublets") or []:
        items = s.get("items") or []
        cost = (sum(i.get("cost") or 0 for i in items) if items else (s.get("cost") or 0)) / 100
        vendor = s.get("vendor")
        vendor = (vendor.get("name") if isinstance(vendor, dict) else vendor) or ""
        if not s.get("authorized") or not cost:
            continue
        if any(w in f"{vendor} {s.get('name')}".lower() for w in RIDE_SUBLETS):
            continue   # customer rides aren't parts cost (Julio, 2026-10-10)
        if dater[1]({"id": s.get("id"), "authorizedDate": s.get("authorizedDate")}, end):
            approved = tekmetric_db.local(s["authorizedDate"]).strftime("%m/%d/%y") if s.get("authorizedDate") else "-"
            out.append({"part": s.get("name") or "(sublet)", "vendor": vendor.strip(), "invoice": None,
                        "ordered": approved, "status": "SUBLET", "amount": round(cost, 2),
                        "type": "sublet", "dated_by": dater[0]})
    return out


def ro_live_cost(ro):
    """Parts + sublet cost currently on the RO (API sync), to spot ROs that have nothing left."""
    total = 0
    for j in ro["payload"].get("jobs") or []:
        for p in j.get("parts") or []:
            total += (p.get("cost") or 0) * (p.get("quantity") or 0)
    for s in ro["payload"].get("sublets") or []:
        total += sum(i.get("cost") or 0 for i in s.get("items") or []) or (s.get("cost") or 0)
    return round(total / 100, 2)


def split(items):
    """What the tool adds on its own (account vendors, in-house, sublets) vs what a person
    confirms (one-off vendors, no vendor)."""
    auto = [i for i in items if i["type"] in ("account", "in-house", "sublet")]
    ask = [i for i in items if i["type"] not in ("account", "in-house", "sublet")]
    return auto, ask


def plan(shop_key, year, month, log=print, allow_existing=False):
    """Work out the new tab without writing anything."""
    shop = SHOPS[shop_key]
    start, end = month_bounds(year, month)
    prev_start = month_bounds(year - (month == 1), (month - 2) % 12 + 1)[0]
    new_tab, prev_tab = tab_name(start), tab_name(prev_start)
    sheet_id = shop["sheets"].get(year)
    if not sheet_id or prev_start.year != year:
        raise RuntimeError(f"No AJE file set up for {year} (or last month is in last year's file). Add it in config.py.")
    sheet = Sheet(sheet_id)
    tabs = sheet.tabs()
    if new_tab in tabs and not allow_existing:
        raise RuntimeError(f"Tab '{new_tab}' already exists in the {shop['name']} file; not touching it.")
    if prev_tab not in tabs:
        raise RuntimeError(f"Last month's tab '{prev_tab}' isn't in the {shop['name']} file.")
    layout = parse_tab(sheet.grid(prev_tab))

    now = datetime.now(TZ)
    by_number = {r["number"]: r for r in tekmetric_db.ros(shop["shop_id"])}
    result = {"shop": shop_key, "month": start.strftime("%Y-%m"), "new_tab": new_tab, "prev_tab": prev_tab,
              "rows": [], "removed": [], "updated": [], "added": [], "questions": [], "run_at": now.isoformat()}
    q = result["questions"]
    month_name, next_name = MONTHS[month - 1], MONTHS[end.month - 1]
    dater = sublet_dater(shop_key, start, prev_start)
    result["sublets_dated_by"] = dater[0]

    with TekmetricWeb() as tw:
        tw.check_signed_in()
        report = tw.parts_purchased(shop_key, start, end)
    activity, skipped = month_activity(report, shop_key, by_number)
    result["report_lines"], result["report_skipped"] = len(report), len(skipped)
    log(f"  Parts Purchased {month_name}: {len(report)} lines, {len(activity)} ROs, {len(skipped)} not tied to an RO")

    def open_at_month_end(ro):
        return ro["status"] != "Deleted" and not (ro["posted"] and ro["posted"] < end)

    def ask_items(ro_number, desc, ask, where):
        for a in ask:
            text = f"One-off purchase: was it paid in {month_name}? If yes, {where}."
            q.append({"ro": ro_number, "desc": desc, "ask": text, **a})

    # steps 2 + 3: last month's ROs
    on_list = set()
    for row in layout["rows"]:
        on_list.add(row["ro"])
        ro = by_number.get(row["ro"])
        if "remove when starting" in str(row["note"]).lower():
            result["removed"].append({"ro": row["ro"], "desc": row["desc"], "why": row["note"]})
            continue
        if not ro:
            q.append({"ro": row["ro"], "desc": row["desc"], "ask": "RO not found in Tekmetric. Kept as is; remove it?"})
            result["rows"].append(dict(row, note=""))
            continue
        if not open_at_month_end(ro):
            result["removed"].append({"ro": row["ro"], "desc": row["desc"],
                                      "why": f"posted {ro['posted'].strftime('%m/%d/%y')}"})
            continue
        auto, ask = split(activity.get(row["ro"], []) + ro_sublets(ro, dater, end))
        already = amount_terms(row["amount"])   # typed into the amount by hand already
        auto = [a for a in auto if not (a["amount"] > 0 and round(a["amount"], 2) in already)]
        ask = [a for a in ask if not (a["amount"] > 0 and round(a["amount"], 2) in already)]
        add = round(sum(a["amount"] for a in auto), 2)
        amount = row["amount"]
        if add:
            base = amount if isinstance(amount, str) and amount.startswith("=") else f"={amount}"
            amount = base + (f"+{money(add)}" if add > 0 else f"-{money(-add)}")
            result["updated"].append({"ro": row["ro"], "desc": row["desc"], "added": add, "parts": auto})
        ask_items(row["ro"], row["desc"], ask, "add it to this RO")
        if ro_live_cost(ro) == 0:
            q.append({"ro": row["ro"], "desc": row["desc"],
                      "ask": f"No parts on this RO in Tekmetric anymore (status {ro['status']}). Remove it?"})
        result["rows"].append(dict(row, amount=amount, note=""))

    # steps 4 + 5: other ROs open at month end with purchases or sublets in the closing month
    candidates = set(activity)
    candidates |= {n for n, ro in by_number.items() if ro["created"] and ro["created"] < end and ro_sublets(ro, dater, end)}
    for n in sorted(candidates - on_list):
        ro = by_number[n]
        if not open_at_month_end(ro) or (ro["posted"] and ro["posted"] > now):
            continue
        auto, ask = split(activity.get(n, []) + ro_sublets(ro, dater, end))
        add = round(sum(a["amount"] for a in auto), 2)
        if add + sum(a["amount"] for a in ask) < MIN_RO_AMOUNT:
            continue   # under $10 in total: not worth accruing or asking about
        note = f"Posted {ro['posted'].strftime('%m/%d/%y')}: remove when starting {next_name}" if ro["posted"] else ""
        ask_items(n, ro["customer"], ask, "add this RO to the tab")
        if add >= MIN_RO_AMOUNT:
            result["rows"].append({"ro": n, "desc": ro["customer"], "amount": add, "note": note})
            result["added"].append({"ro": n, "desc": ro["customer"], "added": add, "posted": bool(note),
                                    "status": ro["status"], "parts": auto})
    # posted ROs go at the bottom, with their note
    result["rows"].sort(key=lambda r: bool(r["note"]))
    return sheet, layout, result


# ---------- writing the new tab ----------

def apply(sheet, layout, result):
    tabs = sheet.tabs()
    prev_id = tabs[result["prev_tab"]]
    order = list(tabs)
    rep = sheet.batch([{"duplicateSheet": {"sourceSheetId": prev_id, "newSheetName": result["new_tab"],
                                           "insertSheetIndex": order.index(result["prev_tab"]) + 1}}])
    sid = rep["replies"][0]["duplicateSheet"]["properties"]["sheetId"]

    hr, cr = layout["header_row"], layout["ro_col"]
    r0, old_n, rows = hr + 1, len(layout["rows"]), result["rows"]
    new_n = max(len(rows), 1)
    amt, bal = col_letter(cr + 2), col_letter(cr + 3)
    reqs = []
    if layout["title"]:
        tr, tc, text = layout["title"]
        prev_m, new_m = result["prev_tab"].split()[0], result["new_tab"].split()[0]
        reqs.append({"updateCells": {"start": {"sheetId": sid, "rowIndex": tr, "columnIndex": tc},
                                     "rows": [{"values": [{"userEnteredValue": {"stringValue": text.replace(prev_m, new_m)}}]}],
                                     "fields": "userEnteredValue"}})
    if new_n > old_n:
        reqs.append({"insertDimension": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": r0 + old_n,
                                                   "endIndex": r0 + new_n}, "inheritFromBefore": True}})
    elif new_n < old_n:
        reqs.append({"deleteDimension": {"range": {"sheetId": sid, "dimension": "ROWS", "startIndex": r0 + new_n,
                                                   "endIndex": r0 + old_n}}})
    data = []
    for i, row in enumerate(rows):
        n = r0 + i + 1   # 1-based sheet row
        a = row["amount"]
        a_val = {"formulaValue": a} if isinstance(a, str) and a.startswith("=") else {"numberValue": float(a)}
        b = f"={amt}{n}" if i == 0 else f"={bal}{n - 1}+{amt}{n}"
        note = {"userEnteredValue": {"stringValue": row["note"]}} if row["note"] else {}
        data.append({"values": [{"userEnteredValue": {"numberValue": row["ro"]}},
                                {"userEnteredValue": {"stringValue": str(row["desc"])}},
                                {"userEnteredValue": a_val},
                                {"userEnteredValue": {"formulaValue": b}}, note]})
    if data:
        reqs.append({"updateCells": {"start": {"sheetId": sid, "rowIndex": r0, "columnIndex": cr},
                                     "rows": data, "fields": "userEnteredValue"}})
    for t in range(layout["trailing"]):
        n = r0 + new_n + t + 1
        reqs.append({"updateCells": {"start": {"sheetId": sid, "rowIndex": n - 1, "columnIndex": cr + 3},
                                     "rows": [{"values": [{"userEnteredValue": {"formulaValue": f"={bal}{n - 1}+{amt}{n}"}}]}],
                                     "fields": "userEnteredValue"}})
    sheet.batch(reqs)
    last = r0 + new_n + layout["trailing"]
    shown = sheet.values(result["new_tab"], f"{bal}{last}:{bal}{last}")
    result["total_shown"] = shown[0][0].strip() if shown and shown[0] else None
    result["sheet_url"] = f"https://docs.google.com/spreadsheets/d/{sheet.id}/edit#gid={sid}"
    return result


def save(result):
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"{result['month']}-{result['shop']}.json"
    path.write_text(json.dumps(result, indent=1, default=str))
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shop", required=True, choices=SHOPS)
    ap.add_argument("--month", required=True, help="closing month, YYYY-MM")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-existing", action="store_true", help="dry run only: plan even if the tab exists")
    ap.add_argument("--no-refresh", action="store_true", help="skip pulling the latest ROs first")
    args = ap.parse_args()
    y, m = map(int, args.month.split("-"))
    if not args.no_refresh:
        tekmetric_db.refresh()
    if args.apply and args.allow_existing:
        ap.error("--allow-existing is for dry runs only")
    sheet, layout, result = plan(args.shop, y, m, allow_existing=args.allow_existing)
    print(json.dumps({k: result[k] for k in ("removed", "updated", "added", "questions")}, indent=1, default=str))
    print("rows:", [(r["ro"], r["amount"]) for r in result["rows"]])
    if args.apply:
        apply(sheet, layout, result)
        print("written:", result["total_shown"], save(result))


if __name__ == "__main__":
    main()
