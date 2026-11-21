# Token Garden architecture

Token Garden reads saved Pi usage and serves a loopback token view to terminal clients. It neither instruments running agents nor estimates billing.

## Components and data

See the README's data-flow diagram. `collector.py` reads supported saved JSONL sessions; `core.py` validates usage and computes deduplication identities. `ledger.py` owns SQLite events, source checkpoints, retention, and snapshot queries. `runtime.py` publishes immutable snapshots from one worker. `service.py` serves cached HTTP/metrics; `cli.py`, `display.py`, and `ui.py` provide command routing and terminal rendering.

The ledger stores hashes, timestamps, provider/model labels, usage categories, and source paths. It does not store conversation bodies. Source paths stay out of snapshots and metrics. Source sessions are read-only. State is private local data, never release content.

## Interfaces and operation

The README documents commands, windows, endpoint paths, default ports, and explicit installed-package state selection. HTTP handlers never query SQLite. Collection errors preserve prior data while marking it stale. The popup and status client tolerate an unavailable collector. Prometheus and Herdr integration are optional and manually configured.

Only one collector may own a ledger/port. The app does not install or supervise background services. No remote authentication boundary is provided; keep the API on loopback.

## Validation boundary

`just check` combines Python validation/package smoke with Shaka repository gates. Application tests use synthetic sessions and disposable state; installed smoke uses an isolated Python interpreter outside the source directory. See the README for tooling pins, output inventories, and CI limitations.
