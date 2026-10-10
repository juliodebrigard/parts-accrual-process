"""Post to the #parts-accrual Slack channel (Julio + Franyeska)."""
import json
import urllib.request

from config import SLACK_WEBHOOK_FILE


def slack(text):
    url = SLACK_WEBHOOK_FILE.read_text().strip()
    req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(),
                                 headers={"Content-Type": "application/json"})
    import ssl
    ctx = ssl.create_default_context(cafile="/etc/ssl/cert.pem")   # this Python lacks its own CA bundle
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        return r.read().decode()
