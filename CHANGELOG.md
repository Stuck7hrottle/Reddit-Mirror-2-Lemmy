# 🧾 Changelog

## v2.0 — October 2025
### Major Improvements
- 🧩 **Dual-bot architecture**: Separate post and comment mirroring
- 🔒 **JWT caching system**: Tokens auto-refresh and persist safely
- ⏳ **Rate-limit handling**: Adaptive backoff with detailed logs
- 🧠 **Lemmy user verification** after login to confirm identity
- 🧱 **Docker Compose overhaul**: Simplified environment setup
- 🧰 Added `.env.example` for clean configuration
- 🪶 Improved README with clear deployment steps

### Minor Fixes
- Corrected comment threading reliability
- Stabilized concurrent JWT use between threads
- Simplified network retries and logging output
- Added trusted user support for Lemmy rate limits

---
Future releases will focus on:
- 📊 Web UI dashboard for mirror status
- 🌐 Multi-instance Lemmy federation
- 🎨 Rich media embedding for Reddit posts