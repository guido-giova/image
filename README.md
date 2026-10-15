# Local Image and Video Gallery

Browse a private image and video collection locally. The app reads media from
`_files` (including nested folders) and does not upload files. The viewer can
toggle each media file's favorite value in its JSON sidecar, or move it to
`_files/.deleted`. Deleted files are retained for 30 days and then permanently
removed within a minute while the app is running, or at startup if already
expired.
It has no external service or frontend dependency.

## Run locally

Use Python 3.10 or later:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m app
```

The app opens <http://127.0.0.1:8000> in your browser on startup and binds to
localhost only. Put media files in `_files`; press **Reload** in the gallery
after adding or removing files.

Alternatively, run `python main.py` from the project root.

The gallery lists all discovered media together. Image previews load lazily, and
videos use the browser's native controls and load only when played.
Click an image preview or a media filename to open the viewer. The viewer shows
the media alongside its filename, path, and type, with a thumbnail carousel for
navigating the collection. Videos have playback controls in the viewer as well.
Use **fav** to toggle `isFavorite` in the media file's same-stem JSON sidecar;
if no sidecar exists, one is created with default metadata. Use **del** to move
the media and its sidecar into a uniquely named folder under `_files/.deleted`,
preserving the original relative path. Deleted items are excluded from the
gallery. Cleanup runs once a minute and at startup.

Supported formats:

- Images: `.avif`, `.gif`, `.jpeg`, `.jpg`, `.png`, `.webp`
- Videos: `.mp4`, `.ogv`, `.ogg`, `.webm`