from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Literal

import imageio_ffmpeg
from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

ThumbnailVariant = Literal["still", "video"]
IMAGE_MAX_EDGE = 480
VIDEO_MAX_WIDTH = 854
VIDEO_MAX_HEIGHT = 480
IMAGE_QUALITY = 75
VIDEO_CRF = 30


def _relative_media_path(relative_path: str) -> PurePosixPath:
    if not relative_path or "\\" in relative_path or "\0" in relative_path:
        raise ValueError("Media path must be a non-empty POSIX relative path")

    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Media path must remain inside the media folder")
    if path.parts[0] in {".deleted", ".thumbnails"}:
        raise ValueError("Media path cannot refer to an internal folder")
    return path


def _thumbnail_root(media_root: Path, *, create: bool) -> Path:
    root = media_root.resolve()
    thumbnail_root = root / ".thumbnails"
    if thumbnail_root.is_symlink():
        raise OSError("The thumbnail folder cannot be a symlink")
    if create:
        thumbnail_root.mkdir(exist_ok=True)
    if not thumbnail_root.is_dir():
        raise FileNotFoundError("The thumbnail folder does not exist")

    resolved_root = thumbnail_root.resolve(strict=True)
    try:
        resolved_root.relative_to(root)
    except ValueError as error:
        raise OSError("The thumbnail folder must remain inside the media folder") from error
    return resolved_root


def _asset_paths(
    media_root: Path,
    relative_path: str,
    *,
    create_parent: bool,
) -> tuple[Path, Path, Path]:
    media_path = _relative_media_path(relative_path)
    thumbnail_root = _thumbnail_root(media_root, create=create_parent)
    base_path = thumbnail_root.joinpath(*media_path.parts)
    if create_parent:
        base_path.parent.mkdir(parents=True, exist_ok=True)

    resolved_parent = base_path.parent.resolve(strict=create_parent)
    try:
        resolved_parent.relative_to(thumbnail_root)
    except ValueError as error:
        raise OSError("Thumbnail paths must remain inside the thumbnail folder") from error
    if any(parent.is_symlink() for parent in base_path.parents if parent != thumbnail_root.parent):
        raise OSError("Thumbnail paths cannot include symlinks")

    return (
        resolved_parent / f"{base_path.name}.jpg",
        resolved_parent / f"{base_path.name}.mp4",
        resolved_parent / f"{base_path.name}.json",
    )


def get_thumbnail_path(
    media_root: Path,
    relative_path: str,
    variant: ThumbnailVariant,
) -> Path:
    still_path, video_path, _ = _asset_paths(
        media_root,
        relative_path,
        create_parent=False,
    )
    thumbnail_path = still_path if variant == "still" else video_path
    if thumbnail_path.is_symlink():
        raise OSError("Thumbnail assets cannot be symlinks")
    if thumbnail_path.exists():
        resolved_path = thumbnail_path.resolve(strict=True)
        try:
            resolved_path.relative_to(_thumbnail_root(media_root, create=False))
        except ValueError as error:
            raise OSError("Thumbnail assets must remain inside the thumbnail folder") from error
        return resolved_path
    return thumbnail_path


def _temporary_path(target: Path) -> Path:
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}-",
        suffix=target.suffix,
        dir=target.parent,
    )
    os.close(file_descriptor)
    return Path(temporary_name)


def _source_signature(source_path: Path) -> dict[str, int]:
    source_stat = source_path.stat()
    return {"size": source_stat.st_size, "mtime_ns": source_stat.st_mtime_ns}


def _valid_cache(
    manifest_path: Path,
    still_path: Path,
    video_path: Path,
    signature: dict[str, int],
    kind: Literal["image", "video"],
) -> bool:
    if not still_path.is_file() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(manifest, dict)
        and manifest.get("source") == signature
        and manifest.get("kind") == kind
        and isinstance(manifest.get("video"), bool)
        and (
            (kind == "image" and not manifest["video"])
            or (kind == "video" and (not manifest["video"] or video_path.is_file()))
        )
    )


def _save_still_image(source_path: Path, output_path: Path) -> None:
    with Image.open(source_path) as source:
        frame = ImageOps.exif_transpose(source)
        frame.seek(0)
        frame.thumbnail(
            (IMAGE_MAX_EDGE, IMAGE_MAX_EDGE),
            Image.Resampling.LANCZOS,
        )
        rgba = frame.convert("RGBA")
        image = Image.new("RGB", rgba.size, "white")
        image.paste(rgba, mask=rgba.getchannel("A"))

        temporary_path = _temporary_path(output_path)
        try:
            image.save(temporary_path, format="JPEG", quality=IMAGE_QUALITY, optimize=True)
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)


def _ffmpeg_scale_filter(max_width: int, max_height: int) -> str:
    return (
        f"scale=w='min({max_width},iw)':h='min({max_height},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )


def _run_ffmpeg(
    ffmpeg_executable: str,
    arguments: list[str],
    output_path: Path,
) -> None:
    subprocess.run(
        [ffmpeg_executable, "-hide_banner", "-loglevel", "error", *arguments],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise OSError(f"FFmpeg did not create {output_path.name}")


def _save_video_still(
    source_path: Path,
    output_path: Path,
    ffmpeg_executable: str,
) -> None:
    temporary_path = _temporary_path(output_path)
    try:
        for seek_seconds in ("1", "0"):
            temporary_path.unlink(missing_ok=True)
            try:
                _run_ffmpeg(
                    ffmpeg_executable,
                    [
                        "-y",
                        "-ss",
                        seek_seconds,
                        "-i",
                        str(source_path),
                        "-frames:v",
                        "1",
                        "-vf",
                        _ffmpeg_scale_filter(IMAGE_MAX_EDGE, IMAGE_MAX_EDGE),
                        "-q:v",
                        "5",
                        "-f",
                        "image2",
                        str(temporary_path),
                    ],
                    temporary_path,
                )
            except (OSError, subprocess.CalledProcessError):
                if seek_seconds == "0":
                    raise
            else:
                os.replace(temporary_path, output_path)
                return
        raise OSError(f"Unable to extract a still image from {source_path.name}")
    finally:
        temporary_path.unlink(missing_ok=True)


def _save_video_preview(
    source_path: Path,
    output_path: Path,
    ffmpeg_executable: str,
) -> None:
    temporary_path = _temporary_path(output_path)
    try:
        _run_ffmpeg(
            ffmpeg_executable,
            [
                "-y",
                "-i",
                str(source_path),
                "-map",
                "0:v:0",
                "-vf",
                _ffmpeg_scale_filter(VIDEO_MAX_WIDTH, VIDEO_MAX_HEIGHT),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                str(VIDEO_CRF),
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(temporary_path),
            ],
            temporary_path,
        )
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def generate_thumbnail_assets(
    media_root: Path,
    relative_path: str,
    kind: Literal["image", "video"],
) -> tuple[Path, Path | None]:
    media_relative_path = _relative_media_path(relative_path)
    root = media_root.resolve()
    source_path = root.joinpath(*media_relative_path.parts)
    if source_path.is_symlink():
        raise OSError("Media files cannot be symlinks")
    resolved_source = source_path.resolve(strict=True)
    try:
        resolved_source.relative_to(root)
    except ValueError as error:
        raise OSError("Media paths must remain inside the media folder") from error
    if not resolved_source.is_file():
        raise FileNotFoundError(f"Media file not found: {relative_path}")

    still_path, video_path, manifest_path = _asset_paths(
        root,
        relative_path,
        create_parent=True,
    )
    if any(path.is_symlink() for path in (still_path, video_path, manifest_path)):
        raise OSError("Thumbnail assets cannot be symlinks")
    signature = _source_signature(resolved_source)
    if _valid_cache(manifest_path, still_path, video_path, signature, kind):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return still_path, video_path if manifest["video"] else None

    video_available = False
    if kind == "image":
        _save_still_image(resolved_source, still_path)
    else:
        ffmpeg_executable = imageio_ffmpeg.get_ffmpeg_exe()
        _save_video_still(resolved_source, still_path, ffmpeg_executable)
        try:
            _save_video_preview(resolved_source, video_path, ffmpeg_executable)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            logger.exception("Unable to create video preview for %s", relative_path)
            video_path.unlink(missing_ok=True)
        else:
            video_available = True

    manifest = {
        "source": signature,
        "kind": kind,
        "video": video_available,
    }
    temporary_manifest_path = _temporary_path(manifest_path)
    try:
        temporary_manifest_path.write_text(
            json.dumps(manifest, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary_manifest_path, manifest_path)
    finally:
        temporary_manifest_path.unlink(missing_ok=True)

    return still_path, video_path if video_available else None


def remove_thumbnail_assets(media_root: Path, relative_path: str) -> None:
    thumbnail_root = media_root.resolve() / ".thumbnails"
    if thumbnail_root.is_symlink():
        raise OSError("The thumbnail folder cannot be a symlink")
    if not thumbnail_root.exists():
        return
    still_path, video_path, manifest_path = _asset_paths(
        media_root,
        relative_path,
        create_parent=False,
    )
    for asset_path in (still_path, video_path, manifest_path):
        if asset_path.is_symlink():
            raise OSError("Thumbnail assets cannot be symlinks")
        asset_path.unlink(missing_ok=True)

    thumbnail_root = _thumbnail_root(media_root, create=False)
    parent = still_path.parent
    while parent != thumbnail_root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def prune_thumbnail_cache(
    media_root: Path,
    media: dict[str, Literal["image", "video"]],
) -> None:
    thumbnail_root = _thumbnail_root(media_root, create=True)
    expected_paths: set[Path] = set()
    for relative_path, kind in media.items():
        still_path, video_path, manifest_path = _asset_paths(
            media_root,
            relative_path,
            create_parent=False,
        )
        expected_paths.update((still_path, manifest_path))
        if kind == "video":
            expected_paths.add(video_path)

    for asset_path in thumbnail_root.rglob("*"):
        if asset_path.is_symlink():
            raise OSError("Thumbnail cache cannot contain symlinks")
        if asset_path.is_file() and asset_path.suffix in {".jpg", ".mp4", ".json"}:
            if asset_path not in expected_paths:
                asset_path.unlink()

    for directory in sorted(
        (path for path in thumbnail_root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    ):
        try:
            directory.rmdir()
        except OSError:
            pass
