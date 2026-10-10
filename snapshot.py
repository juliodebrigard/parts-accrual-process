"""Month-end snapshot (launchd com.tunerite.parts-accrual-snapshot, every night at 23:50).

Only acts on the last day of the month (or with --force). It saves what is on every open RO of
each shop at month end (from the Tekmetric API sync, so it works even if the web sign-in has
expired), then checks the Tekmetric web sign-in so Julio can renew it before the 5th.
"""
import json
import sys
from datetime import datetime, timedelta

import tekmetric_db
from config import SHOPS, SLACK_JULIO, SNAPSHOT_DIR, TZ, WIP_STATUSES
from notify import slack
from tekmetric_web import NotSignedIn, TekmetricWeb

SIGNIN_CMD = "cd ~/'Parts Accrual Process' && .venv/bin/python tekmetric_web.py signin"


def ro_summary(d):
    parts = []
    for j in d.get("jobs") or []:
        for p in j.get("parts") or []:
            parts.append({"job": j.get("name"), "jobAuthorized": j.get("authorized"), "name": p.get("name"),
                          "partNumber": p.get("partNumber"), "qty": p.get("quantity"),
                          "cost": (p.get("cost") or 0) / 100,
                          "status": (p.get("partStatus") or {}).get("name") if isinstance(p.get("partStatus"), dict) else p.get("partStatus")})
    sublets = [{"id": s.get("id"), "name": s.get("name"), "authorized": s.get("authorized"),
                "cost": sum(i.get("cost") or 0 for i in s.get("items") or []) / 100 or (s.get("cost") or 0) / 100}
               for s in d.get("sublets") or []]
    return parts, sublets


def main(force=False, no_slack=False):
    now = datetime.now(TZ)
    if not force and (now + timedelta(days=1)).day != 1:
        return
    tekmetric_db.refresh()
    folder = SNAPSHOT_DIR / (now.strftime("%Y-%m") + ("-test" if no_slack else ""))
    folder.mkdir(parents=True, exist_ok=True)
    counts = {}
    for key, shop in SHOPS.items():
        out = []
        for ro in tekmetric_db.ros(shop["shop_id"]):
            if ro["posted"] or ro["status"] not in WIP_STATUSES:
                continue
            parts, sublets = ro_summary(ro["payload"])
            out.append({"id": ro["id"], "number": ro["number"], "status": ro["status"], "customer": ro["customer"],
                        "parts": parts, "sublets": sublets})
        (folder / f"{key}.json").write_text(json.dumps({"taken_at": now.isoformat(), "ros": out}, indent=1))
        counts[shop["name"]] = len(out)
    try:
        with TekmetricWeb() as tw:
            tw.check_signed_in()
        signin = "Tekmetric sign-in is OK, so the run on the 5th is ready."
    except NotSignedIn:
        signin = (f"<@{SLACK_JULIO}> Tekmetric's sign-in has expired. Before the 5th, on the Mac mini run:\n"
                  f"`{SIGNIN_CMD}`\nand sign in in the Chrome window that opens.")
    if no_slack:
        print(counts, signin)
        return
    slack(f"Month-end snapshot saved for {now:%B %Y} (open ROs: " +
          ", ".join(f"{k} {v}" for k, v in counts.items()) + f"). {signin}")


if __name__ == "__main__":
    main(force="--force" in sys.argv, no_slack="--no-slack" in sys.argv)
