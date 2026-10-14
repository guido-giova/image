import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.__main__ import discover_media, reload_media, resolve_media_path, run_web_app


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
