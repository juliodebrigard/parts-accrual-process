"""Tekmetric sign-in keep-alive (launchd com.tunerite.parts-accrual-keepalive, every 6 hours).

Opens Tekmetric once in the background so the saved session stays in use, and logs whether it
was still signed in (logs/session.log), which also tells us how long Tekmetric keeps a session.
The first time it finds the session expired, it asks Julio in #parts-accrual to sign in again;
it doesn't repeat that until the session has worked again.
"""
from datetime import datetime

from config import PROJECT, SLACK_JULIO, TZ
from notify import slack
from tekmetric_web import NotSignedIn, TekmetricWeb

LOG = PROJECT / "logs/session.log"
EXPIRED_FLAG = PROJECT / "logs/.session-expired"
SIGNIN_CMD = "cd ~/'Parts Accrual Process' && .venv/bin/python tekmetric_web.py signin"


def main():
    LOG.parent.mkdir(exist_ok=True)
    try:
        with TekmetricWeb() as tw:
            tw.check_signed_in()
        ok = True
    except NotSignedIn:
        ok = False
    with LOG.open("a") as f:
        f.write(f"{datetime.now(TZ):%Y-%m-%d %H:%M} {'signed-in' if ok else 'EXPIRED'}\n")
    if ok:
        EXPIRED_FLAG.unlink(missing_ok=True)
    elif not EXPIRED_FLAG.exists():
        EXPIRED_FLAG.touch()
        slack(f"<@{SLACK_JULIO}> The Parts AJE tool's Tekmetric sign-in expired. When you're at the Mac mini, run:\n"
              f"`{SIGNIN_CMD}`\nand sign in in the Chrome window that opens. (Needed before the 5th.)")


if __name__ == "__main__":
    main()
