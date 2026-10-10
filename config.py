"""Shop settings and file locations for the monthly Parts AJE (parts accrual)."""
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/New_York")
PROJECT = Path(__file__).resolve().parent
RUNS_DIR = PROJECT / "runs"            # one report per shop per month
SNAPSHOT_DIR = PROJECT / "snapshots"   # month-end snapshots

SYNC_DIR = Path.home() / "tekmetric-api-integration"
SYNC_DB = SYNC_DIR / "data/tekmetric.sqlite"
GOOGLE_KEY = Path.home() / ".config/tunerite-recon/google-sa.json"   # recon-drive robot (Julio OK'd reuse 2026-10-10)
SLACK_WEBHOOK_FILE = Path.home() / ".config/tunerite-accrual/slack-webhook"

SLACK_JULIO = "U04E7920CC8"
SLACK_FRANYESKA = "U0949L7QN1M"

# One AJE spreadsheet per shop per year. Add next year's files here in January.
SHOPS = {
    "boca": {
        "name": "Boca",
        "shop_id": 2545,
        "sheets": {2026: "1QvFOJDqqabl67uH_0rg917P-DVkbfnnjf8P1EMaOZnA"},
    },
    "davie": {
        "name": "Davie",
        "shop_id": 3937,
        "sheets": {2026: "1QsBdymFQLXT_TN8Dqy3rd4-VplJA8feWPfDPayJj7gU"},
    },
}

# Tekmetric RO statuses that make up the WIP screen (open ROs).
WIP_STATUSES = {"Estimate", "Work-In-Progress", "Complete"}
MIN_RO_AMOUNT = 10.00   # ROs with less than this in new parts are left off (Julio, 2026-10-10)
