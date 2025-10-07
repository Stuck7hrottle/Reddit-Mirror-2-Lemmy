# Roadmap — Reddit → Lemmy Bridge

---

## 🧱 Phase 1 — Core Mirroring

- [x] Mirror Reddit → Lemmy posts with titles, bodies, and permalinks
- [x] Include embedded Reddit media preview links in post body
- [x] Basic comment mirroring with nesting
- [x] `.env` configuration and Docker Compose setup
- [x] Logging with timestamps and colored status markers

---

## 🧩 Phase 2 — Core Upgrades

- [x] **Bearer Authentication Refactor**  
  - Replace legacy `"auth"` payloads with `Authorization: Bearer` headers  
  - Implement token reuse and cooldowns to prevent duplicate-token bugs
- [x] **Community Map Refresh**  
  - Auto-refresh every 6 hours  
  - Persist map to `/app/data/community_map.json`
- [x] **Improved Lemmy Login Cache**  
  - Smart retry logic with exponential backoff  
  - Cooldown enforcement to avoid rate limits
- [x] **Embedded Permalinks**  
  - Add Reddit source link + media previews for richer Lemmy posts

---

## 💬 Phase 3 — Extended Features

- [ ] Mirror Reddit edits → Lemmy edits
- [ ] Bi-directional comment sync (optional)
- [ ] Post flairs and user tagging
- [ ] Reddit media upload proxying

---

## 🧰 Phase 4 — Backend & Maintenance

- [ ] SQLite caching backend for post + comment metadata
- [ ] Web dashboard to view sync logs
- [ ] Multi-Lemmy federation support
- [ ] Persistent queue for Reddit polling with retry safety
- [ ] Auto-healthcheck and container self-restart on failure

---

## 🧩 Phase 5 — Community Management

- [ ] Auto-create Lemmy communities if missing
- [ ] Optional flair → tag conversion
- [ ] Modmail integration for moderation mirroring

---

## 🪪 License

MIT License  
Open-source and free for all federated instances.
