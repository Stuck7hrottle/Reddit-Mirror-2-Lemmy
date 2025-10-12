# Maintenance Guide

## Common Tasks

### Check running containers
```bash
docker compose ps
```

### Watch logs
```bash
docker compose logs -f reddit-mirror
```

### Force refresh all mirrored posts
```bash
docker compose run --rm reddit-mirror python3 auto_mirror.py --update-existing
```

### Database inspection
```bash
sqlite3 data/jobs.db "SELECT COUNT(*) FROM posts;"
```

### Backup data
```bash
tar -czvf backup_$(date +%F).tar.gz data/
```

---

## Troubleshooting
- **Images missing** → check Pict-rs connectivity
- **No posts updating** → ensure correct data path in container
- **Lemmy 503** → container overloaded, try again later
