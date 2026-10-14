const GALLERY_MAX_COLUMNS = 10;
const GALLERY_MIN_COLUMNS = 3;
const GALLERY_DEFAULT_COLUMNS = 5;

const gallery = document.querySelector("#gallery");
const statusMessage = document.querySelector("#gallery-status");
const mediaCount = document.querySelector("#media-count");
const reloadButton = document.querySelector("#reload-button");

function makeCard(item) {
  const card = document.createElement("article");
  card.className = "media-card";

  const preview = document.createElement(item.kind === "video" ? "video" : "img");
  preview.className = "media-preview";
  if (item.kind === "video") {
    preview.controls = true;
    preview.preload = "none";
    preview.playsInline = true;
  } else {
    preview.loading = "lazy";
    preview.decoding = "async";
    preview.alt = item.name;
  }
  preview.src = item.url;

  const caption = document.createElement("p");
  caption.className = "media-name";
  caption.textContent = item.path;
  caption.title = item.path;

  card.append(preview, caption);
  return card;
}

async function loadGallery(url = "/api/media", options) {
  reloadButton.disabled = true;
  try {
    const response = await fetch(url, options);
    if (!response.ok) {
      throw new Error(`Gallery request failed (${response.status})`);
    }

    const items = await response.json();
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      fragment.append(makeCard(item));
    }
    gallery.replaceChildren(fragment);
    mediaCount.textContent = `${items.length} ${items.length === 1 ? "item" : "items"}`;
    statusMessage.textContent = items.length
      ? ""
      : "No supported media found. Add images or videos to _files, then reload.";
    statusMessage.hidden = items.length > 0;
  } catch (error) {
    console.error(error);
    mediaCount.textContent = "Library unavailable";
    statusMessage.textContent = "Couldn't load the gallery. Check that the local app is running and try again.";
    statusMessage.hidden = false;
  } finally {
    reloadButton.disabled = false;
  }
}

reloadButton.addEventListener("click", () => {
  loadGallery("/api/media/reload", { method: "POST" });
});

const amountPerRowRange = document.querySelector("#amount-per-row");
const amountPerRowValue = document.querySelector("#amount-per-row-value");

amountPerRowRange.addEventListener("input", updateGalleryColumns);

function updateGalleryColumns() {
  const parsed = parseInt(amountPerRowRange.value, 10);
  const columns = Number.isNaN(parsed) ? GALLERY_DEFAULT_COLUMNS : Math.min(GALLERY_MAX_COLUMNS, Math.max(GALLERY_MIN_COLUMNS, parsed));
  amountPerRowRange.value = columns;
  
  gallery.style.setProperty("--columns", columns);
  amountPerRowValue.textContent = columns;
}

updateGalleryColumns();
loadGallery();
