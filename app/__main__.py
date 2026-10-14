from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path, PurePosixPath
from typing import AsyncGenerator, Literal
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = PROJECT_ROOT / "app"
MEDIA_ROOT = PROJECT_ROOT / "_files"
WEB_ROOT = APP_ROOT / "web"
STATIC_ROOT = WEB_ROOT / "static"

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


class MediaItem(BaseModel):
    name: str
    path: str
    kind: Literal["image", "video"]
    url: str


def discover_media(media_root: Path) -> list[MediaItem]:
    root = media_root.resolve()
    items: list[MediaItem] = []

    if not root.is_dir():
        return items

    for file_path in root.rglob("*"):
        if file_path.is_symlink() or not file_path.is_file():
            continue

        try:
            relative_path = file_path.resolve().relative_to(root).as_posix()
        except ValueError:
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
            )
        )

    return sorted(items, key=lambda item: (item.name.casefold(), item.path.casefold()))


def refresh_media(application: FastAPI, media_root: Path) -> list[MediaItem]:
    items = discover_media(media_root)
    application.state.media_items = items
    application.state.media_paths = {item.path for item in items}
    return items


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


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    refresh_media(application, MEDIA_ROOT)
    yield


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


@app.get("/media/{relative_path:path}")
async def serve_media(relative_path: str, request: Request) -> FileResponse:
    if relative_path not in request.app.state.media_paths:
        raise HTTPException(status_code=404, detail="Media file not found")

    media_path = resolve_media_path(MEDIA_ROOT, relative_path)
    if media_path is None:
        raise HTTPException(status_code=404, detail="Media file not found")

    return FileResponse(media_path)


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