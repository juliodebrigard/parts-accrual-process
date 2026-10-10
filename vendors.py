"""Credit-account vendors (from the doc "Parts Purchases: How to Date Them in QBO").
Any vendor not listed for a shop is one-off. Names in Tekmetric aren't typed
consistently, so matching ignores case, spaces and punctuation and looks for a key
inside the vendor name."""
import re

ACCOUNT_VENDORS = {
    "both": ["advance", "autozone", "worldpac", "imc", "partsauthority", "napa",
             "1800radiator", "partshouse", "tph", "usautoforce", "mighty", "oreilly"],
    "davie": ["northside"],
    "boca": [],
}
IN_HOUSE = "inhouse"


def _norm(name):
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def vendor_type(shop, vendor_name):
    """'account', 'one-off', 'in-house', or None when the part has no vendor."""
    n = _norm(vendor_name)
    if not n:
        return None
    if n == IN_HOUSE:
        return "in-house"
    keys = ACCOUNT_VENDORS["both"] + ACCOUNT_VENDORS[shop]
    return "account" if any(k in n for k in keys) else "one-off"
