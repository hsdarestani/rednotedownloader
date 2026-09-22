import asyncio
import hashlib
import html
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests
import yt_dlp
from bs4 import BeautifulSoup
from telegram import Update
from telegram.constants import ChatAction
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
REDNOTE_COOKIE = os.getenv("REDNOTE_COOKIE", "").strip()
MAX_TELEGRAM_BYTES = 47 * 1024 * 1024
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGES = 20
DOWNLOAD_CONCURRENCY = max(1, int(os.getenv("DOWNLOAD_CONCURRENCY", "2")))

ALLOWED_HOSTS = (
    "rednote.com",
    "xiaohongshu.com",
    "xhslink.com",
    "xhslink.cn",
)
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36"
)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("rednote-bot")


def _is_allowed_host(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return any(host == root or host.endswith("." + root) for root in ALLOWED_HOSTS)


def extract_rednote_url(text: str) -> str | None:
    for match in URL_RE.findall(text or ""):
        candidate = match.rstrip(").,]}>\"'")
        try:
            parsed = urlparse(candidate)
        except ValueError:
            continue
        if parsed.scheme in {"http", "https"} and _is_allowed_host(parsed.hostname or ""):
            return candidate
    return None


def _request_headers(referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    if REDNOTE_COOKIE:
        headers["Cookie"] = REDNOTE_COOKIE
    return headers


def resolve_share_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if "xhslink." not in host:
        return url

    response = requests.get(
        url,
        headers=_request_headers(),
        timeout=20,
        allow_redirects=True,
    )
    response.raise_for_status()
    if _is_allowed_host(urlparse(response.url).hostname or ""):
        return response.url
    return url


def _ydl_options(download_dir: Path) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.rednote.com/",
    }
    if REDNOTE_COOKIE:
        headers["Cookie"] = REDNOTE_COOKIE

    return {
        "format": "best[filesize<47M]/best[filesize_approx<47M]/best[height<=1080]/best",
        "outtmpl": str(download_dir / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "http_headers": headers,
        "overwrites": True,
        "restrictfilenames": False,
    }


def _media_files(download_dir: Path) -> list[Path]:
    ignored = {".part", ".ytdl", ".json", ".description", ".vtt", ".srt"}
    files = [
        p for p in download_dir.iterdir()
        if p.is_file() and p.suffix.lower() not in ignored and not p.name.startswith(".")
    ]
    return sorted(files, key=lambda p: p.stat().st_size, reverse=True)


def download_video(url: str, download_dir: Path) -> tuple[list[Path], dict]:
    with yt_dlp.YoutubeDL(_ydl_options(download_dir)) as ydl:
        info = ydl.extract_info(url, download=True)
    files = _media_files(download_dir)
    if not files:
        raise RuntimeError("No downloadable video file was produced.")
    return [files[0]], info or {}


def _decode_page_text(text: str) -> str:
    return (
        html.unescape(text)
        .replace("\\u002F", "/")
        .replace("\\u002f", "/")
        .replace("\\/", "/")
        .replace("\\u0026", "&")
        .replace("\\u003D", "=")
        .replace("\\u003d", "=")
    )


def _image_candidates(page_text: str) -> list[str]:
    decoded = _decode_page_text(page_text)
    soup = BeautifulSoup(decoded, "html.parser")
    candidates: list[str] = []

    for tag in soup.find_all("meta"):
        prop = (tag.get("property") or tag.get("name") or "").lower()
        if prop in {"og:image", "twitter:image", "twitter:image:src"}:
            value = tag.get("content")
            if value:
                candidates.append(value)

    key_patterns = [
        r'"(?:urlDefault|urlPre|url_default|url_pre)"\s*:\s*"([^"]+)"',
        r'"imageUrl"\s*:\s*"([^"]+)"',
    ]
    for pattern in key_patterns:
        candidates.extend(re.findall(pattern, decoded, flags=re.IGNORECASE))

    # Last-resort CDN discovery for image notes.
    for value in re.findall(r'https?://[^"\'<>\s]+', decoded, flags=re.IGNORECASE):
        if "xhscdn.com" in value.lower() and any(
            token in value.lower() for token in ("sns-img", "webpic", "image", "notes")
        ):
            candidates.append(value)

    result: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        item = _decode_page_text(item).replace("&amp;", "&")
        if not item.startswith("http"):
            continue
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def download_images(url: str, download_dir: Path) -> tuple[list[Path], dict]:
    response = requests.get(
        url,
        headers=_request_headers("https://www.rednote.com/"),
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()

    page_title = ""
    soup = BeautifulSoup(response.text, "html.parser")
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        page_title = og_title["content"].strip()
    elif soup.title and soup.title.string:
        page_title = soup.title.string.strip()

    candidates = _image_candidates(response.text)
    if not candidates:
        raise RuntimeError("No downloadable images were found.")

    files: list[Path] = []
    hashes: set[str] = set()
    for index, image_url in enumerate(candidates):
        if len(files) >= MAX_IMAGES:
            break
        try:
            r = requests.get(
                image_url,
                headers=_request_headers(response.url),
                timeout=30,
                allow_redirects=True,
            )
            r.raise_for_status()
            content_type = (r.headers.get("Content-Type") or "").split(";")[0].lower()
            if not content_type.startswith("image/"):
                continue
            data = r.content
            if not data or len(data) > MAX_IMAGE_BYTES:
                continue

            digest = hashlib.sha1(data).hexdigest()
            if digest in hashes:
                continue
            hashes.add(digest)

            ext = mimetypes.guess_extension(content_type) or ".jpg"
            if ext == ".jpe":
                ext = ".jpg"
            path = download_dir / f"image_{len(files) + 1:02d}{ext}"
            path.write_bytes(data)
            files.append(path)
        except requests.RequestException:
            logger.debug("Image candidate failed: %s", image_url, exc_info=True)

    if not files:
        raise RuntimeError("RedNote returned an image post, but the images could not be downloaded.")

    return files, {"title": page_title, "webpage_url": response.url}


def _probe_duration(path: Path) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        value = float(result.stdout.strip())
        return value if value > 0 else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def compress_video(path: Path, duration: float | None = None) -> Path:
    if path.stat().st_size <= MAX_TELEGRAM_BYTES:
        return path
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Video is too large for Telegram and ffmpeg is unavailable.")

    duration = duration or _probe_duration(path)
    if not duration:
        raise RuntimeError("Video is too large for Telegram and its duration could not be detected.")

    target_bytes = 44 * 1024 * 1024
    total_kbps = int((target_bytes * 8) / duration / 1000)
    audio_kbps = 64 if total_kbps < 500 else 96
    video_kbps = max(100, total_kbps - audio_kbps - 24)

    output = path.with_name(path.stem + "_telegram.mp4")
    command = [
        "ffmpeg", "-y", "-i", str(path),
        "-vf", "scale=1280:-2:force_original_aspect_ratio=decrease",
        "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", f"{video_kbps}k",
        "-maxrate", f"{video_kbps}k",
        "-bufsize", f"{video_kbps * 2}k",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        "-movflags", "+faststart",
        str(output),
    ]
    try:
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=1800,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Video compression timed out.") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg failed: %s", exc.stderr.decode(errors="ignore")[-2000:])
        raise RuntimeError("Video compression failed.") from exc

    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("Video compression failed.")
    if output.stat().st_size > MAX_TELEGRAM_BYTES:
        raise RuntimeError("The video is still too large for Telegram after compression.")
    return output


def download_rednote(url: str, download_dir: Path) -> tuple[list[Path], dict, str]:
    resolved = resolve_share_url(url)
    try:
        files, info = download_video(resolved, download_dir)
        video = files[0]
        duration = info.get("duration") if isinstance(info, dict) else None
        video = compress_video(video, duration)
        return [video], info, "video"
    except Exception as video_error:
        logger.info("Video extraction failed, trying image post fallback: %s", video_error)
        # Remove partial/video files before the image fallback.
        for path in download_dir.iterdir():
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass
        try:
            files, info = download_images(resolved, download_dir)
            return files, info, "images"
        except Exception as image_error:
            raise RuntimeError(
                f"Could not download this RedNote post. Video: {video_error}. Images: {image_error}"
            ) from image_error


def clean_caption(info: dict) -> str:
    title = ""
    if isinstance(info, dict):
        title = str(info.get("title") or "").strip()
    if not title:
        return "Downloaded from RedNote"
    title = re.sub(r"\s+", " ", title)
    if len(title) > 850:
        title = title[:847] + "..."
    return f"{title}\n\nDownloaded from RedNote"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "Send me a public RedNote link and I will download the video or images.\n\n"
        "Supported: rednote.com, xiaohongshu.com and xhslink share links.\n"
        "Please only download content you are allowed to save."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return

    url = extract_rednote_url(message.text)
    if not url:
        await message.reply_text("Please send a valid RedNote share link.")
        return

    status = await message.reply_text("Downloading...")
    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_DOCUMENT)

    async with DOWNLOAD_SEMAPHORE:
        try:
            with tempfile.TemporaryDirectory(prefix="rednote_") as tmp:
                paths, info, media_type = await asyncio.to_thread(
                    download_rednote, url, Path(tmp)
                )
                caption = clean_caption(info)

                if media_type == "video":
                    path = paths[0]
                    with path.open("rb") as file_obj:
                        await message.reply_video(
                            video=file_obj,
                            caption=caption,
                            supports_streaming=True,
                            read_timeout=300,
                            write_timeout=300,
                            connect_timeout=60,
                        )
                else:
                    for index, path in enumerate(paths):
                        with path.open("rb") as file_obj:
                            await message.reply_document(
                                document=file_obj,
                                caption=caption if index == 0 else None,
                                read_timeout=180,
                                write_timeout=180,
                                connect_timeout=60,
                            )
                try:
                    await status.delete()
                except BadRequest:
                    pass
        except (TimedOut, NetworkError):
            logger.exception("Telegram network error")
            await status.edit_text("Telegram timed out while sending the file. Please try again.")
        except Exception as exc:
            logger.exception("Download failed")
            text = str(exc)
            lowered = text.lower()
            if any(word in lowered for word in ("captcha", "blocked", "403", "risk", "login")):
                friendly = (
                    "RedNote blocked the server request or requires verification for this post. "
                    "Try another public share link."
                )
            else:
                friendly = (
                    "I could not download this post. Make sure the link is public and still available, "
                    "then try again."
                )
            try:
                await status.edit_text(friendly)
            except BadRequest:
                await message.reply_text(friendly)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled Telegram error", exc_info=context.error)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(4)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    logger.info("RedNote Downloader Bot started")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
        close_loop=False,
    )


if __name__ == "__main__":
    main()
