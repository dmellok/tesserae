"""Pydantic model for offline photo albums (frame-cache collections, #177).

An album turns a Picture Gallery folder into a producer on the generic
frame-cache path (see ``docs/dev/frame-cache.md``): its images are pre-rendered
to each bound device's panel, content-addressed, and served through the
``/collection`` manifest so a storage-capable display caches the frames and
plays them back locally (wake, read card, paint, radio off).

The album owns the *producer* half of the contract: the source folder, an
explicit frame order, the fit mode used to render each image, and the playback
settings the firmware honours (``mode`` / ``interval_s`` / ``repeat``). The
shared cache (``app.collection_sync``) owns everything generic: digests, the
capacity budget, versioning.

Shape::

    Album(
        id="kitchen_album",
        name="Kitchen photos",
        device_ids=["kitchen_panel"],
        source_folder="holidays",
        order=["01.jpg", "07.jpg", "02.jpg"],   # empty = folder's natural sort
        fit="fill",
        playback=AlbumPlayback(mode="shuffle", interval_s=1800, repeat="reshuffle"),
    )

mypy --strict applies via re-export through app.state.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AlbumPlayback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # How the firmware walks the cached frames. ``sequential`` follows ``order``;
    # ``shuffle`` draws a firmware-local bag that resets when the manifest
    # version changes (new membership -> new bag).
    mode: Literal["sequential", "shuffle"] = "sequential"

    # Requested dwell per frame, in seconds. The firmware CLAMPS this to its
    # board / power bounds, so this is a hint, not a guarantee.
    interval_s: int = Field(default=1800, ge=1, le=86_400)

    # What happens at the end of the walk: replay in order, draw a fresh
    # shuffle bag, or hold the last frame.
    repeat: Literal["loop", "reshuffle", "once"] = "loop"


class Album(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    enabled: bool = True

    # Devices this album is pre-rendered for and played back on. Empty means the
    # album is defined but not bound yet (nothing is warmed).
    device_ids: list[str] = Field(default_factory=list)

    # The Picture Gallery folder id whose images are the album's frames.
    source_folder: str = Field(min_length=1)

    # Explicit frame order by filename. Empty means the folder's natural sort
    # (filename order). Filenames not present in the folder are ignored at
    # render time; filenames in the folder but absent here are appended in
    # natural sort so a newly uploaded image still shows up.
    order: list[str] = Field(default_factory=list)

    # How each image is fitted to the panel. ``fill`` crops to cover (no bars),
    # ``fit`` letterboxes. Matches ``app.quantizer.fit_to_panel`` modes.
    fit: Literal["fit", "fill"] = "fill"

    playback: AlbumPlayback = Field(default_factory=AlbumPlayback)
