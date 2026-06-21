# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-06-21

### Added

- Web UI with Alpine.js declarative frontend (dark/light theme, keyboard accessible)
- Quality presets: best MP4, 1080p, 720p, 480p, audio-only MP3
- Format preview table with codec info and estimated file sizes
- Copy yt-dlp command button with clipboard API
- Real-time download progress via Server-Sent Events (SSE) with auto-retry
- Multi-platform support: YouTube, Vimeo, Twitter/X, TikTok, Instagram
- Playlist batch download with progress counter
- Download history persisted to JSON file (max 500 entries)
- Token authentication via Bearer header, HTTP-only cookie (30-day), or query param
- Auth endpoints: `POST /api/auth` and `POST /api/logout`
- Configurable limits: max concurrent jobs, max file size, rate limiting (slowapi)
- Automatic eviction of completed jobs from memory after 1 hour
- Docker image with non-root user, auto-updating yt-dlp, and healthcheck
- Docker Compose configuration
- Nginx reverse proxy config with TLS, CSP, Referrer-Policy, Permissions-Policy, SSE support
- 29 pytest tests covering helpers, models, auth, and API endpoints

### Security

- HTTP-only cookie with SameSite=Lax and conditional Secure flag
- URL sanitization in server logs to prevent token leaks
- Path containment check on file serving
- Rate limiting: 30/min default, 10/min on `/api/info`, 5/min on `/api/jobs`
- Graceful fallback if static files are missing

[1.0.0]: https://github.com/skydope/ytgrab/releases/tag/v1.0.0
