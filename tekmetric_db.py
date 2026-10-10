"""Read-only lookups in the local Tekmetric API sync (~/tekmetric-api-integration)."""
import json
import sqlite3
import subprocess
from datetime import datetime

from config import SYNC_DB, SYNC_DIR, TZ

SYNC_PYTHON = "/Library/Frameworks/Python.framework/Versions/3.13/bin/python3"


def local(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ) if ts else None


def refresh():
    """Pull the latest ROs into the local sync. Its own schedule only runs on
    weekdays 7:00-18:30, and our jobs run at night or on the 5th, so pull here."""
    subprocess.run([SYNC_PYTHON, "-m", "tekmetric.sync", "--mode", "incremental"],
                   cwd=SYNC_DIR, check=True, capture_output=True, timeout=900)


def ros(shop_id):
    """Every non-deleted RO of a shop: id, number, status, customer, posted, created."""
    c = sqlite3.connect(f"file:{SYNC_DB}?mode=ro", uri=True)
    names = dict(c.execute("select customer_id, name from customers where shop_id = ?", (shop_id,)))
    out = []
    for (payload,) in c.execute("select payload from raw_repair_orders where shop_id = ?", (shop_id,)):
        d = json.loads(payload)
        if d.get("deletedDate"):
            continue
        out.append({
            "id": d["id"], "number": d["repairOrderNumber"],
            "status": (d.get("repairOrderStatus") or {}).get("name"),
            "customer": (names.get(d.get("customerId")) or "").strip(),
            "posted": local(d.get("postedDate")),
            "created": local(d.get("createdDate")),
            "payload": d,
        })
    c.close()
    return out
