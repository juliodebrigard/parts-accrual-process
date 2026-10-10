"""Post to the #parts-accrual Slack channel (Julio + Franyeska)."""
import requests

from config import SLACK_WEBHOOK_FILE


def slack(text):
    r = requests.post(SLACK_WEBHOOK_FILE.read_text().strip(), json={"text": text}, timeout=30)
    r.raise_for_status()
    return r.text
