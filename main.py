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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
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
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "").strip().rstrip("/")
LOCAL_BOT_API_ENABLED = bool(LOCAL_BOT_API_URL)
MAX_TELEGRAM_BYTES = (
    1900 * 1024 * 1024 if LOCAL_BOT_API_ENABLED else 47 * 1024 * 1024
)
MAX_DIRECT_VIDEO_BYTES = (
    1900 * 1024 * 1024 if LOCAL_BOT_API_ENABLED else 45 * 1024 * 1024
)
MAX_IMAGE_BYTES = (100 * 1024 * 1024 if LOCAL_BOT_API_ENABLED else 20 * 1024 * 1024)
MAX_IMAGES = 20
DOWNLOAD_CONCURRENCY = max(1, int(os.getenv("DOWNLOAD_CONCURRENCY", "2")))

REDNOTE_HOSTS = (
    "rednote.com",
    "xiaohongshu.com",
    "xhslink.com",
    "xhslink.cn",
)
PINTEREST_HOSTS = (
    "pinterest.com",
    "pin.it",
)
ALLOWED_HOSTS = REDNOTE_HOSTS + PINTEREST_HOSTS
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/153.0.0.0 Safari/537.36"
)

HTTP_SESSION = requests.Session()
HTTP_ADAPTER = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
HTTP_SESSION.mount("https://", HTTP_ADAPTER)
HTTP_SESSION.mount("http://", HTTP_ADAPTER)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("rednote-bot")


def _is_allowed_host(host: str) -> bool:
    host = (host or "").lower().split(":")[0]
    return any(host == root or host.endswith("." + root) for root in ALLOWED_HOSTS)


def _platform_for_url(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower().split(":")[0]
    if any(host == root or host.endswith("." + root) for root in REDNOTE_HOSTS):
        return "rednote"
    if any(host == root or host.endswith("." + root) for root in PINTEREST_HOSTS):
        return "pinterest"
    return None


def extract_supported_url(text: str) -> str | None:
    for match in URL_RE.findall(text or ""):
        candidate = match.rstrip(").,]}>\"'")
        try:
            parsed = urlparse(candidate)
        except ValueError:
            continue
        if parsed.scheme in {"http", "https"} and _platform_for_url(candidate):
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
    needs_redirect = "xhslink." in host or host == "pin.it" or host.endswith(".pin.it")
    if not needs_redirect:
        return url

    response = HTTP_SESSION.get(
        url,
        headers=_request_headers(),
        timeout=(6, 12),
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
    response = HTTP_SESSION.get(
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
            try:
                bitrate = int(item.get("avgBitrate") or item.get("videoBitrate") or 0)
            except (TypeError, ValueError):
                bitrate = 0
            for media_url in urls:
                video_candidates.append({
                    "url": media_url,
                    "size": size,
                    "height": height,
                    "bitrate": bitrate,
                    "video_codec": str(item.get("videoCodec") or "").lower(),
                    "audio_codec": str(item.get("audioCodec") or "").lower(),
                    "quality": str(item.get("qualityType") or ""),
                    "origin": False,
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
                "height": 0,
                "bitrate": 0,
                "video_codec": "",
                "audio_codec": "",
                "quality": "origin",
                "origin": True,
            })

    # Prefer native H.264 streams that already fit Telegram. Unknown-size streams
    # are checked from HTTP headers before downloading; the origin URL is last.
    def candidate_rank(item: dict) -> tuple:
        size = item.get("size") or 0
        codec = (item.get("video_codec") or "").lower()
        if item.get("origin"):
            group = 4
        elif 0 < size <= MAX_DIRECT_VIDEO_BYTES and codec in {"h264", "avc", "avc1", ""}:
            group = 0
        elif size == 0 and codec in {"h264", "avc", "avc1", ""}:
            group = 1
        elif 0 < size <= MAX_DIRECT_VIDEO_BYTES:
            group = 2
        else:
            group = 3
        return (
            group,
            -(item.get("height", 0) or 0),
            -(item.get("bitrate", 0) or 0),
        )

    video_candidates.sort(key=candidate_rank)

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
    safe_limit = MAX_DIRECT_VIDEO_BYTES

    for index, candidate in enumerate(candidates[:12], start=1):
        media_url = candidate.get("url")
        if not media_url:
            continue

        declared_size = int(candidate.get("size") or 0)
        if declared_size > safe_limit:
            errors.append(f"candidate {index} skipped: {declared_size} bytes")
            continue

        try:
            response = HTTP_SESSION.get(
                media_url,
                headers=_request_headers(referer),
                timeout=(8, 18),
                stream=True,
                allow_redirects=True,
            )
            response.raise_for_status()

            content_type = (response.headers.get("Content-Type") or "").lower()
            if "text/html" in content_type or "application/json" in content_type:
                raise RuntimeError(f"Unexpected media response: {content_type}")

            try:
                header_size = int(response.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                header_size = 0

            if header_size > safe_limit:
                response.close()
                errors.append(f"candidate {index} too large from header: {header_size} bytes")
                continue

            path = download_dir / f"rednote_direct_{index}.mp4"
            downloaded = 0
            too_large = False
            with path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=4 * 1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > safe_limit:
                        too_large = True
                        break
                    output.write(chunk)
            response.close()

            if too_large:
                path.unlink(missing_ok=True)
                errors.append(f"candidate {index} exceeded Telegram-safe size")
                continue
            if not path.exists() or path.stat().st_size < 1024:
                path.unlink(missing_ok=True)
                raise RuntimeError("Downloaded video is empty.")

            declared_codec = str(candidate.get("video_codec") or "").lower()
            declared_audio = str(candidate.get("audio_codec") or "").lower() or None
            if declared_codec in {"h264", "avc", "avc1"}:
                video_codec = "h264"
                audio_codec = declared_audio
            else:
                _, video_codec, audio_codec = _video_stream_info(path)

            if video_codec not in {"h264", "avc1"}:
                path.unlink(missing_ok=True)
                errors.append(f"candidate {index} skipped: codec={video_codec}")
                continue

            return [path], {
                "title": "",
                "webpage_url": referer,
                "duration": None,
                "source_size": path.stat().st_size,
                "video_codec": video_codec,
                "audio_codec": audio_codec,
            }
        except Exception as exc:
            errors.append(str(exc))
            logger.info("Direct RedNote video candidate failed: %s", exc)

    raise RuntimeError("No Telegram-safe H.264 RedNote stream found: " + " | ".join(errors[-5:]))


def _ydl_options(download_dir: Path) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": "https://www.rednote.com/",
    }
    if REDNOTE_COOKIE:
        headers["Cookie"] = REDNOTE_COOKIE

    return {
        "format": "best[vcodec^=avc][filesize<45M]/best[vcodec^=avc][filesize_approx<45M]/best[vcodec^=avc][height<=1080]/best[filesize<45M]/best[height<=1080]",
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


def _normalize_rednote_image_url(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    url = _decode_page_text(raw).replace("&amp;", "&").strip()
    if not url.startswith("http"):
        return ""

    # RedNote sometimes appends imageView resize operations to otherwise
    # canonical CDN URLs. Strip those so we fetch the original asset.
    url = re.sub(r"/imageView\\d+/\\d+/w/\\d+.*$", "", url)
    return url


def _note_image_candidates(note: dict) -> list[str]:
    if not isinstance(note, dict):
        return []

    result: list[str] = []
    seen: set[str] = set()

    for item in note.get("imageList") or []:
        if not isinstance(item, dict):
            continue

        candidates = []

        # Canonical full-resolution URL first.
        for key in ("urlDefault", "url", "urlPre"):
            value = item.get(key)
            if isinstance(value, str):
                candidates.append(value)

        # WB_DFT is generally the default/original display asset.
        info_list = item.get("infoList") or []
        if isinstance(info_list, list):
            wb_dft = [
                info.get("url")
                for info in info_list
                if isinstance(info, dict)
                and info.get("imageScene") == "WB_DFT"
                and isinstance(info.get("url"), str)
            ]
            other = [
                info.get("url")
                for info in info_list
                if isinstance(info, dict)
                and isinstance(info.get("url"), str)
            ]
            candidates.extend(wb_dft + other)

        chosen = ""
        for candidate in candidates:
            normalized = _normalize_rednote_image_url(candidate)
            if normalized:
                chosen = normalized
                break

        if chosen and chosen not in seen:
            seen.add(chosen)
            result.append(chosen)

    return result


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


def _save_original_image(
    data: bytes,
    content_type: str,
    output_base: Path,
) -> Path | None:
    if not data or len(data) > MAX_IMAGE_BYTES:
        return None

    if content_type in {"image/jpeg", "image/jpg"}:
        path = output_base.with_suffix(".jpg")
        path.write_bytes(data)
        return path
    if content_type == "image/png":
        path = output_base.with_suffix(".png")
        path.write_bytes(data)
        return path

    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "RGBA", "L", "LA"):
            image = image.convert("RGBA")
        path = output_base.with_suffix(".png")
        image.save(path, format="PNG", optimize=False)
        return path
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _fetch_image_candidate(args: tuple[int, str, str, Path, str]):
    index, image_url, referer, download_dir, prefix = args
    try:
        # Each worker uses its own short-lived connection to avoid Session
        # contention while still downloading carousel images concurrently.
        response = requests.get(
            image_url,
            headers=_request_headers(referer),
            timeout=(6, 20),
            allow_redirects=True,
        )
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()
        if not content_type.startswith("image/"):
            return None
        data = response.content
        digest = hashlib.sha1(data).hexdigest()
        path = _save_original_image(
            data,
            content_type,
            download_dir / f"{prefix}_{index:02d}",
        )
        if not path:
            return None
        return index, path, digest, image_url
    except requests.RequestException:
        logger.debug("Image candidate failed: %s", image_url, exc_info=True)
        return None


def download_images(
    url: str,
    download_dir: Path,
    note: dict | None = None,
    page_text: str | None = None,
) -> tuple[list[Path], dict]:
    response = None

    candidates = _note_image_candidates(note or {})

    if not candidates:
        response = HTTP_SESSION.get(
            url,
            headers=_request_headers("https://www.rednote.com/"),
            timeout=30,
            allow_redirects=True,
        )
        response.raise_for_status()
        page_text = response.text
        candidates = _image_candidates(response.text)

    if not candidates:
        raise RuntimeError("No downloadable images were found.")

    page_title = ""
    if isinstance(note, dict):
        page_title = str(note.get("title") or "").strip()

    if not page_title and page_text:
        soup = BeautifulSoup(page_text, "html.parser")
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            page_title = og_title["content"].strip()
        elif soup.title and soup.title.string:
            page_title = soup.title.string.strip()

    referer = response.url if response is not None else url
    files: list[Path] = []
    hashes: set[str] = set()

    work = [
        (index, image_url, referer, download_dir, "rednote")
        for index, image_url in enumerate(candidates[:MAX_IMAGES], start=1)
    ]
    worker_count = min(6, max(1, len(work)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        results = list(executor.map(_fetch_image_candidate, work))

    for result in results:
        if not result:
            continue
        _, path, digest, image_url = result
        if digest in hashes:
            path.unlink(missing_ok=True)
            continue
        hashes.add(digest)
        files.append(path)
        logger.info(
            "Downloaded original RedNote image: file=%s bytes=%s source=%s",
            path.name,
            path.stat().st_size,
            image_url,
        )

    if not files:
        raise RuntimeError("RedNote returned an image post, but the original images could not be downloaded.")

    return files, {"title": page_title, "webpage_url": referer}


def _probe_media(path: Path) -> dict:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration,format_name,start_time:stream=index,codec_type,codec_name,width,height,duration,start_time,time_base",
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
            timeout=40,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Video conversion timed out.") from exc
    except subprocess.CalledProcessError as exc:
        logger.error("ffmpeg failed: %s", exc.stderr.decode(errors="ignore")[-2000:])
        raise RuntimeError("Video conversion failed.") from exc


def _telegram_video_metadata(path: Path) -> tuple[int | None, int | None, int | None]:
    probe = _probe_media(path)
    duration = None
    width = None
    height = None

    try:
        value = float((probe.get("format") or {}).get("duration") or 0)
        if value > 0:
            duration = max(1, int(round(value)))
    except (TypeError, ValueError):
        pass

    for stream in probe.get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        try:
            width = int(stream.get("width") or 0) or None
        except (TypeError, ValueError):
            width = None
        try:
            height = int(stream.get("height") or 0) or None
        except (TypeError, ValueError):
            height = None
        if duration is None:
            try:
                value = float(stream.get("duration") or 0)
                if value > 0:
                    duration = max(1, int(round(value)))
            except (TypeError, ValueError):
                pass
        break

    return duration, width, height


def prepare_video(path: Path, duration_hint: float | None = None) -> Path:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are required for video delivery.")

    duration, video_codec, audio_codec = _video_stream_info(path)
    duration = duration or duration_hint
    if not video_codec:
        raise RuntimeError("Downloaded media is not a real video.")
    if path.stat().st_size > MAX_DIRECT_VIDEO_BYTES:
        raise RuntimeError(
            f"Video is larger than the configured Telegram upload limit "
            f"({MAX_DIRECT_VIDEO_BYTES} bytes)."
        )
    if video_codec not in {"h264", "avc1"}:
        raise RuntimeError(f"Video codec {video_codec} is not Telegram-native H.264.")

    output = path.with_name(path.stem + "_telegram.mp4")

    # Keep the video bit-for-bit. Rebuild MP4 timing/index metadata so Telegram
    # can detect duration, seek correctly and generate a thumbnail.
    command = [
        "ffmpeg", "-y",
        "-fflags", "+genpts",
        "-i", str(path),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "copy",
    ]

    if audio_codec in {None, "aac"}:
        command += ["-c:a", "copy"]
    else:
        command += ["-c:a", "aac", "-b:a", "128k"]

    command += [
        "-avoid_negative_ts", "make_zero",
        "-reset_timestamps", "1",
        "-movflags", "+faststart",
        "-video_track_timescale", "90000",
        str(output),
    ]

    _run_ffmpeg(command)

    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError("Video remux failed.")
    if output.stat().st_size > MAX_TELEGRAM_BYTES:
        raise RuntimeError("Remuxed video is still too large for Telegram.")

    final_duration, final_width, final_height = _telegram_video_metadata(output)
    logger.info(
        "Prepared Telegram video: size=%s duration=%s width=%s height=%s codec=%s audio=%s",
        output.stat().st_size,
        final_duration,
        final_width,
        final_height,
        video_codec,
        audio_codec,
    )
    if not final_duration:
        raise RuntimeError("Telegram MP4 has no valid duration after remux.")

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

    # Confirmed image post: skip yt-dlp entirely. This removes a slow,
    # unnecessary extractor round-trip for RedNote carousels.
    if analysis.get("has_state") and _note_image_candidates(analysis.get("note") or {}):
        files, info = download_images(
            resolved,
            download_dir,
            note=analysis.get("note"),
            page_text=analysis.get("page_text"),
        )
        return files, info, "images"

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
        files, info = download_images(
            resolved,
            download_dir,
            note=analysis.get("note") if isinstance(analysis, dict) else None,
            page_text=analysis.get("page_text") if isinstance(analysis, dict) else None,
        )
        return files, info, "images"
    except Exception as image_error:
        raise RuntimeError(
            f"Could not download this RedNote post. Video: {video_error}. Images: {image_error}"
        ) from image_error


def _pinterest_ydl_options(download_dir: Path, metadata_only: bool = False) -> dict:
    options = {
        "outtmpl": str(download_dir / "pinterest_%(id)s.%(ext)s"),
        "format": "best[vcodec^=avc]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 1,
        "fragment_retries": 1,
        "socket_timeout": 15,
        "http_headers": {
            "User-Agent": USER_AGENT,
            "Referer": "https://www.pinterest.com/",
        },
        "overwrites": True,
        "restrictfilenames": False,
        "ignore_no_formats_error": True,
    }
    if metadata_only:
        options["skip_download"] = True
    return options


def _pinterest_original_image_candidates(info: dict) -> list[str]:
    thumbnails = info.get("thumbnails") or []
    ordered = sorted(
        [t for t in thumbnails if isinstance(t, dict) and isinstance(t.get("url"), str)],
        key=lambda t: (int(t.get("width") or 0) * int(t.get("height") or 0)),
        reverse=True,
    )
    result: list[str] = []
    seen: set[str] = set()
    for thumb in ordered:
        url = thumb["url"]
        variants = [url]
        if "i.pinimg.com/" in url and "/originals/" not in url:
            original = re.sub(
                r"(https?://i\.pinimg\.com/)(?:\d+x|236x|474x|564x|736x|1200x)/",
                r"\1originals/",
                url,
                count=1,
            )
            if original != url:
                variants.insert(0, original)
        for candidate in variants:
            if candidate not in seen:
                seen.add(candidate)
                result.append(candidate)
    return result


def download_pinterest(url: str, download_dir: Path) -> tuple[list[Path], dict, str]:
    resolved = resolve_share_url(url)
    parsed = urlparse(resolved)
    if "/pin/" not in parsed.path:
        raise RuntimeError("Please send an individual Pinterest Pin link, not a board or profile.")

    # One extractor pass handles both detection and download. For image-only
    # pins yt-dlp returns metadata/thumbnails without producing a video file.
    with yt_dlp.YoutubeDL(_pinterest_ydl_options(download_dir)) as ydl:
        info = ydl.extract_info(resolved, download=True) or {}

    info["_platform"] = "Pinterest"
    formats = [
        fmt for fmt in (info.get("formats") or [])
        if isinstance(fmt, dict) and fmt.get("url")
    ]
    files = _media_files(download_dir)

    if formats and files:
        video = prepare_video(
            files[0],
            info.get("duration") if isinstance(info, dict) else None,
        )
        return [video], info, "video"

    candidates = _pinterest_original_image_candidates(info)
    if not candidates:
        raise RuntimeError("No downloadable Pinterest image was found.")

    referer = resolved

    # Try the best/original image first. Usually this completes immediately
    # and avoids waiting for lower-quality fallback URLs.
    first = _fetch_image_candidate(
        (1, candidates[0], referer, download_dir, "pinterest")
    )
    if first:
        _, path, _, _ = first
        return [path], info, "images"

    fallback = candidates[1:6]
    if fallback:
        work = [
            (index + 2, image_url, referer, download_dir, "pinterest")
            for index, image_url in enumerate(fallback)
        ]
        with ThreadPoolExecutor(max_workers=min(4, len(work))) as executor:
            results = list(executor.map(_fetch_image_candidate, work))
        for result in results:
            if result:
                _, path, _, _ = result
                return [path], info, "images"

    raise RuntimeError("Pinterest image download failed.")


def download_supported(url: str, download_dir: Path) -> tuple[list[Path], dict, str]:
    platform = _platform_for_url(url)
    if platform == "pinterest":
        return download_pinterest(url, download_dir)
    if platform == "rednote":
        files, info, media_type = download_rednote(url, download_dir)
        if isinstance(info, dict):
            info["_platform"] = "RedNote"
        return files, info, media_type
    raise RuntimeError("Unsupported link.")


def clean_caption(info: dict) -> str:
    title = ""
    platform = "RedNote"
    if isinstance(info, dict):
        title = str(info.get("title") or "").strip()
        platform = str(info.get("_platform") or platform)
    if not title:
        return f"Downloaded from {platform}"
    title = re.sub(r"\s+", " ", title)
    if len(title) > 850:
        title = title[:847] + "..."
    return f"{title}\n\nDownloaded from {platform}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message:
        return
    await update.effective_message.reply_text(
        "Send me a public RedNote or Pinterest link and I will download the video or images.\n\n"
        "Supported: RedNote, Xiaohongshu, xhslink, Pinterest and pin.it links.\n"
        "Please only download content you are allowed to save."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return

    url = extract_supported_url(message.text)
    if not url:
        await message.reply_text("Please send a valid RedNote or Pinterest link.")
        return

    status = await message.reply_text("Downloading...")
    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_VIDEO)

    async with DOWNLOAD_SEMAPHORE:
        try:
            with tempfile.TemporaryDirectory(prefix="rednote_") as tmp:
                paths, info, media_type = await asyncio.wait_for(
                    asyncio.to_thread(download_supported, url, Path(tmp)),
                    timeout=150,
                )
                caption = clean_caption(info)

                if media_type == "video":
                    path = paths[0]
                    duration, width, height = await asyncio.to_thread(
                        _telegram_video_metadata, path
                    )
                    logger.info(
                        "Sending Telegram video: size=%s duration=%s width=%s height=%s",
                        path.stat().st_size,
                        duration,
                        width,
                        height,
                    )
                    with path.open("rb") as file_obj:
                        await message.reply_video(
                            video=file_obj,
                            caption=caption,
                            filename=(
                                "pinterest_video.mp4"
                                if str(info.get("_platform") or "").lower() == "pinterest"
                                else "rednote_video.mp4"
                            ),
                            duration=duration,
                            width=width,
                            height=height,
                            supports_streaming=True,
                            read_timeout=300,
                            write_timeout=300,
                            connect_timeout=60,
                        )
                else:
                    for index, path in enumerate(paths):
                        with path.open("rb") as file_obj:
                            if LOCAL_BOT_API_ENABLED:
                                await message.reply_document(
                                    document=file_obj,
                                    filename=path.name,
                                    caption=caption if index == 0 else None,
                                    read_timeout=300,
                                    write_timeout=300,
                                    connect_timeout=60,
                                )
                            else:
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
                "The media server did not return a usable file in time. Please try again."
            )
        except (TimedOut, NetworkError):
            logger.exception("Telegram network error")
            await status.edit_text("Telegram timed out while sending the file. Please try again.")
        except Exception as exc:
            logger.exception("Download failed")
            text = str(exc)
            lowered = text.lower()
            if (
                not LOCAL_BOT_API_ENABLED
                and any(
                    marker in lowered
                    for marker in (
                        "larger than the configured telegram upload limit",
                        "telegram-safe",
                        "candidate 1 skipped",
                        "too large",
                    )
                )
            ):
                friendly = (
                    "This RedNote video is larger than Telegram's standard bot upload limit. "
                    "Large-file mode is not enabled on the server yet."
                )
            elif any(word in lowered for word in ("captcha", "risk", "login")):
                friendly = (
                    "The source site requires verification for this post. "
                    "Try another public share link."
                )
            elif "403" in lowered:
                friendly = (
                    "The source site refused one of the media CDN links for this post. "
                    "Please try the link again."
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

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(4)
    )

    if LOCAL_BOT_API_ENABLED:
        logger.info("Using local Telegram Bot API at %s", LOCAL_BOT_API_URL)
        builder = (
            builder
            .base_url(f"{LOCAL_BOT_API_URL}/bot")
            .base_file_url(f"{LOCAL_BOT_API_URL}/file/bot")
            .local_mode(True)
            .http_version("1.1")
            .get_updates_http_version("1.1")
        )

    application = builder.build()
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
