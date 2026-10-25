# Changelog

## [Unreleased]
### Fixed
- repair_posts.py treated 3 in 4 posts as deleted from Reddit: /by_id returns only 25 posts per request. It now asks for 25 at a time and re-checks any post a batch leaves out
- Log rotation uses numbered files (bridge.log.1, .2…) so rotating twice in one day can't fail

### Added
- `contrib/backup.sh`: nightly consistent backup of jobs.db, media cache and .env, kept 14 days
- `contrib/logrotate.conf` and `contrib/cron.d-reddit-mirror` to install log rotation and the backup schedule
- README section on installing backups and restoring one
- `repair_posts.py`: fixes old posts that lost their link or whose video fell back to a link, editing only posts that gain something. Dry-run mode, resumable, fetches Reddit 25 posts per request

### Fixed
- Link posts (news articles and other web pages) arrived on Lemmy with only a title: the link was dropped. The link is now the post's URL, so Lemmy shows its preview
- YouTube/Rumble/etc. links are now the post's URL instead of a "[Video]" line in the body, so Lemmy shows a thumbnail and player
- If Lemmy refuses a link, the post is created with the link in the body instead of failing
- Post and media activity is now written to bridge.log, so it shows on the dashboard's Logs page

### Changed
- Upload and video-download timeouts default to 600 seconds so long videos aren't re-uploaded while pict-rs transcodes them
- README documents how media is handled and how to allow longer videos in pict-rs

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
