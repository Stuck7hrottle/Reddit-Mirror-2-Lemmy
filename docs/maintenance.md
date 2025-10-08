# Maintenance

## Where data is stored
- Token cache: `DATA_DIR/token.json`
- Community map: `DATA_DIR/community_map.json` + `community_map.last`
- Post mapping (if you add one later): `DATA_DIR/post_map.json`

## Force-refresh community map
The map auto-refreshes every 6 hours. To force it sooner, simply delete the
timestamp file and restart the container:

```bash
docker compose exec reddit-lemmy-bridge sh -lc 'rm -f "$DATA_DIR/community_map.last"'
docker compose restart reddit-lemmy-bridge
```

## Clear cached token
If you suspect a bad token (e.g., after changing Lemmy passwords):

```bash
docker compose exec reddit-lemmy-bridge sh -lc 'rm -f "$DATA_DIR/token.json"'
docker compose restart reddit-lemmy-bridge
```

## Safe rebuild
```bash
docker compose up -d --build reddit-lemmy-bridge
```

## Test mode
To verify everything without Reddit:
1. Set `TEST_MODE=true` in `.env`
2. Restart the service:
   ```bash
   docker compose restart reddit-lemmy-bridge
   ```
3. You should see “Example mirrored post” in each mapped community.

## Handling rate limits
The bridge backs off automatically on comment posting. If your instance has
tight limits, consider:
- Reducing `MAX_POSTS_PER_RUN`
- Lowering `REDDIT_LIMIT`
- Increasing `SLEEP_SECONDS`
