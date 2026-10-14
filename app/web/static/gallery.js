const GALLERY_MAX_COLUMNS = 10;
const GALLERY_MIN_COLUMNS = 3;
const GALLERY_DEFAULT_COLUMNS = 5;

const gallery = document.querySelector("#gallery");
const statusMessage = document.querySelector("#gallery-status");
const mediaCount = document.querySelector("#media-count");
const reloadButton = document.querySelector("#reload-button");
const mediaDialog = document.querySelector("#media-dialog");
const viewerStage = document.querySelector("#viewer-stage");
const viewerData = document.querySelector("#viewer-data");
const viewerThumbnails = document.querySelector("#viewer-thumbnails");
const viewerClose = document.querySelector("#viewer-close");
const viewerPrevious = document.querySelector("#viewer-previous");
const viewerNext = document.querySelector("#viewer-next");

let mediaItems = [];
let thumbnailButtons = [];
let selectedIndex = -1;
let viewerOpener = null;

function makeCard(item, index) {
  const card = document.createElement("button");
  card.className = "media-card";
  card.type = "button";
  card.setAttribute("aria-label", `Open ${item.name}`);
  card.addEventListener("click", () => openViewer(index, preview));

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
  card.append(preview);
  preview.src = item.url;

  if (item.kind === "video") {
    card.append(preview);
  }

  return card;
}

function makeThumbnail(item, index) {
  const button = document.createElement("button");
  button.className = "viewer-thumbnail";
  button.type = "button";
  button.setAttribute("aria-label", `Show ${item.name}`);
  button.addEventListener("click", () => selectMedia(index));

  if (item.kind === "image") {
    const image = document.createElement("img");
    image.src = item.url;
    image.alt = "";
    image.loading = "lazy";
    image.decoding = "async";
    button.append(image);
  } else {
    const videoLabel = document.createElement("span");
    videoLabel.className = "thumbnail-video-label";
    videoLabel.textContent = "VIDEO";
    button.append(videoLabel);
  }

  return button;
}

function renderThumbnails() {
  const fragment = document.createDocumentFragment();
  thumbnailButtons = mediaItems.map((item, index) => {
    const button = makeThumbnail(item, index);
    fragment.append(button);
    return button;
  });
  viewerThumbnails.replaceChildren(fragment);
}

function getViewerDetails(item) {
  return [
    { label: "Filename", value: item.name },
    { label: "Path", value: item.path },
    { label: "Media type", value: item.kind === "video" ? "Video" : "Image" },
  ];
}

function renderViewer(index) {
  const item = mediaItems[index];
  if (!item) {
    return;
  }

  selectedIndex = index;

  const media = document.createElement(item.kind === "video" ? "video" : "img");
  media.className = "viewer-media";
  media.src = item.url;
  if (item.kind === "video") {
    media.controls = true;
    media.preload = "metadata";
    media.playsInline = true;
    media.setAttribute("aria-label", item.name);
  } else {
    media.alt = item.name;
    media.decoding = "async";
  }
  viewerStage.replaceChildren(media);

  const details = document.createDocumentFragment();
  for (const detail of getViewerDetails(item)) {
    const group = document.createElement("div");
    const label = document.createElement("dt");
    label.textContent = detail.label;
    const value = document.createElement("dd");
    value.textContent = detail.value;
    group.append(label, value);
    details.append(group);
  }
  viewerData.replaceChildren(details);

  viewerPrevious.disabled = index === 0;
  viewerNext.disabled = index === mediaItems.length - 1;
  for (const [thumbnailIndex, button] of thumbnailButtons.entries()) {
    if (thumbnailIndex === index) {
      button.setAttribute("aria-current", "true");
    } else {
      button.removeAttribute("aria-current");
    }
  }
  thumbnailButtons[index]?.scrollIntoView({ block: "nearest", inline: "nearest" });
}

function selectMedia(index) {
  if (index < 0 || index >= mediaItems.length) {
    return;
  }
  renderViewer(index);
}

function openViewer(index, opener) {
  if (!mediaDialog.open) {
    viewerOpener = opener;
    mediaDialog.showModal();
  }
  selectMedia(index);
  viewerClose.focus();
}

function closeViewer() {
  if (mediaDialog.open) {
    mediaDialog.close();
  }
}

viewerClose.addEventListener("click", closeViewer);
viewerPrevious.addEventListener("click", () => selectMedia(selectedIndex - 1));
viewerNext.addEventListener("click", () => selectMedia(selectedIndex + 1));

mediaDialog.addEventListener("click", (event) => {
  if (event.target === mediaDialog) {
    closeViewer();
  }
});

mediaDialog.addEventListener("close", () => {
  viewerStage.replaceChildren();
  if (viewerOpener?.isConnected) {
    viewerOpener.focus();
  }
  viewerOpener = null;
});

document.addEventListener("keydown", (event) => {
  if (!mediaDialog.open) {
    return;
  }

  if (event.key === "Escape") {
    event.preventDefault();
    closeViewer();
    return;
  }

  const activeElementTag = document.activeElement?.tagName;
  if (["INPUT", "TEXTAREA", "VIDEO"].includes(activeElementTag)) {
    return;
  }

  if (event.key === "ArrowLeft") {
    event.preventDefault();
    selectMedia(selectedIndex - 1);
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    selectMedia(selectedIndex + 1);
  }
});

async function loadGallery(url = "/api/media", options) {
  reloadButton.disabled = true;
  try {
    const response = await fetch(url, options);
    if (!response.ok) {
      throw new Error(`Gallery request failed (${response.status})`);
    }

    mediaItems = await response.json();
    const fragment = document.createDocumentFragment();
    mediaItems.forEach((item, index) => {
      fragment.append(makeCard(item, index));
    });
    gallery.replaceChildren(fragment);
    renderThumbnails();
    mediaCount.textContent = `${mediaItems.length} ${mediaItems.length === 1 ? "item" : "items"}`;
    statusMessage.textContent = mediaItems.length
      ? ""
      : "No supported media found. Add images or videos to _files, then reload.";
    statusMessage.hidden = mediaItems.length > 0;
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
