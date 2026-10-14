# Local Image and Video Gallery

Browse a private image and video collection locally. The app reads media from
`_files` (including nested folders) and does not upload, edit, or delete files.
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

Supported formats:

- Images: `.avif`, `.gif`, `.jpeg`, `.jpg`, `.png`, `.webp`
- Videos: `.mp4`, `.ogv`, `.ogg`, `.webm`