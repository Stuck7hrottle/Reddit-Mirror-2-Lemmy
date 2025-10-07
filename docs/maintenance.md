# Maintenance & Troubleshooting

This document covers advanced operations, safe resets, and common issues for the Reddit → Lemmy Bridge.

---

## 📦 What `data/` Stores (and Why It Matters)

The `data/` directory contains your bridge’s **state**:
- `lemmy_token.json`: cached login token for Lemmy
- `post_map.json`: Reddit→Lemmy post mapping (prevents duplicates)
- (optional) additional caches for comments/edits, depending on implementation

> Deleting `data/` removes the bridge’s memory and **may cause re-mirroring** of existing Reddit posts.

---

## 🧹 Safe Reset Procedures

### Refresh the Lemmy token only
```bash
rm -f data/lemmy_token.json
docker compose up -d reddit-lemmy-bridge
```

### Full rebuild (use with care)
```bash
docker compose down
rm -rf data/*
docker compose up -d --build
```
> This may cause the bridge to re-post previously mirrored content.

---

## 🧩 Rebuild `post_map.json` (Optional Helper)

If you must wipe `data/` but want to **avoid duplicates**, rebuild mappings by scanning existing Lemmy posts via API and regenerating `post_map.json` before running the bridge.

> Ask maintainers or contributors for a `rebuild_map_from_lemmy.py` helper script if needed.

---

## 🔌 Network & Connectivity

If `lemmy` does not resolve inside the container:
- Ensure your Compose file attaches to the correct external network.
- Or set `LEMMY_URL` to your public instance URL (`https://lemmy.example.com`).

Check from inside the container:
```bash
docker compose exec reddit-lemmy-bridge getent hosts lemmy
```

---

## 🚦 Rate Limits & Backoff

- Reddit API may throttle large threads or bursty access.
- Lemmy may rate-limit frequent login attempts and post/comment creation.

Tips:
- Increase `SLEEP_SECONDS` between cycles.
- Tune `COMMENT_SLEEP`, `EDIT_SLEEP`.
- Reduce `MAX_POSTS_PER_RUN` or `COMMENT_LIMIT_TOTAL` for busy subs.

---

## 🧪 Common Errors

### `401 {"error":"incorrect_login"}`
- Verify `LEMMY_USER`, `LEMMY_PASS`, `LEMMY_URL`.
- Clear `data/lemmy_token.json` and re-run.

### `MissingRequiredAttributeException: client_id missing`
- Ensure `.env` contains all `REDDIT_*` variables.
- Confirm your Reddit app credentials at https://www.reddit.com/prefs/apps

### `Json deserialize error: missing field 'community_id'`
- Verify the target Lemmy community is found and the API payload includes `community_id` (script bug or misconfiguration).

### DNS / Name resolution failures
- Confirm Compose network and that the service is attached to the Lemmy-Ansible external network.
- Use a public `LEMMY_URL` as a fallback for testing.

---

## 📋 Operational Tips

- Run **comment refresh** to fill missing threads without duplicates:
  ```bash
  docker compose run --rm reddit-comment-mirror --refresh
  ```
- Keep `data/` bind-mounted and backed up if the content is mission-critical.
- Consider a **read-only mount** for Python scripts to prevent accidental edits in production:
  ```yaml
  - ./auto_mirror.py:/app/auto_mirror.py:ro
  ```

---

## 🛡️ Security

- Keep `.env` out of version control.
- Use a dedicated `mirrorbot` user on your Lemmy instance with only required privileges.
- Rotate Reddit and Lemmy credentials periodically.
