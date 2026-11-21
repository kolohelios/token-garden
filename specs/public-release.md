# Public release preparation

## Goal and boundary

Ship the existing Python/Herdr app as an inspectable source distribution and wheel, not a Pi extension. Work stays in this checkout. Do not publish, create a remote, change live configuration, or read/package private state or sessions.

## Reader scenario

Alex downloads the source on a laptop, creates a virtual environment, and runs the offline fixture checks before pointing the collector at saved sessions. “Where will the ledger go?” The README names the state path explicitly. When the collector is unavailable, the status command says offline rather than showing invented zeros.

## Plan

1. Establish a sanitized Git baseline: application, synthetic tests, portable examples, public documentation, and this release scope. Exclude local state, environments, generated metadata, and reports.
2. Pin development dependencies and Nix tooling. Keep Python assembly separate from Shaka repository gates. Test wheel/sdist contents and install the built wheel outside the source tree with synthetic sessions and isolated state.
3. Add read-only GitHub Actions using the same local gate, dependency caches, and narrowly selected reports/packages. No publishing job or credentials.

## Acceptance

- Existing lint, format, strict typing, and branch-aware coverage gates remain enforced; minimum coverage stays 90%.
- Package inventories allow only intended application/metadata files in the wheel and reviewed source files in the sdist. Private state never enters either archive.
- Installed CLI indexes synthetic sessions and renders offline totals; no live collector or user session root is contacted.
- Shaka preflight and whitespace checks run without a fabricated project kind. `shaka build` is a stub and does not assemble Python artifacts.
- Local validation and CI share recipes. Cache keys include pinned inputs; uploads contain only fixture reports and reviewed release archives.

## Publication decisions

The owner selected MIT OR Apache-2.0, at the recipient’s option. Include both canonical license texts, Jon Edwards’s copyright notice, and SPDX packaging metadata in releases. The minimal Shaka repository-gate integration is approved; native Python/TS support is deferred. Publishing and repository creation still require separate approval, as do the final package name/version.
