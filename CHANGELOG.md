# Changelog

## [Unreleased]
### Fixed
- Dashboard showed 0 posts mirrored per hour: the post worker only prints to its console, never to bridge.log. Hourly activity and "last post/comment" now come from the database's created_at timestamps
- Hour labels and "Updated at" now show in the viewer's own time zone instead of the server's UTC

### Dashboard redesign
- New look: drafting-paper theme with automatic dark mode, readable on phones
- Overview leads with a plain status ("Mirroring normally", "Quiet for 3 hours", "1 worker stopped")
- 24-hour activity strip: posts and comments mirrored per hour, read from bridge.log
- Totals now come from the posts and comments tables; adds last 24 hours, queue, errors and uptime
- Uptime is how long the workers have been running (was based on the data folder's timestamp)
- Workers show plain-language names, how long they've been up or when they stopped/crashed, and CPU/memory bars
- Worker stats are fetched in parallel, so the list loads in about a second instead of four or more
- Logs page: filter box, Everything/Mirrored/Problems views, follows new lines until you scroll up
- No external CSS/JS libraries (Tailwind, Chart.js, htmx removed); Tailwind's stylesheet had never loaded
- Removed the "Videos uploaded" count, which read a log message nothing writes

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
