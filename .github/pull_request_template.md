## Summary

<!-- What changes for the squad, and why. Screenshots help for anything visible on the site. -->

## Test plan

- [ ] `python tests/selftest.py` passes (CI runs it on Python 3.10 and 3.12)
- [ ] Checked in demo mode (`python server.py --demo`) in the browser, with no console errors
- [ ] New checks added to `tests/selftest.py` for new logic
- [ ] `docs/DATA.md` updated if a table, column, API field or `/api/*` endpoint changed
- [ ] README updated for user-facing changes, and new `config.json` keys added to its configuration table
- [ ] `CLAUDE.md` updated if the architecture changed, then `AGENTS.md` rebuilt:
      `{ echo "# AGENTS.md"; tail -n +2 CLAUDE.md; } > AGENTS.md`
- [ ] Works against an existing `data/tracker.db` (new columns need migration code; new tables are fine)
