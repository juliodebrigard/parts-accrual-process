"""Monthly Parts AJE for one shop: builds the closing month's tab from last month's.

    .venv/bin/python accrual.py --shop boca --month 2026-09            # dry run: print the plan only
    .venv/bin/python accrual.py --shop boca --month 2026-09 --apply    # write the new tab + report

Steps (see README.md for the rules behind each one):
  1. Duplicate last month's tab, rename it and its title to the closing month.
  2. Drop ROs posted in the closing month (or earlier) and rows noted "remove when starting".
  3. For ROs that stay, add parts bought in the closing month.
  4. Add ROs posted between the 1st of the new month and today that had parts bought in the closing month.
  5. Add open ROs (WIP screen) that had parts bought in the closing month.
Anything that needs a person (one-off vendors, ROs that vanished, ROs with no parts left) is
NOT written to the tab; it goes to the report's questions instead.
"""
import argparse
import json
from datetime import datetime

import tekmetric_db
from config import MIN_RO_AMOUNT, RUNS_DIR, SHOPS, SNAPSHOT_DIR, TZ, WIP_STATUSES
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


def month_parts(tw, shop, ro, start, end, dater=None):
    """Parts bought (and sublets added) in [start, end) on one RO, split into what the tool
    adds on its own (account vendors, in-house, sublets) and what needs a person
    (one-off vendors, no vendor)."""
    parts, sublets = tw.ro_items(shop, ro["id"])
    auto, ask, live = [], [], 0
    for s in sublets:
        if not s["authorized"] or str(s["status"]).lower() == "declined" or not s["cost"]:
            continue
        if any(w in f"{s['vendor']} {s['name']}".lower() for w in RIDE_SUBLETS):
            continue   # customer rides aren't parts cost (Julio, 2026-10-10)
        live += s["cost"]
        if dater and dater[1](s, end):
            approved = tekmetric_db.local(s["authorizedDate"]).strftime("%m/%d/%y") if s["authorizedDate"] else "-"
            auto.append({"part": s["name"] or "(sublet)", "vendor": (s["vendor"] or "").strip(), "invoice": None,
                         "ordered": approved, "status": "SUBLET", "amount": round(s["cost"], 2),
                         "type": "sublet", "dated_by": dater[0]})
    for p in parts:
        if p["status"] == "QUOTED" or not p["orderDate"]:
            continue
        qty = (p["ordered"] or 0) - (p["returned"] or 0)
        amount = round(p["cost"] * qty, 2)
        live += amount
        od = tekmetric_db.local(p["orderDate"])
        if not (start <= od < end) or amount == 0:
            continue
        item = {"part": p["name"] or "(no name)", "vendor": (p["vendor"] or "").strip(),
                "invoice": p["invoice"], "ordered": od.strftime("%m/%d/%y"), "status": p["status"],
                "amount": amount, "type": vendor_type(shop, p["vendor"])}
        (auto if item["type"] in ("account", "in-house") else ask).append(item)
    return auto, ask, round(live, 2)


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
    next_name = MONTHS[end.month - 1]

    dater = sublet_dater(shop_key, start, prev_start)
    result["sublets_dated_by"] = dater[0]
    with TekmetricWeb() as tw:
        tw.check_signed_in()
        on_list = set()
        # steps 2 + 3: last month's ROs
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
            if ro["posted"] and ro["posted"] < end:
                result["removed"].append({"ro": row["ro"], "desc": row["desc"],
                                          "why": f"posted {ro['posted'].strftime('%m/%d/%y')}"})
                continue
            auto, ask, live = month_parts(tw, shop_key, ro, start, end, dater)
            already = amount_terms(row["amount"])
            auto = [a for a in auto if round(a["amount"], 2) not in already]   # typed in by hand already
            log(f"  kept {row['ro']}: +{sum(a['amount'] for a in auto):.2f}, {len(ask)} to ask")
            amount = row["amount"]
            add = round(sum(a["amount"] for a in auto), 2)
            if add:
                amount = (amount if isinstance(amount, str) and amount.startswith("=") else f"={amount}") + f"+{money(add)}"
                result["updated"].append({"ro": row["ro"], "desc": row["desc"], "added": add, "parts": auto})
            for a in ask:
                if round(a["amount"], 2) in already:   # typed into the amount by hand already
                    continue
                q.append({"ro": row["ro"], "desc": row["desc"], "ask": "One-off purchase: was it paid in "
                          f"{MONTHS[month - 1]}? If yes, add it to this RO.", **a})
            if live == 0:
                q.append({"ro": row["ro"], "desc": row["desc"],
                          "ask": f"No parts on this RO in Tekmetric anymore (status {ro['status']}). Remove it?"})
            result["rows"].append(dict(row, amount=amount, note=""))

        # steps 4 + 5: ROs posted since the 1st, then open ROs
        candidates = []
        for ro in by_number.values():
            if ro["number"] in on_list or not ro["created"] or ro["created"] >= end:
                continue
            if ro["posted"] and end <= ro["posted"] <= now:
                candidates.append((ro, f"Posted {ro['posted'].strftime('%m/%d/%y')}: remove when starting {next_name}"))
            elif not ro["posted"] and ro["status"] in WIP_STATUSES:
                candidates.append((ro, ""))
        log(f"  checking {len(candidates)} posted/open ROs")
        for ro, note in sorted(candidates, key=lambda x: x[0]["number"]):
            auto, ask, _ = month_parts(tw, shop_key, ro, start, end, dater)
            add = round(sum(a["amount"] for a in auto), 2)
            if add + sum(a["amount"] for a in ask) < MIN_RO_AMOUNT:
                continue   # under $10 in total: not worth accruing or asking about
            for a in ask:
                q.append({"ro": ro["number"], "desc": ro["customer"], "ask": "One-off purchase: was it paid in "
                          f"{MONTHS[month - 1]}? If yes, add this RO to the tab.", **a})
            if add >= MIN_RO_AMOUNT:
                result["rows"].append({"ro": ro["number"], "desc": ro["customer"], "amount": add, "note": note})
                result["added"].append({"ro": ro["number"], "desc": ro["customer"], "added": add,
                                        "posted": bool(note), "parts": auto})
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
