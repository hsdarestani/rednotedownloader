# RedNote Downloader Telegram Bot

Telegram bot for downloading public RedNote / Xiaohongshu posts.

## Supported links

- `rednote.com`
- `xiaohongshu.com`
- `xhslink.com`
- `xhslink.cn`

The bot first tries video extraction with `yt-dlp`. If the post is an image note, it falls back to RedNote page image extraction.

## Environment variables

- `TELEGRAM_BOT_TOKEN` — required
- `REDNOTE_COOKIE` — optional, for public pages that RedNote refuses without a browser session
- `DOWNLOAD_CONCURRENCY` — optional, defaults to `2`
- `LOG_LEVEL` — optional, defaults to `INFO`

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="..."
python main.py
```

## Production

Pushes to `main` automatically deploy to the configured server through GitHub Actions.

Required repository secrets:

- `HOST`
- `PASS`
- `TELEGRAM_BOT_TOKEN`

The workflow installs Python, ffmpeg and the app under `/opt/rednotedownloader`, then runs it as a systemd service named `rednotedownloader`.

## Notes

RedNote can occasionally return CAPTCHA / risk-control pages to datacenter IPs. The bot reports this cleanly instead of hanging. An optional `REDNOTE_COOKIE` can be added later if needed.


## Diagnostics

Production deploys capture recent systemd logs for RedNote download troubleshooting.


## Large videos via Local Bot API

For original-quality RedNote videos larger than Telegram's standard 50 MB bot upload limit, add these repository secrets:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`

Obtain them from Telegram's API development tools at my.telegram.org. When both are present, deployment automatically provisions a local Bot API server and switches the bot to large-file mode (up to 2000 MB). Deployment is re-triggered after credential setup.
