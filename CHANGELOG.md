# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-06-21

### Added

- Web UI for pasting YouTube URLs and downloading videos
- Quality presets: best MP4, 1080p, 720p, 480p, audio-only MP3
- Real-time download progress via Server-Sent Events (SSE)
- Playlist support — browse playlist entries and download selected videos in batch
- Download history persisted to JSON file
- Optional token authentication (`YTGRAB_TOKEN`)
- Configurable limits: max concurrent jobs (`YTGRAB_MAX_JOBS`), max file size (`YTGRAB_MAX_SIZE_MB`)
- Docker image with auto-updating yt-dlp on container start
- Docker Compose configuration with healthcheck
- Nginx reverse proxy config with TLS, SSE support, and security headers

[1.0.0]: https://github.com/skydope/ytgrab/releases/tag/v1.0.0
