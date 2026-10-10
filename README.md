# Parts Accrual Process (monthly Parts AJE)

Every month, each shop's parts cost has to land in COGS in the month the RO is **posted**. Parts
bought for a car that is still in the shop at month-end are moved forward with a journal entry:
the month-end JE (last day of the month) and a reversing JE (1st of the next month) in QBO. This
folder works out that amount for **Boca** and **Davie** and writes it into the AJE sheets.

- Boca: [Parts AJE 2026 Tune RIte Boca](https://docs.google.com/spreadsheets/d/1QvFOJDqqabl67uH_0rg917P-DVkbfnnjf8P1EMaOZnA/edit)
- Davie: [Parts AJE Tune Rite Davie - 2026](https://docs.google.com/spreadsheets/d/1QsBdymFQLXT_TN8Dqy3rd4-VplJA8feWPfDPayJj7gU/edit)
- QBO entry steps: Drive doc "Parts accrual JE"
- Dating rule for Franyesca: Claude Doc [Parts Purchases: How to Date Them in QBO](https://claude.ai/code/artifact/72ae0f32-daf5-47c6-9df8-89c676f8d1fb)
- Notifications: Slack **#parts-accrual** (private: Julio + Franyeska)
- Hand-off to Franyesca: the update "Chart is ready for JE @Franyeska Reyes" on the **Enter parts adjustments JEs** item in the current month's group of each shop's monday.com accounting board (Tune Rite North board 9563055882, Davie board 9570355047)

## What runs on its own (Mac mini, launchd)

| Job | When | What it does |
|---|---|---|
| `com.tunerite.parts-accrual-snapshot` | every night 23:50, acts only on the last day of the month | Saves every open RO of both shops (`snapshots/YYYY-MM/`), checks the Tekmetric sign-in, posts to #parts-accrual (asks Julio to sign in if it expired) |
| `com.tunerite.parts-accrual-monthly` | every hour 8:00-18:00, acts only on the 5th-10th | Runs `accrual.py` for each shop not done yet for last month, writes the new tab, posts the report and questions to #parts-accrual. Retries hourly if something blocks it (Slack alerts at most once a day) |
| `com.tunerite.parts-accrual-keepalive` | every 6 hours | Opens Tekmetric in the background to keep the saved sign-in in use; logs the result to `logs/session.log`; tells Julio in Slack the first time it finds the sign-in expired |

Job files: `launchd/`, installed in `~/Library/LaunchAgents/`. Logs: `logs/`. Reports: `runs/YYYY-MM-<shop>.json` (a report there means that shop is done for that month).

## The monthly steps (what `accrual.py` does)

The closing month is the month that just ended (run on Oct 5 → closing month = September).

1. **New tab.** Duplicate last month's tab, rename it to the closing month ("September 2026") and change the month in the title cell.
2. **Drop posted ROs.** Remove every RO posted in the closing month or earlier, and every row whose note says "remove when starting …". An RO posted on or after the 1st of the new month stays (the process runs late, on the 5th).
3. **Update the ROs that stay.** Add the cost of parts bought on them during the closing month (appended to the amount formula, e.g. `=3309+40`). Parts from earlier months are already in the amount.
4. **ROs posted since the 1st.** For ROs posted between the 1st of the new month and today, add those with parts bought in the closing month. They get a note in the column after Balance, "Posted mm/dd/yy: remove when starting <next month>", so next month's step 2 drops them.
5. **Open ROs.** For ROs still open (Estimate, Work-In-Progress, Complete and **Saved for Later**), add those with parts bought in the closing month. No duplicates: ROs already on the tab are handled in step 3.

Balance formulas are rewritten for the whole table each time.

6. **monday.com hand-off.** If a shop has no open questions, the run posts "Chart is ready for JE" (tagging Franyeska) on monday right away. If there are questions, it waits: once the answers are applied, post it with `.venv/bin/python monday.py --shop <boca|davie>`.

## Rules (agreed with Julio, 2026-10-10)

- **The date that counts is the date QBO booked the cost.**
  - **Credit-account vendors** are booked by invoice date, so the Tekmetric **order date** is used. List in `vendors.py`: Advance, AutoZone, WorldPac, IMC-Parts Authority, NAPA, 1800 Radiator, The Parts House (TPH), US Autoforce, Mighty Auto Parts, O'Reilly, plus **Northside for Davie only**. Boca buys from Northside as a one-off; never mix the shops. Add new account vendors to `vendors.py` and the Claude Doc.
  - **Every other vendor is one-off**, booked by payment date. The tool never adds these on its own; they go to the questions, and Franyesca or Julio confirms whether each was paid in the closing month.
- **Sublets** count like account-vendor purchases (added automatically) but are listed in the Slack report for Franyesca to check, because Tekmetric has no bill date for them. Dated by the month-end snapshots (on this month's, not on last month's); until two snapshots exist, by approval date. **Lyft/Uber rides are left out.**
- **"In house" parts** (shop stock) count, dated by their order date.
- **Returns are ignored.** Marking a part "returned" in Tekmetric is a human action and doesn't mean the credit came back; credits wash out in QBO when the money is actually received.
- **Quotes don't count.** A part whose status is QUOTED (a PartsTech quote, often on a declined job) was never bought.
- **Don't rely on the job "approved" time.** Parts are sometimes ordered before the job is approved in Tekmetric. Only the part's order date and status matter.
- **Under $10:** an RO whose new parts total less than $10 is left off, and not asked about either.
- **Parts bought in an earlier month and never accrued** are ignored: they already hit COGS in that month.
- **Questions are never written to the tab.** Anything unclear goes to the report: one-off parts, an RO that has no parts anymore, an RO that can't be found. Julio applies the answers with Claude in this folder.

## Where the data comes from

- **RO list, statuses, posted dates:** the local Tekmetric API sync, `~/tekmetric-api-integration/data/tekmetric.sqlite` (refreshed at the start of each run).
- **Parts bought in the month:** Tekmetric's **Parts Purchased** report (Reports → Parts → Parts Purchased), one pull per shop per month: RO #, PO #, vendor, invoice #, date purchased, cost. The API doesn't have this. `tekmetric_web.py` opens the report in a hidden Chrome window and reads the data the page loads. It only reads and never clicks anything. (`ro_items()` can still read a single RO page for spot checks.)
- **Sublets:** from the API sync (vendor, cost, approval date), dated by the month-end snapshots.
  - Tekmetric's login has a captcha, so a person signs in by hand: run `.venv/bin/python tekmetric_web.py signin` on the Mac mini and sign in in the Chrome window that opens. The session is kept in `~/.config/tunerite-accrual/chrome-profile/`. No password is stored.
- **Sheets:** written as the `recon-drive@tunerite-hq.iam.gserviceaccount.com` robot. It's an Editor on both AJE files and reuses the Parts Recon key `~/.config/tunerite-recon/google-sa.json` (Julio OK'd, 2026-10-10).
- **Slack:** incoming webhook for #parts-accrual in `~/.config/tunerite-accrual/slack-webhook`.
- **monday.com:** Julio's personal API token (account tuneriteauto) in `~/.config/tunerite-accrual/monday-token`.

## Running it by hand

```bash
cd ~/'Parts Accrual Process'
.venv/bin/python accrual.py --shop davie --month 2026-09            # dry run: shows the plan, writes nothing
.venv/bin/python accrual.py --shop davie --month 2026-09 --apply    # writes the new tab + runs/ report
.venv/bin/python run_monthly.py --force                              # what the 8:00 job does, any day
.venv/bin/python snapshot.py --force --no-slack                      # test snapshot (saved as YYYY-MM-test)
.venv/bin/python tekmetric_web.py signin                             # renew the Tekmetric sign-in
.venv/bin/python monday.py --shop boca                               # post "Chart is ready for JE" on monday
```

`accrual.py` refuses to touch a month whose tab already exists, so it never overwrites work done by hand.

## Known limits

- **January:** last month's tab is in the previous year's file. Before February 5, set up the new year's AJE files and add their IDs to `config.py` (`sheets: {2027: ...}`). Until then the run stops and says so in Slack.
- **Tekmetric sign-in** can expire (Julio's own browser needs a new sign-in every morning, so maybe ~12 h idle). The keep-alive uses it every 6 hours; `logs/session.log` shows how long it really lasts. If it expires, the tool can't sign in alone (captcha): Julio runs the `signin` command once.
- **Sublet dates are approximate**: Tekmetric keeps no order or bill date for sublets, so they're always listed for review.
