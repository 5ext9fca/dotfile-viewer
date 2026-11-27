# Repository Guidelines

## Language

Conduct all user-facing conversation in Chinese. Keep code, identifiers and commands in their native form.

## Scope

This project is dotfile-viewer: the single owner of software declarations, configuration paths, profiles, local configuration editing, snapshot v2 export/inspection/application, and the shared deployment lease. dotfile-syncronizer depends on its public Python API and owns installation and combined installation/application orchestration. Never add an installer or reverse dependency here.

Local files are authoritative configuration content. Do not add repository-maintained application payloads or a `configs/` tree. Default declarations are user-editable choices, not exhaustive configuration discovery. Preserve declaration v1/profile v3 semantics. Old snapshot v1 and historical journal restoration commands are intentionally unsupported. Existing user backups and journals must not be deleted.

Keep per-file backups, guarded failure recovery, atomic publication, cooperative deployment locks and explicit hard-link detachment. Never automatically reclaim an unclean-exit lease. No cross-stage transactions, version locking, service probes, automatic retries, crash recovery or environment reproducibility guarantees. Application must preserve unknown destination files.

## Implementation

Use Python >=3.14; validate on Python 3.14, without rejecting later versions. Dependencies must be installed before invoking commands, including previews. The project provides CLI and public Python API interfaces, with no Web UI or third-party runtime dependencies. No Node build or remote hosting.

`src/api.py` is the public integration surface. CLI and public Python API share editor, snapshot/application and lease implementations. Package resources contain the only default declarations, profiles and declaration schemas; installation execution policies remain in syncronizer. No checkout path imports or hard-coded user paths.

Before modifying declarations, read `docs/configuration.md` and `docs/llm-config-authoring.md`, the existing declaration and its schema; do not invent package identities or unverified platform paths. Do not execute declaration text. Catalog overrides replace, rather than merge, defaults.

## Validation and Documentation

Run `./tests/check.sh --strict` or `./tests/check.ps1 -Strict` using Python 3.14. Tests use temporary catalogs, HOME/configuration roots and synthetic content. Never capture real user configuration into fixtures. Test conflicts, links, exclusions, backups/failure recovery, malicious archives and shared lease ownership. Record native platforms not exercised; simulations do not establish native coverage.

Review README, affected docs and these instructions against final code. Keep validation records in `tests/acceptance.md`. Keep credentials and private host content out of tracked artifacts. Commit focused, verified changes together; do not commit incomplete work or unrelated user edits.
