from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
import shutil
import subprocess
from pathlib import Path, PurePosixPath
import tempfile
from typing import AsyncGenerator, Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from app.storage.thumbnail import (
    generate_thumbnail_assets,
    get_thumbnail_path,
    prune_thumbnail_cache,
    remove_thumbnail_assets,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
MEDIA_ROOT = PROJECT_ROOT / "_files"
WEB_ROOT = APP_ROOT / "web"
STATIC_ROOT = WEB_ROOT / "static"
DELETED_RETENTION = timedelta(days=30)
DELETION_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S%fZ"
DELETED_CLEANUP_INTERVAL_SECONDS = 60
logger = logging.getLogger(__name__)

MEDIA_TYPES: dict[str, Literal["image", "video"]] = {
    ".avif": "image",
    ".gif": "image",
    ".jpeg": "image",
    ".jpg": "image",
    ".png": "image",
    ".webp": "image",
    ".mp4": "video",
    ".ogv": "video",
    ".ogg": "video",
    ".webm": "video",
}


class MediaSource(BaseModel):
    site: str | None = None
    id: str | int | None = None


class MediaMetadata(BaseModel):
    title: str = ""
    description: str = ""
    isFavorite: bool = False
    downloaded_at: str | None = None
    width: int | None = None
    height: int | None = None
    tags: list[str] = Field(default_factory=list)
    source: MediaSource | None = None


class MediaItem(BaseModel):
    name: str
    path: str
    kind: Literal["image", "video"]
    url: str
    thumbnail_url: str | None = None
    preview_url: str | None = None
    metadata: MediaMetadata | None = None


class MediaPathRequest(BaseModel):
    path: str


class DeleteResult(BaseModel):
    path: str
    expiresAt: datetime


def load_media_metadata(root: Path, file_path: Path) -> MediaMetadata | None:
    sidecar_path = file_path.with_suffix(".json")
    try:
        resolved_path = sidecar_path.resolve(strict=True)
        resolved_path.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return None

    if not resolved_path.is_file():
        return None

    return MediaMetadata.model_validate_json(resolved_path.read_text(encoding="utf-8"))


def discover_media(media_root: Path) -> list[MediaItem]:
    root = media_root.resolve()
    items: list[MediaItem] = []

    if not root.is_dir():
        return items

    for file_path in root.rglob("*"):
        if file_path.is_symlink() or not file_path.is_file():
            continue

        try:
            relative_path = file_path.relative_to(root).as_posix()
            file_path.resolve().relative_to(root)
        except ValueError:
            continue

        if PurePosixPath(relative_path).parts[0] in {".deleted", ".thumbnails"}:
            continue

        kind = MEDIA_TYPES.get(file_path.suffix.lower())
        if kind is None:
            continue

        items.append(
            MediaItem(
                name=file_path.name,
                path=relative_path,
                kind=kind,
                url=f"/media/{quote(relative_path, safe='/')}",
                metadata=load_media_metadata(root, file_path),
            )
        )

    return sorted(items, key=lambda item: (item.name.casefold(), item.path.casefold()))


def refresh_media(application: FastAPI, media_root: Path) -> list[MediaItem]:
    items: list[MediaItem] = []
    media_kinds: dict[str, Literal["image", "video"]] = {}
    for item in discover_media(media_root):
        media_kinds[item.path] = item.kind
        try:
            still_path, video_path = generate_thumbnail_assets(
                media_root,
                item.path,
                item.kind,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            logger.exception("Unable to create thumbnails for %s", item.path)
            still_path = None
            video_path = None

        encoded_path = quote(item.path, safe="/")
        items.append(
            item.model_copy(
                update={
                    "thumbnail_url": (
                        f"/thumbnail/{encoded_path}?variant=still"
                        if still_path is not None and still_path.is_file()
                        else None
                    ),
                    "preview_url": (
                        f"/thumbnail/{encoded_path}?variant=video"
                        if video_path is not None and video_path.is_file()
                        else None
                    ),
                }
            )
        )

    try:
        prune_thumbnail_cache(media_root, media_kinds)
    except (OSError, RuntimeError, ValueError):
        logger.exception("Unable to prune the thumbnail cache")

    application.state.media_items = items
    application.state.media_paths = {item.path for item in items}
    return items


def purge_expired_deleted(media_root: Path, now: datetime | None = None) -> None:
    deleted_root = media_root / ".deleted"
    if deleted_root.is_symlink():
        raise OSError("The deleted-media folder cannot be a symlink")
    if not deleted_root.is_dir():
        return
    try:
        deleted_root.resolve(strict=True).relative_to(media_root.resolve())
    except (OSError, RuntimeError, ValueError) as error:
        raise OSError("The deleted-media folder must remain inside the media folder") from error

    current_time = now or datetime.now(timezone.utc)
    for entry in deleted_root.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue

        timestamp, separator, unique_id = entry.name.partition("_")
        if not separator:
            continue
        try:
            deleted_at = datetime.strptime(timestamp, DELETION_TIMESTAMP_FORMAT).replace(
                tzinfo=timezone.utc
            )
            UUID(hex=unique_id)
        except ValueError:
            continue

        if deleted_at + DELETED_RETENTION <= current_time:
            shutil.rmtree(entry)


def resolve_media_path(media_root: Path, relative_path: str) -> Path | None:
    if not relative_path or "\\" in relative_path or "\0" in relative_path:
        return None

    posix_path = PurePosixPath(relative_path)
    if posix_path.is_absolute() or any(part in {".", ".."} for part in posix_path.parts):
        return None

    root = media_root.resolve()
    candidate = root.joinpath(*posix_path.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None

    try:
        resolved.relative_to(root)
    except ValueError:
        return None

    return resolved if resolved.is_file() else None


def get_indexed_media(request: Request, relative_path: str) -> tuple[Path, MediaItem]:
    items = request.app.state.media_items
    item = next((item for item in items if item.path == relative_path), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Media file not found")

    media_path = resolve_media_path(MEDIA_ROOT, relative_path)
    if media_path is None:
        raise HTTPException(status_code=404, detail="Media file not found")
    return media_path, item


def get_sidecar_path(media_root: Path, media_path: Path) -> Path:
    sidecar_path = media_path.with_suffix(".json")
    if sidecar_path.is_symlink():
        raise HTTPException(status_code=400, detail="Media metadata sidecar cannot be a symlink")
    try:
        sidecar_path.resolve(strict=False).relative_to(media_root.resolve())
    except (OSError, RuntimeError, ValueError):
        raise HTTPException(status_code=400, detail="Media metadata sidecar is outside the media folder")
    if sidecar_path.exists() and not sidecar_path.is_file():
        raise HTTPException(status_code=400, detail="Media metadata sidecar is not a file")
    return sidecar_path


def write_json_atomically(file_path: Path, value: dict[str, object]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=file_path.parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(value, temporary_file, ensure_ascii=False, indent=2)
            temporary_file.write("\n")
        temporary_path.replace(file_path)
    except OSError as error:
        raise HTTPException(status_code=500, detail="Unable to write media metadata") from error
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def sidecar_is_shared(
    request: Request,
    relative_path: str,
    sidecar_path: Path,
) -> bool:
    sidecar_identity = str(sidecar_path.resolve()).casefold()
    return any(
        item.path != relative_path
        and str((MEDIA_ROOT / item.path).with_suffix(".json").resolve()).casefold()
        == sidecar_identity
        for item in request.app.state.media_items
    )


async def purge_deleted_periodically(media_root: Path) -> None:
    while True:
        await asyncio.sleep(DELETED_CLEANUP_INTERVAL_SECONDS)
        try:
            purge_expired_deleted(media_root)
        except OSError:
            logger.exception("Unable to purge expired media from the deleted folder")


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    purge_expired_deleted(MEDIA_ROOT)
    refresh_media(application, MEDIA_ROOT)
    purge_task = asyncio.create_task(purge_deleted_periodically(MEDIA_ROOT))
    try:
        yield
    finally:
        purge_task.cancel()
        try:
            await purge_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Local Media Gallery", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/", response_class=FileResponse)
async def gallery_page() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/api/media", response_model=list[MediaItem])
async def list_media(request: Request) -> list[MediaItem]:
    return request.app.state.media_items


@app.post("/api/media/reload", response_model=list[MediaItem])
async def reload_media(request: Request) -> list[MediaItem]:
    return refresh_media(request.app, MEDIA_ROOT)


@app.patch("/api/media/favorite", response_model=MediaItem)
async def toggle_favorite(payload: MediaPathRequest, request: Request) -> MediaItem:
    media_path, item = get_indexed_media(request, payload.path)
    sidecar_path = get_sidecar_path(MEDIA_ROOT, media_path)

    if sidecar_path.exists():
        try:
            metadata_document = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise HTTPException(status_code=422, detail="Media metadata JSON is invalid") from error
        if not isinstance(metadata_document, dict):
            raise HTTPException(status_code=422, detail="Media metadata JSON must be an object")
    else:
        metadata_document = MediaMetadata().model_dump(mode="json")

    current_value = metadata_document.get("isFavorite", False)
    if not isinstance(current_value, bool):
        raise HTTPException(status_code=422, detail="Media metadata isFavorite must be a boolean")
    metadata_document["isFavorite"] = not current_value

    try:
        updated_metadata = MediaMetadata.model_validate(metadata_document)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail="Media metadata fields are invalid") from error

    write_json_atomically(sidecar_path, metadata_document)
    updated_item = item.model_copy(update={"metadata": updated_metadata})
    item_index = request.app.state.media_items.index(item)
    request.app.state.media_items[item_index] = updated_item
    return updated_item


@app.post("/api/media/delete", response_model=DeleteResult)
async def soft_delete_media(payload: MediaPathRequest, request: Request) -> DeleteResult:
    media_path, _ = get_indexed_media(request, payload.path)
    sidecar_path = get_sidecar_path(MEDIA_ROOT, media_path)
    move_sidecar = sidecar_path.is_file() and not sidecar_is_shared(
        request, payload.path, sidecar_path
    )
    deleted_at = datetime.now(timezone.utc)
    deletion_id = f"{deleted_at.strftime(DELETION_TIMESTAMP_FORMAT)}_{uuid4().hex}"
    deleted_root = MEDIA_ROOT / ".deleted"
    deletion_root = deleted_root / deletion_id
    try:
        if deleted_root.is_symlink():
            raise OSError("The deleted-media folder cannot be a symlink")
        deleted_root.mkdir(exist_ok=True)
        deleted_root.resolve(strict=True).relative_to(MEDIA_ROOT.resolve())
        deletion_root.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=500, detail="Unable to prepare deleted-media folder") from error

    relative_path = PurePosixPath(payload.path)
    deleted_media_path = deletion_root.joinpath(*relative_path.parts)
    deleted_sidecar_path = deleted_media_path.with_suffix(".json")
    moved_sidecar = False

    try:
        deleted_media_path.parent.mkdir(parents=True, exist_ok=True)
        if move_sidecar:
            sidecar_path.rename(deleted_sidecar_path)
            moved_sidecar = True
        media_path.rename(deleted_media_path)
    except OSError as error:
        try:
            if moved_sidecar:
                deleted_sidecar_path.rename(sidecar_path)
            shutil.rmtree(deletion_root)
        except OSError as rollback_error:
            raise HTTPException(
                status_code=500,
                detail="Unable to move media and restore its metadata sidecar",
            ) from rollback_error
        raise HTTPException(status_code=500, detail="Unable to move media to the deleted folder") from error

    request.app.state.media_items = [
        item for item in request.app.state.media_items if item.path != payload.path
    ]
    request.app.state.media_paths.discard(payload.path)
    try:
        remove_thumbnail_assets(MEDIA_ROOT, payload.path)
    except (OSError, RuntimeError, ValueError):
        logger.exception("Unable to remove thumbnails for deleted media %s", payload.path)
    return DeleteResult(path=payload.path, expiresAt=deleted_at + DELETED_RETENTION)


@app.get("/media/{relative_path:path}")
async def serve_media(relative_path: str, request: Request) -> FileResponse:
    if relative_path not in request.app.state.media_paths:
        raise HTTPException(status_code=404, detail="Media file not found")

    media_path = resolve_media_path(MEDIA_ROOT, relative_path)
    if media_path is None:
        raise HTTPException(status_code=404, detail="Media file not found")

    return FileResponse(media_path)


@app.get("/thumbnail/{relative_path:path}")
async def serve_thumbnail(
    relative_path: str,
    variant: Literal["still", "video"],
    request: Request,
) -> FileResponse:
    if relative_path not in request.app.state.media_paths:
        raise HTTPException(status_code=404, detail="Media file not found")

    media_item = next(
        item for item in request.app.state.media_items if item.path == relative_path
    )
    if variant == "video" and media_item.kind != "video":
        raise HTTPException(status_code=404, detail="Video preview not found")

    try:
        thumbnail_path = get_thumbnail_path(MEDIA_ROOT, relative_path, variant)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Thumbnail not found") from error
    if not thumbnail_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(thumbnail_path)


def run_web_app(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> None:
    import threading
    import uvicorn
    import webbrowser

    if open_browser:
        browser_timer = threading.Timer(
            1.0,
            webbrowser.open,
            args=(f"http://{host}:{port}",),
        )
        browser_timer.daemon = True
        browser_timer.start()

    uvicorn.run("app.__main__:app", host=host, port=port)


if __name__ == "__main__":
    run_web_app()