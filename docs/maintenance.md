# Maintenance Guide

## 🧰 Common Tasks

### Rebuilding Comment Mapping

If `/data/comment_map.json` is removed, re-fetch all comments:

```bash
docker compose run --rm -e REFRESH=true reddit-comment-mirror
```

Rebuilds the local comment map and missing Lemmy comments.

### Reset Lemmy Authentication

```bash
rm -f data/lemmy_token.json
docker compose restart reddit-lemmy-bridge
```

### Clean Cache

```bash
rm -rf data/*
docker compose up -d reddit-lemmy-bridge
```

---

## ⚙️ Troubleshooting

### 401 or "incorrect_login" Errors

Check `LEMMY_USER` and `LEMMY_PASS` in `.env`.  
Delete `/data/lemmy_token.json` to reauthenticate.

### Duplicate Comments

Delete `/data/comment_map.json` and re-run:

```bash
docker compose run --rm -e REFRESH=true reddit-comment-mirror
```

### Network Errors

Attach to Lemmy’s Docker network:

```yaml
networks:
  lemmy_net:
    external: true
    name: example_default
```

### Reddit API Rate Limit

If you see `RATELIMIT` errors, increase `SLEEP_SECONDS` or reduce `REDDIT_LIMIT`.

---

## 🧭 Advanced Notes

- `post_map.json`: Reddit → Lemmy post mappings  
- `comment_map.json`: Reddit → Lemmy comment mappings  

Mount `/data` as a Docker volume to persist mappings.

---

## 📘 Related

- [`README.md`](../README.md)
- [`ROADMAP.md`](../ROADMAP.md)
