# Parts Accrual Process: notes for Claude

Read `README.md` first. It has the process, the rules, and where every file and key lives.

## Working with Julio
- **Go slow.** Do one step, show the result, and wait for his go-ahead. He stops runs that move too fast.
- He wants to stay involved in the decisions. Franyesca ("Franyeska" in Slack, freyes@) is the bookkeeper who books the JEs in QBO and answers the one-off purchase questions.
- Speak in accounting and shop terms, not code.

## Applying answers from #parts-accrual
When Julio pastes answers to the monthly questions, edit that month's tab with the Google Sheets connector. When adding a confirmed one-off part to an RO, append it to the amount formula (`=296.98+370`). After deleting or inserting rows, rewrite the Balance formulas (`=E<prev>+D<row>`). Never send an `updateCells` with empty `{}` cells: that clears them. When that shop's chart is final, post the monday hand-off: `.venv/bin/python monday.py --shop <shop>` (once per shop per month; check the item's updates first so it isn't posted twice).

## Hard rules
- Never write questions to a tab before someone answers them.
- Never print or move secrets: `~/.config/tunerite-accrual/*`, `~/.config/tunerite-recon/google-sa.json`.
- The Tekmetric web scraper only reads; never click or save anything in Tekmetric.
- Business data (`runs/`, `snapshots/`, `scrape_debug/`) stays out of git.
