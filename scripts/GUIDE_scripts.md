# Scripts

The files in `scripts/` are small wrappers for schedulers and operators who
need a file path. The reusable logic stays in
`src/hyperliquid_candles/`; these scripts only call it.

Use the installed commands when possible:

```bash
uv run hyperliquid-candles-run-once
uv run hyperliquid-candles-quality
```

Use a file path when an external scheduler requires one.

## Files

- `run_once.py` runs exactly one ingestion cycle. It calls
  `hyperliquid_candles.scripts_run_once.main` and is suitable for cron or
  `systemd` timers.
- `run_quality_report.py` prints a plain-text ClickHouse quality report. Its
  freshness section covers active symbols.
- `update.sh` updates a Docker deployment. It finds the project directory from
  its own location, pulls the committed code, then stops, rebuilds, and
  restarts the containers. It stops at the first failed command, so a failed
  pull cannot quietly redeploy the old code. It is a shell script because it
  only coordinates `git` and `docker` commands.

## Notes

- 2026-06-21: Kept these files as wrappers so reusable behavior remains in the
  package.
- 2026-08-26: Added `update.sh` to replace three manual deployment commands
  with one.
