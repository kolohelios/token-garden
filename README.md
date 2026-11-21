# Token Garden

A local Python app for saved Pi token usage, with a Herdr tab-bar readout and terminal popup. No Pi extension is required.

## Run from source

Python 3.12+ on macOS/Linux, with curses support:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
sh garden.sh serve
```

Run these in another terminal:

```sh
sh garden.sh status
sh garden.sh ui
sh garden.sh show --window 24h
```

**h** selects the last hour; **d**, the last day. Arrow keys/Page Up/Page Down scroll; **q** or Escape closes. Sparklines use per-model scales, oldest to newest.

For a snapshot without a server:

```sh
sh garden.sh scan
sh garden.sh show --offline --window 24h
```

The source wrapper uses `.venv` and `.state` beside `garden.sh`. An installed `token-garden` command is also available. **Pass `--state /absolute/path/to/private-state` to installed collector/offline commands**: the current default is relative to the installed package, not a user data directory. All collector and offline commands must use the same state directory. Do not run two collectors against one ledger/port.

## Herdr (optional)

Review `herdr-config.example.toml`, replace `/absolute/path/to/token-garden` with your checkout, and merge only its entries into your configuration. Check shortcut conflicts and preserve unrelated entries. Configuration reload and service installation are manual, opt-in operations.

`herdr-plugin.toml` offers a terminal overlay or split. It requires the source checkout's `.venv`; it is not installed by the Python wheel. Herdr 0.9.0+ is declared by the plugin manifest. Integration examples do not install background services.

## What counts

- Reads saved JSONL under `~/.pi/agent/sessions/`; repeated `--sessions PATH` on `serve`/`scan` overrides the default roots.
- Counts fresh input, output, cache reads, and cache writes. The total includes all four.
- Attributes each response to its recorded provider/model; missing attribution stays `unattributed`.
- Counts abandoned branches and deduplicates copies retaining ID, timestamp, kind, model, and usage. Embedded compaction tails are not counted again. Imports regenerating IDs cannot be deduplicated this way.
- Holds incomplete trailing records until the next scan. Malformed entries, inaccessible sources, and unsupported session versions show partial coverage. Valid usage on aborted/error responses still counts.
- Excludes ephemeral `--no-session` runs and unreported provider work. No costs, quotas, billing estimates, process monitoring, or diagnosis.

The worker reconciles every five seconds. HTTP reads cached snapshots, not SQLite. Missing, partial, stale, and offline states remain explicit; snapshots become stale after thirty seconds without a successful scan.

## Privacy and operation

Session files are read-only. The SQLite ledger retains thirty days of usage metadata and ingestion checkpoints, **including local source paths**, but not conversation bodies. Provider/model names appear in the UI/API/metrics; avoid sensitive model aliases. Keep the ledger, sessions, logs, and monitoring storage private. Do not attach them to issues or releases.

The collector binds to `127.0.0.1:9467` with Host guards. Read-only endpoints:

- `GET /usage?window=3600`
- `GET /usage?window=86400`
- `GET /metrics`

`--port PORT` changes the collector/client port. Loopback is not authentication against other local processes; do not expose the collector through a public proxy.

```mermaid
flowchart LR
    S[Saved Pi sessions] --> W[Token worker]
    W --> L[Private SQLite ledger]
    W --> C[Immutable token cache]
    C --> H[Loopback HTTP]
    H --> U[Herdr bar and popup]
    H --> P[Optional Prometheus]
```

`prometheus.yml` is an optional loopback scrape example. Token window totals are **gauges**: do not apply `rate()` or `increase()`. Choose private monitoring storage and retention deliberately; the app does not manage Prometheus or system services.

## Development and release checks

See [the release preparation scope](specs/public-release.md). Development tooling and CI recipes are documented below as they are implemented.

## Release status

Publication is not automated. No repository, package registry, or release upload is created by local checks. **A license has not been selected**; source availability alone does not grant an open-source license. Confirm licensing, package name/version, and publication approval before distributing.
