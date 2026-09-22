import asyncio
import hashlib
import html
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
import yt_dlp
from yt_dlp.utils import js_to_json
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, UnidentifiedImageError
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


def _normalize_for_ytdlp(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path

    if "rednote.com" in host:
        if path.startswith("/search_result/"):
            path = "/explore/" + path.split("/search_result/", 1)[1]
        parsed = parsed._replace(netloc="www.xiaohongshu.com", path=path)
        return urlunparse(parsed)
    return url


def _balanced_js_object(source: str, marker: str) -> str | None:
    marker_index = source.find(marker)
    if marker_index < 0:
        return None

    start = source.find("{", marker_index + len(marker))
    if start < 0:
        return None

    depth = 0
    quote = None
    escaped = False
    for index in range(start, len(source)):
        ch = source[index]
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue

        if ch in {'"', "'", "`"}:
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return None


def _extract_initial_state(page_text: str) -> dict:
    raw = _balanced_js_object(page_text, "window.__INITIAL_STATE__")
    if not raw:
        return {}
    try:
        return json.loads(js_to_json(raw))
    except Exception:
        logger.debug("Could not parse RedNote initial state", exc_info=True)
        return {}


def _find_note_info(initial_state: dict, url: str) -> dict:
    note_map = (
        initial_state.get("note", {}).get("noteDetailMap", {})
        if isinstance(initial_state, dict)
        else {}
    )
    if not isinstance(note_map, dict) or not note_map:
        return {}

    path_parts = [part for part in urlparse(url).path.split("/") if part]
    candidate_id = next(
        (part for part in reversed(path_parts) if re.fullmatch(r"[0-9a-fA-F]{16,32}", part)),
        None,
    )
    if candidate_id and candidate_id in note_map:
        item = note_map.get(candidate_id) or {}
        return item.get("note") or item

    for item in note_map.values():
        if not isinstance(item, dict):
            continue
        note = item.get("note") if isinstance(item.get("note"), dict) else item
        if isinstance(note, dict) and note:
            return note
    return {}


def _walk_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _analyze_rednote_page(url: str) -> dict:
    response = requests.get(
        url,
        headers=_request_headers("https://www.rednote.com/"),
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()

    state = _extract_initial_state(response.text)
    note = _find_note_info(state, response.url)
    title = ""
    soup = BeautifulSoup(response.text, "html.parser")
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        title = og_title["content"].strip()
    elif isinstance(note, dict):
        title = str(note.get("title") or "").strip()

    video = note.get("video") if isinstance(note, dict) else None
    video_candidates = []
    if isinstance(video, dict):
        for item in _walk_dicts(video.get("media", {}).get("stream", {})):
            urls = []
            master = item.get("masterUrl")
            if isinstance(master, str) and master.startswith("http"):
                urls.append(master)
            backups = item.get("backupUrls")
            if isinstance(backups, list):
                urls.extend(u for u in backups if isinstance(u, str) and u.startswith("http"))
            if not urls:
                continue
            try:
                size = int(item.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            try:
                height = int(item.get("height") or 0)
            except (TypeError, ValueError):
                height = 0
            for media_url in urls:
                video_candidates.append({
                    "url": media_url,
                    "size": size,
                    "height": height,
                })

        origin_key = (
            video.get("consumer", {}).get("originVideoKey")
            if isinstance(video.get("consumer"), dict)
            else None
        )
        if isinstance(origin_key, str) and origin_key:
            video_candidates.append({
                "url": f"https://sns-video-bd.xhscdn.com/{origin_key}",
                "size": 0,
                "height": 99999,
            })

    # Prefer an ordinary <=1080p stream over the often huge original source.
    video_candidates.sort(
        key=lambda x: (
            x.get("height", 0) > 1080,
            x.get("size", 0) == 0,
            -(x.get("height", 0) or 0),
            x.get("size", 0) or 10**18,
        )
    )

    return {
        "resolved_url": response.url,
        "title": title,
        "note": note,
        "has_state": bool(state),
        "has_video": bool(video_candidates),
        "video_candidates": video_candidates,
        "page_text": response.text,
    }


def _download_direct_video(candidates: list[dict], download_dir: Path, referer: str) -> tuple[list[Path], dict]:
    errors = []
    for index, candidate in enumerate(candidates[:8], start=1):
        media_url = candidate.get("url")
        if not media_url:
            continue
        try:
            response = requests.get(
                media_url,
                headers=_request_headers(referer),
                timeout=(10, 20),
                stream=True,
                allow_redirects=True,
            )
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type or "application/json" in content_type:
                raise RuntimeError(f"Unexpected media response: {content_type}")

            path = download_dir / f"rednote_direct_{index}.mp4"
            with path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)

            if path.stat().st_size < 1024:
                raise RuntimeError("Downloaded video is empty.")
            _, video_codec, _ = _video_stream_info(path)
            if not video_codec:
                raise RuntimeError("Downloaded stream is not a video.")
            return [path], {"title": "", "webpage_url": referer}
        except Exception as exc:
            errors.append(str(exc))
            logger.info("Direct RedNote video candidate failed: %s", exc)

    raise RuntimeError("Direct RedNote video download failed: " + " | ".join(errors[-3:]))


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
        "retries": 1,
        "fragment_retries": 1,
        "socket_timeout": 15,
        "http_headers": headers,
        "overwrites": True,
        "restrictfilenames": False,
    }


def _media_files(download_dir: Path) -> list[Path]:
    video_extensions = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi", ".flv", ".ts"}
    files = [
        p for p in download_dir.iterdir()
        if p.is_file() and p.suffix.lower() in video_extensions and not p.name.startswith(".")
    ]
    return sorted(files, key=lambda p: p.stat().st_size, reverse=True)


def download_video(url: str, download_dir: Path) -> tuple[list[Path], dict]:
    ytdlp_url = _normalize_for_ytdlp(url)
    with yt_dlp.YoutubeDL(_ydl_options(download_dir)) as ydl:
        info = ydl.extract_info(ytdlp_url, download=True)
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

            try:
                image = Image.open(io.BytesIO(data))
                image = ImageOps.exif_transpose(image)
                if image.mode != "RGB":
                    if "A" in image.getbands():
                        background = Image.new("RGB", image.size, "white")
                        background.paste(image, mask=image.getchannel("A"))
                        image = background
                    else:
                        image = image.convert("RGB")

                if max(image.size) > 4096:
                    image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)

                path = download_dir / f"image_{len(files) + 1:02d}.jpg"
                quality = 92
                image.save(path, format="JPEG", quality=quality, optimize=True)

                while path.stat().st_size > 9 * 1024 * 1024 and max(image.size) > 1280:
                    image.thumbnail(
                        (max(1280, int(image.width * 0.85)), max(1280, int(image.height * 0.85))),
                        Image.Resampling.LANCZOS,
                    )
                    quality = max(78, quality - 4)
                    image.save(path, format="JPEG", quality=quality, optimize=True)

                if path.stat().st_size <= 10 * 1024 * 1024:
                    files.append(path)
                else:
                    path.unlink(missing_ok=True)
            except (UnidentifiedImageError, OSError, ValueError):
                logger.debug("Image conversion failed: %s", image_url, exc_info=True)
        except requests.RequestException:
            logger.debug("Image candidate failed: %s", image_url, exc_info=True)

    if not files:
        raise RuntimeError("RedNote returned an image post, but the images could not be downloaded.")

    return files, {"title": page_title, "webpage_url": response.url}


def _probe_media(path: Path) -> dict:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration:stream=index,codec_type,codec_name",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return json.loads(result.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return {}


def _video_stream_info(path: Path) -> tuple[float | None, str | None, str | None]:
    probe = _probe_media(path)
    duration = None
    try:
        value = float((probe.get("format") or {}).get("duration") or 0)
        duration = value if value > 0 else None
    except (TypeError, ValueError):
        pass

    video_codec = None
    audio_codec = None
    for stream in probe.get("streams") or []:
        if stream.get("codec_type") == "video" and video_codec is None:
            video_codec = stream.get("codec_name")
        elif stream.get("codec_type") == "audio" and audio_codec is None:
            audio_codec = stream.get("codec_name")
    return duration, video_codec, audio_codec


def _run_ffmpeg(command: list[str]) -> None:
    try:
        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Video conversion timed out.") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg failed: %s", exc.stderr.decode(errors="ignore")[-2000:])
        raise RuntimeError("Video conversion failed.") from exc


def prepare_video(path: Path, duration_hint: float | None = None) -> Path:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required for video delivery.")

    duration, video_codec, _ = _video_stream_info(path)
    duration = duration or duration_hint
    if not video_codec:
        raise RuntimeError("Downloaded media is not a real video.")

    output = path.with_name(path.stem + "_telegram.mp4")
    common = [
        "ffmpeg", "-y", "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", "scale=1280:-2:force_original_aspect_ratio=decrease",
        "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
    ]

    if path.stat().st_size <= 38 * 1024 * 1024:
        _run_ffmpeg(common + ["-crf", "22", str(output)])
    else:
        if not duration:
            raise RuntimeError("Video is too large and its duration could not be detected.")
        target_bytes = 43 * 1024 * 1024
        total_kbps = int((target_bytes * 8) / duration / 1000)
        video_kbps = max(120, total_kbps - 120)
        _run_ffmpeg(
            common
            + [
                "-b:v", f"{video_kbps}k",
                "-maxrate", f"{video_kbps}k",
                "-bufsize", f"{video_kbps * 2}k",
                str(output),
            ]
        )

    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("Video conversion failed.")
    if output.stat().st_size > MAX_TELEGRAM_BYTES:
        raise RuntimeError("The converted video is still too large for Telegram.")
    return output


def download_rednote(url: str, download_dir: Path) -> tuple[list[Path], dict, str]:
    resolved = resolve_share_url(url)
    analysis = {}
    try:
        analysis = _analyze_rednote_page(resolved)
        resolved = analysis.get("resolved_url") or resolved
    except Exception as exc:
        logger.info("RedNote page analysis failed: %s", exc)

    # Fast path: for video notes, download RedNote's own stream first.
    if analysis.get("has_video"):
        try:
            files, info = _download_direct_video(
                analysis.get("video_candidates") or [],
                download_dir,
                resolved,
            )
            video = prepare_video(files[0])
            info["title"] = analysis.get("title") or info.get("title") or ""
            return [video], info, "video"
        except Exception as direct_error:
            logger.info("Direct RedNote video download failed: %s", direct_error)

            # Clean up before the secondary extractor.
            for path in download_dir.iterdir():
                try:
                    if path.is_file():
                        path.unlink()
                except OSError:
                    pass

            try:
                files, info = download_video(resolved, download_dir)
                video = prepare_video(
                    files[0],
                    info.get("duration") if isinstance(info, dict) else None,
                )
                if isinstance(info, dict) and not info.get("title") and analysis.get("title"):
                    info["title"] = analysis["title"]
                return [video], info, "video"
            except Exception as ytdlp_error:
                raise RuntimeError(
                    f"This is a video post, but video download failed. "
                    f"Direct: {direct_error}. yt-dlp: {ytdlp_error}"
                ) from ytdlp_error

    # Unknown type: try yt-dlp briefly before image fallback.
    video_error = None
    try:
        files, info = download_video(resolved, download_dir)
        video = prepare_video(
            files[0],
            info.get("duration") if isinstance(info, dict) else None,
        )
        return [video], info, "video"
    except Exception as exc:
        video_error = exc
        logger.info("yt-dlp RedNote video extraction failed: %s", exc)

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
    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_VIDEO)

    async with DOWNLOAD_SEMAPHORE:
        try:
            with tempfile.TemporaryDirectory(prefix="rednote_") as tmp:
                paths, info, media_type = await asyncio.wait_for(
                    asyncio.to_thread(download_rednote, url, Path(tmp)),
                    timeout=110,
                )
                caption = clean_caption(info)

                if media_type == "video":
                    path = paths[0]
                    with path.open("rb") as file_obj:
                        await message.reply_video(
                            video=file_obj,
                            caption=caption,
                            filename="rednote_video.mp4",
                            supports_streaming=True,
                            read_timeout=300,
                            write_timeout=300,
                            connect_timeout=60,
                        )
                else:
                    for index, path in enumerate(paths):
                        with path.open("rb") as file_obj:
                            await message.reply_photo(
                                photo=file_obj,
                                caption=caption if index == 0 else None,
                                read_timeout=180,
                                write_timeout=180,
                                connect_timeout=60,
                            )
                try:
                    await status.delete()
                except BadRequest:
                    pass
        except asyncio.TimeoutError:
            logger.exception("RedNote download timed out")
            await status.edit_text(
                "RedNote did not respond in time. Please try the link again."
            )
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
