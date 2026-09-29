"""Single source of truth for user-visible photo-analysis limits.

The product supports one logical analysis or Story request containing up to
10,000 photos.  A Google Photos Picker session has its own documented 2,000
item ceiling, so Google acquisition is intentionally split into durable
sessions while the parent operation retains the 10,000-photo budget.
"""

from __future__ import annotations


MAX_ANALYSIS_PHOTOS = 10_000
"""Maximum photos in one user-visible direct/manual analysis operation."""

MAX_PICKER_SESSION_PHOTOS = 2_000
"""Google Photos Picker's maximum selectable items in one session."""

GOOGLE_DOWNLOAD_BATCH_PHOTOS = 100
"""Bounded download/checkpoint batch; deliberately independent of request size."""

MAX_LOCATION_PREFETCH_PHOTOS = MAX_ANALYSIS_PHOTOS
"""Maximum Android original-metadata records covered by one manual request."""

MAX_RESULT_GALLERY_ITEMS = MAX_ANALYSIS_PHOTOS
"""Maximum persisted rows loaded by the virtualized desktop result gallery."""
