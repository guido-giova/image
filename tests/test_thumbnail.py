from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from app.storage.thumbnail import (
    _save_video_preview,
    generate_thumbnail_assets,
    get_thumbnail_path,
    prune_thumbnail_cache,
    remove_thumbnail_assets,
)


class ImageThumbnailTests(unittest.TestCase):
    def test_resizes_and_caches_image_thumbnail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "photo.png"
            Image.new("RGB", (960, 480), "blue").save(source)

            still_path, video_path = generate_thumbnail_assets(root, "photo.png", "image")
            with patch("app.storage.thumbnail._save_still_image") as save_still:
                cached_still, cached_video = generate_thumbnail_assets(
                    root,
                    "photo.png",
                    "image",
                )

            with Image.open(still_path) as thumbnail:
                self.assertEqual(thumbnail.format, "JPEG")
                self.assertEqual(thumbnail.size, (480, 240))
            self.assertEqual(still_path, cached_still)
            self.assertIsNone(video_path)
            self.assertIsNone(cached_video)
            save_still.assert_not_called()
            self.assertTrue((root / ".thumbnails" / "photo.png.json").is_file())

    def test_regenerates_image_thumbnail_when_source_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "photo.png"
            Image.new("RGB", (100, 100), "blue").save(source)
            still_path, _ = generate_thumbnail_assets(root, "photo.png", "image")
            previous_mtime = still_path.stat().st_mtime_ns

            Image.new("RGB", (200, 100), "red").save(source)
            source_stat = source.stat()
            os.utime(
                source,
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns + 1_000_000),
            )
            regenerated_path, _ = generate_thumbnail_assets(root, "photo.png", "image")

            with Image.open(regenerated_path) as thumbnail:
                self.assertEqual(thumbnail.size, (200, 100))
            self.assertGreater(regenerated_path.stat().st_mtime_ns, previous_mtime)


class VideoThumbnailTests(unittest.TestCase):
    def test_generates_still_and_silent_video_and_reuses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.mp4"
            source.write_bytes(b"video source")

            def create_output(_source: Path, output: Path, _ffmpeg: str) -> None:
                output.write_bytes(b"generated")

            with (
                patch("app.storage.thumbnail.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
                patch(
                    "app.storage.thumbnail._save_video_still",
                    side_effect=create_output,
                ) as save_still,
                patch(
                    "app.storage.thumbnail._save_video_preview",
                    side_effect=create_output,
                ) as save_video,
            ):
                still_path, video_path = generate_thumbnail_assets(
                    root,
                    "clip.mp4",
                    "video",
                )
                cached_still, cached_video = generate_thumbnail_assets(
                    root,
                    "clip.mp4",
                    "video",
                )

            self.assertEqual(still_path.read_bytes(), b"generated")
            self.assertEqual(video_path.read_bytes(), b"generated")
            self.assertEqual((cached_still, cached_video), (still_path, video_path))
            save_still.assert_called_once()
            save_video.assert_called_once()
            manifest = json.loads((root / ".thumbnails" / "clip.mp4.json").read_text())
            self.assertTrue(manifest["video"])

    def test_keeps_still_thumbnail_when_video_transcoding_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "clip.mp4").write_bytes(b"video source")

            def create_still(_source: Path, output: Path, _ffmpeg: str) -> None:
                output.write_bytes(b"still")

            with (
                patch("app.storage.thumbnail.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
                patch("app.storage.thumbnail._save_video_still", side_effect=create_still),
                patch(
                    "app.storage.thumbnail._save_video_preview",
                    side_effect=subprocess.CalledProcessError(1, "ffmpeg"),
                ),
                self.assertLogs("app.storage.thumbnail", level="ERROR"),
            ):
                still_path, video_path = generate_thumbnail_assets(
                    root,
                    "clip.mp4",
                    "video",
                )

            self.assertTrue(still_path.is_file())
            self.assertIsNone(video_path)
            manifest = json.loads((root / ".thumbnails" / "clip.mp4.json").read_text())
            self.assertFalse(manifest["video"])

    def test_video_encoder_disables_audio_and_caps_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.webm"
            source.write_bytes(b"video source")
            output = root / "preview.mp4"

            def write_temp_output(ffmpeg: str, arguments: list[str], path: Path) -> None:
                self.assertEqual(ffmpeg, "ffmpeg")
                path.write_bytes(b"preview")

            with patch(
                "app.storage.thumbnail._run_ffmpeg",
                side_effect=write_temp_output,
            ) as run_ffmpeg:
                _save_video_preview(source, output, "ffmpeg")

            command = run_ffmpeg.call_args.args[1]
            self.assertIn("-an", command)
            self.assertIn("libx264", command)
            self.assertIn("854", command[command.index("-vf") + 1])
            self.assertIn("480", command[command.index("-vf") + 1])
            self.assertTrue(output.is_file())

    def test_removes_deleted_media_assets_and_prunes_removed_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "folder" / "clip.mp4"
            source.parent.mkdir()
            source.write_bytes(b"video source")

            def create_output(_source: Path, output: Path, _ffmpeg: str) -> None:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"generated")

            with (
                patch("app.storage.thumbnail.imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"),
                patch("app.storage.thumbnail._save_video_still", side_effect=create_output),
                patch("app.storage.thumbnail._save_video_preview", side_effect=create_output),
            ):
                still_path, video_path = generate_thumbnail_assets(
                    root,
                    "folder/clip.mp4",
                    "video",
                )

            remove_thumbnail_assets(root, "folder/clip.mp4")
            self.assertFalse(still_path.exists())
            self.assertFalse(video_path.exists())
            prune_thumbnail_cache(root, {})
            self.assertEqual(list((root / ".thumbnails").rglob("*")), [])

    def test_rejects_thumbnail_paths_outside_media_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".thumbnails").mkdir()

            with self.assertRaises(ValueError):
                get_thumbnail_path(root, "../outside.png", "still")


if __name__ == "__main__":
    unittest.main()
