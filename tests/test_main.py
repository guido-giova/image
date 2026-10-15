import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.__main__ import (
    MediaPathRequest,
    discover_media,
    purge_expired_deleted,
    reload_media,
    resolve_media_path,
    run_web_app,
    soft_delete_media,
    toggle_favorite,
)


class DiscoverMediaTests(unittest.TestCase):
    def test_finds_supported_media_recursively_and_ignores_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "photo.JPG").write_bytes(b"image")
            (root / "notes.txt").write_text("not media", encoding="utf-8")
            nested = root / "trips"
            nested.mkdir()
            (nested / "clip.webm").write_bytes(b"video")

            items = discover_media(root)

        self.assertEqual(
            [(item.path, item.kind) for item in items],
            [("trips/clip.webm", "video"), ("photo.JPG", "image")],
        )
        self.assertIsNone(items[0].metadata)

    def test_loads_metadata_from_same_stem_json_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "photo.png").write_bytes(b"image")
            (root / "photo.json").write_text(
                json.dumps(
                    {
                        "title": "A photo",
                        "description": "Details",
                        "downloaded_at": "2026-10-12T21:33:56Z",
                        "tags": ["tag-one", "tag-two"],
                        "source": {"site": "example.test", "id": "123"},
                    }
                ),
                encoding="utf-8",
            )

            item = discover_media(root)[0]

        self.assertIsNotNone(item.metadata)
        self.assertEqual(item.metadata.title, "A photo")
        self.assertEqual(item.metadata.description, "Details")
        self.assertFalse(item.metadata.isFavorite)
        self.assertIsNone(item.metadata.width)
        self.assertIsNone(item.metadata.height)
        self.assertEqual(item.metadata.tags, ["tag-one", "tag-two"])
        self.assertEqual(item.metadata.source.site, "example.test")
        self.assertEqual(item.metadata.source.id, "123")

    def test_ignores_files_in_the_deleted_folder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "visible.png").write_bytes(b"visible")
            deleted = root / ".deleted" / "deletion-id"
            deleted.mkdir(parents=True)
            (deleted / "hidden.png").write_bytes(b"hidden")

            items = discover_media(root)

        self.assertEqual([item.path for item in items], ["visible.png"])

    def test_resolves_files_only_inside_the_media_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "media"
            root.mkdir()
            (root / "photo.png").write_bytes(b"image")
            outside = Path(directory) / "secret.txt"
            outside.write_text("private", encoding="utf-8")

            self.assertEqual(resolve_media_path(root, "photo.png"), root / "photo.png")
            self.assertIsNone(resolve_media_path(root, "../secret.txt"))
            self.assertIsNone(resolve_media_path(root, "missing.png"))


class RefreshMediaTests(unittest.TestCase):
    def test_reload_endpoint_rescans_files_and_updates_media_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "old.png").write_bytes(b"old")
            application = SimpleNamespace(state=SimpleNamespace())
            request = SimpleNamespace(app=application)

            with patch("app.__main__.MEDIA_ROOT", root):
                asyncio.run(reload_media(request))
            (root / "old.png").unlink()
            (root / "new.jpg").write_bytes(b"new")

            with patch("app.__main__.MEDIA_ROOT", root):
                items = asyncio.run(reload_media(request))

        self.assertEqual([item.path for item in items], ["new.jpg"])
        self.assertEqual(application.state.media_items, items)
        self.assertEqual(application.state.media_paths, {"new.jpg"})


class MediaMutationTests(unittest.TestCase):
    def test_favorite_toggles_and_preserves_existing_sidecar_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "photo.png").write_bytes(b"image")
            sidecar_path = root / "photo.json"
            sidecar_path.write_text(
                json.dumps({"title": "My photo", "custom": {"keep": True}}),
                encoding="utf-8",
            )
            item = discover_media(root)[0]
            application = SimpleNamespace(
                state=SimpleNamespace(media_items=[item], media_paths={item.path})
            )
            request = SimpleNamespace(app=application)

            with patch("app.__main__.MEDIA_ROOT", root):
                favorite = asyncio.run(
                    toggle_favorite(MediaPathRequest(path=item.path), request)
                )
                unfavorite = asyncio.run(
                    toggle_favorite(MediaPathRequest(path=item.path), request)
                )

            saved_metadata = json.loads(sidecar_path.read_text(encoding="utf-8"))

        self.assertTrue(favorite.metadata.isFavorite)
        self.assertFalse(unfavorite.metadata.isFavorite)
        self.assertEqual(saved_metadata["title"], "My photo")
        self.assertEqual(saved_metadata["custom"], {"keep": True})
        self.assertFalse(saved_metadata["isFavorite"])

    def test_favorite_creates_a_default_sidecar_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "photo.png").write_bytes(b"image")
            item = discover_media(root)[0]
            application = SimpleNamespace(
                state=SimpleNamespace(media_items=[item], media_paths={item.path})
            )
            request = SimpleNamespace(app=application)

            with patch("app.__main__.MEDIA_ROOT", root):
                updated_item = asyncio.run(
                    toggle_favorite(MediaPathRequest(path=item.path), request)
                )

            metadata_document = json.loads(
                (root / "photo.json").read_text(encoding="utf-8")
            )

        self.assertTrue(updated_item.metadata.isFavorite)
        self.assertEqual(metadata_document["isFavorite"], True)
        self.assertEqual(metadata_document["title"], "")
        self.assertEqual(metadata_document["tags"], [])

    def test_soft_delete_keeps_same_named_files_in_separate_expiring_folders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            media_paths = ["first/photo.png", "second/photo.png"]
            for path in media_paths:
                media_path = root / path
                media_path.parent.mkdir(parents=True)
                media_path.write_bytes(path.encode("utf-8"))
                media_path.with_suffix(".json").write_text(
                    json.dumps({"title": path}),
                    encoding="utf-8",
                )
            items = discover_media(root)
            application = SimpleNamespace(
                state=SimpleNamespace(
                    media_items=items,
                    media_paths={item.path for item in items},
                )
            )
            request = SimpleNamespace(app=application)

            with patch("app.__main__.MEDIA_ROOT", root):
                results = [
                    asyncio.run(soft_delete_media(MediaPathRequest(path=path), request))
                    for path in media_paths
                ]

            deleted_root = root / ".deleted"
            deletion_folders = list(deleted_root.iterdir())
            retained = [
                (media_path, media_path.with_suffix(".json"))
                for media_path in deleted_root.rglob("photo.png")
            ]
            retained_titles = [
                json.loads(sidecar.read_text(encoding="utf-8"))["title"]
                for _, sidecar in retained
            ]
            retained_media_exists = all(media.is_file() for media, _ in retained)
            deletion_folder_names = [folder.name for folder in deletion_folders]

        self.assertEqual(len(deletion_folder_names), 2)
        self.assertNotEqual(deletion_folder_names[0], deletion_folder_names[1])
        self.assertTrue(retained_media_exists)
        self.assertEqual(set(retained_titles), set(media_paths))
        self.assertEqual(application.state.media_items, [])
        self.assertEqual(application.state.media_paths, set())
        self.assertTrue(
            all(
                timedelta(days=29) < result.expiresAt - datetime.now(timezone.utc)
                <= timedelta(days=30)
                for result in results
            )
        )

    def test_purges_only_expired_app_deleted_folders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deleted_root = root / ".deleted"
            deleted_root.mkdir()
            now = datetime.now(timezone.utc)
            expired_name = (
                (now - timedelta(days=31)).strftime("%Y%m%dT%H%M%S%fZ")
                + "_"
                + "a" * 32
            )
            retained_name = (
                (now - timedelta(days=29)).strftime("%Y%m%dT%H%M%S%fZ")
                + "_"
                + "b" * 32
            )
            expired_folder = deleted_root / expired_name
            retained_folder = deleted_root / retained_name
            expired_folder.mkdir()
            retained_folder.mkdir()
            (deleted_root / "manual-folder").mkdir()

            purge_expired_deleted(root, now)

            self.assertFalse(expired_folder.exists())
            self.assertTrue(retained_folder.exists())
            self.assertTrue((deleted_root / "manual-folder").exists())


class RunWebAppTests(unittest.TestCase):
    @patch("uvicorn.run")
    @patch("threading.Timer")
    def test_starts_correct_app_and_opens_local_gallery(self, timer_class, uvicorn_run) -> None:
        run_web_app(host="127.0.0.1", port=12345)

        timer_class.assert_called_once()
        timer_args, timer_kwargs = timer_class.call_args
        self.assertEqual(timer_args[0], 1.0)
        self.assertEqual(timer_kwargs["args"], ("http://127.0.0.1:12345",))
        timer_class.return_value.start.assert_called_once_with()
        self.assertTrue(timer_class.return_value.daemon)
        uvicorn_run.assert_called_once_with(
            "app.__main__:app",
            host="127.0.0.1",
            port=12345,
        )

    @patch("uvicorn.run")
    @patch("threading.Timer")
    def test_can_disable_browser_opening(self, timer_class, uvicorn_run) -> None:
        run_web_app(port=12345, open_browser=False)

        timer_class.assert_not_called()
        uvicorn_run.assert_called_once_with(
            "app.__main__:app",
            host="127.0.0.1",
            port=12345,
        )


if __name__ == "__main__":
    unittest.main()
