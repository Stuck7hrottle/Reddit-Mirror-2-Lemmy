# Changelog

## [Unreleased]
### Security
- Dashboard can only start/stop/restart/build the four mirror workers, not any container on the host
- Dashboard control requests require a custom header, blocking cross-site (CSRF) requests from other web pages
- Example nginx config now requires a login by default and must sit in the HTTPS block
- Example compose files bind the dashboard to 127.0.0.1 only
- Added `.gitignore` so `.env`, data, tokens and logs can't be committed by accident

### Fixed
- Dashboard crashed with "Internal Server Error" on Starlette 1.x (new `TemplateResponse` signature)
- "View Logs", "JSON Metrics" and auto-refreshing stats pointed at paths outside `/dashboard/`
- Live log stream moved to `/dashboard/ws/logs` so nginx proxies it; it now shows recent history on connect and survives log rotation
- Build button never worked (its route was shadowed); it now rebuilds and starts the worker
- Worker CPU % always showed 0 on cgroup v2 hosts
- Toggle Sync no longer reorders `.env`

### Changed
- Dashboard dependencies are pinned
- Example dashboard service no longer runs with `--reload`

## [1.4.0] - 2025-10-12
### Added
- Full gallery/media embedding support
- `--update-existing` rewritten for jobs.db
- Improved retry logic & rate limiting
- Updated Docker Compose example

### Fixed
- Images not appearing in mirrored galleries
- Empty `post_map.json` errors on update
