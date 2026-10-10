"""Scheduled monthly run (launchd com.tunerite.parts-accrual-monthly, every morning at 8:00).

Does nothing outside the 5th-10th of the month. On those days it runs accrual.py for every
shop not done yet for last month, writes the new tab, and posts the report to #parts-accrual.
If Tekmetric needs a fresh sign-in, it asks Julio in Slack and tries again the next morning.
"""
import sys
import traceback
from datetime import datetime

import accrual
import tekmetric_db
from config import RUNS_DIR, SHOPS, SLACK_FRANYESKA, SLACK_JULIO, TZ
from notify import slack
from tekmetric_web import NotSignedIn

SIGNIN_CMD = "cd ~/'Parts Accrual Process' && .venv/bin/python tekmetric_web.py signin"
FIRST_DAY, LAST_DAY = 5, 10


def fmt_money(x):
    return f"${x:,.2f}"


def report_text(r):
    shop = SHOPS[r["shop"]]["name"]
    lines = [f"*Parts AJE {r['new_tab']} · {shop}*: total *{r.get('total_shown') or '?'}*  (<{r['sheet_url']}|open the tab>)"]
    if r["removed"]:
        lines.append(f"Removed ({len(r['removed'])}): " + ", ".join(f"{x['ro']} {x['desc'].strip()} ({x['why']})" for x in r["removed"]))
    for x in r["updated"]:
        lines.append(f"Updated {x['ro']} {x['desc'].strip()}: +{fmt_money(x['added'])} (" +
                     ", ".join(f"{p['vendor']} {p['ordered']}" for p in x["parts"]) + ")")
    for x in r["added"]:
        lines.append(f"Added {x['ro']} {x['desc'].strip()}: {fmt_money(x['added'])}" + (" (already posted, flagged to drop next month)" if x["posted"] else ""))
    subs = [(x["ro"], x["desc"], p) for x in r["updated"] + r["added"] for p in x["parts"] if p["type"] == "sublet"]
    if subs:
        lines.append(f"\n*Sublets added* (dated by {r.get('sublets_dated_by')}; Tekmetric has no bill date for sublets). "
                     "Franyeska, please check these against QBO and say if any was paid in a different month:")
        for ro, desc, p in subs:
            lines.append(f"• RO {ro} {str(desc).strip()}: {p['part']} · {p['vendor']} · approved {p['ordered']} · {fmt_money(p['amount'])}")
    if r["questions"]:
        lines.append(f"\n*Need your input* <@{SLACK_JULIO}> <@{SLACK_FRANYESKA}> (not added to the tab yet):")
        for i, x in enumerate(r["questions"], 1):
            part = f" {x['part']} · {x['vendor']} · inv {x.get('invoice') or '-'} · ordered {x['ordered']} · {fmt_money(x['amount'])}" if "part" in x else ""
            lines.append(f"{i}. RO {x['ro']} {str(x['desc']).strip()}:" + (f"{part}." if part else "") + f" {x['ask']}")
        lines.append("Reply in this thread. Julio applies the answers with Claude in the Parts Accrual Process folder.")
    else:
        lines.append("No questions this month.")
    lines.append("Next: book the month-end JE (last day of the month) and the reversing JE (1st of the next month) in QBO for this total.")
    return "\n".join(lines)


def once_today(tag):
    """True the first time a given alert is raised today (avoids repeat pings)."""
    RUNS_DIR.mkdir(exist_ok=True)
    marker = RUNS_DIR / f".alert-{tag}-{datetime.now(TZ):%Y-%m-%d}"
    if marker.exists():
        return False
    marker.touch()
    return True


def main(force=False):
    today = datetime.now(TZ)
    if not force and not (FIRST_DAY <= today.day <= LAST_DAY):
        return
    y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    month = f"{y}-{m:02d}"
    todo = [s for s in SHOPS if not (RUNS_DIR / f"{month}-{s}.json").exists()]
    if not todo:
        return
    tekmetric_db.refresh()
    for shop in todo:
        try:
            sheet, layout, result = accrual.plan(shop, y, m)
            accrual.apply(sheet, layout, result)
            accrual.save(result)
            slack(report_text(result))
        except NotSignedIn:
            if once_today("signin"):
                slack(f"<@{SLACK_JULIO}> The Parts AJE run for {month} is waiting on Tekmetric: its sign-in expired. "
                      f"On the Mac mini, open Terminal and run:\n`{SIGNIN_CMD}`\nsign in in the Chrome window that opens. "
                      f"I'll try again tomorrow at 8:00 (through the {LAST_DAY}th).")
            return
        except Exception as e:
            traceback.print_exc()
            if once_today(f"error-{shop}"):
                slack(f"<@{SLACK_JULIO}> The Parts AJE run for {SHOPS[shop]['name']} ({month}) stopped: {type(e).__name__}: "
                      f"{str(e)[:300]}\nNothing more was written for this shop. I'll retry tomorrow at 8:00.")
    if today.day == LAST_DAY:
        left = [SHOPS[s]["name"] for s in SHOPS if not (RUNS_DIR / f"{month}-{s}.json").exists()]
        if left:
            slack(f"<@{SLACK_JULIO}> Last automatic try for the {month} Parts AJE: still not done for {', '.join(left)}. "
                  "Please run it with Claude in the Parts Accrual Process folder.")


if __name__ == "__main__":
    main(force="--force" in sys.argv)
