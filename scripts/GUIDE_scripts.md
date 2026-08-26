# Scripts

The files in `scripts/` are small wrappers for schedulers that need a file
path. The reusable logic lives in `src/hyperliquid_candles/`; these scripts
only call into that package.

Use the installed commands when possible:

```bash
uv run hyperliquid-candles-run-once
uv run hyperliquid-candles-quality
```

Use a script path when an external scheduler requires one.

## Files

- `run_once.py` runs one ingestion cycle. It calls
  `hyperliquid_candles.scripts_run_once.main` and works with cron or
  `systemd` timers.
- `run_quality_report.py` prints a plain-text ClickHouse quality report. Its
  freshness section covers active symbols.

The deployment script is at the project root as `update.sh` because it is run
manually on a server.
