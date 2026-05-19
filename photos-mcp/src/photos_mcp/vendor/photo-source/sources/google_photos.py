"""Google Photos source via Google Photos Library API.

Google One 스토리지의 사진에 접근합니다.
OAuth 2.0 인증이 필요하며, 최초 실행 시 브라우저를 통해 인증합니다.

Setup:
  1. Google Cloud Console에서 OAuth 2.0 Client ID 생성
     - Application type: Desktop app
     - Google Photos Library API 활성화
  2. credentials.json 다운로드 → ~/.config/photo-source/credentials.json
  3. 최초 실행 시 브라우저 인증 → token.json 자동 저장
"""

from __future__ import annotations

import base64
import io
import json
import logging
from datetime import datetime
from pathlib import Path

from ..models import Photo, PhotoMetadata

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path.home() / ".config" / "photo-source"
_CREDENTIALS_FILE = _CONFIG_DIR / "credentials.json"
_TOKEN_FILE = _CONFIG_DIR / "token.json"

_SCOPES = ["https://www.googleapis.com/auth/photoslibrary.readonly"]


class GooglePhotosSource:
    """Access photos in Google Photos (Google One) via Library API."""

    def __init__(self, credentials_path: str = "") -> None:
        self._credentials_path = Path(credentials_path) if credentials_path else _CREDENTIALS_FILE
        self._service = None

    def _ensure_authenticated(self):
        if self._service is not None:
            return

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError:
            raise RuntimeError(
                "Google API 라이브러리가 설치되지 않았습니다. "
                "Install with: uv pip install 'photo-source[google]'"
            )

        creds = None

        # Load saved token
        if _TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)

        # Refresh or run OAuth flow
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self._credentials_path.exists():
                    raise FileNotFoundError(
                        f"OAuth credentials 파일이 없습니다: {self._credentials_path}\n"
                        "Google Cloud Console에서 OAuth Client ID를 생성하고 "
                        "credentials.json을 다운로드하세요.\n"
                        "https://console.cloud.google.com/apis/credentials"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self._credentials_path), _SCOPES
                )
                creds = flow.run_local_server(port=0)

            # Save token for next run
            _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            _TOKEN_FILE.write_text(creds.to_json())
            logger.info("Google Photos token saved to %s", _TOKEN_FILE)

        self._service = build("photoslibrary", "v1", credentials=creds, static_discovery=False)
        logger.info("Google Photos API connected")

    def list_photos(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        album: str | None = None,
        limit: int = 100,
    ) -> list[Photo]:
        """List photos from Google Photos library."""
        self._ensure_authenticated()

        if album:
            return self._list_album_photos(album, limit)

        body: dict = {"pageSize": min(limit, 100)}

        # Date filter
        if date_from or date_to:
            date_filter = self._build_date_filter(date_from, date_to)
            body["filters"] = {"dateFilter": date_filter}

        photos: list[Photo] = []
        while len(photos) < limit:
            resp = self._service.mediaItems().search(body=body).execute()
            items = resp.get("mediaItems", [])
            if not items:
                break

            for item in items:
                if len(photos) >= limit:
                    break
                photo = self._to_photo(item)
                if photo:
                    photos.append(photo)

            next_token = resp.get("nextPageToken")
            if not next_token:
                break
            body["pageToken"] = next_token

        return photos

    def _list_album_photos(self, album_name: str, limit: int) -> list[Photo]:
        """List photos in a specific album by name."""
        # Find album ID by name
        album_id = self._find_album_id(album_name)
        if not album_id:
            logger.warning("Album not found: %s", album_name)
            return []

        body = {"albumId": album_id, "pageSize": min(limit, 100)}
        photos: list[Photo] = []

        while len(photos) < limit:
            resp = self._service.mediaItems().search(body=body).execute()
            items = resp.get("mediaItems", [])
            if not items:
                break

            for item in items:
                if len(photos) >= limit:
                    break
                photo = self._to_photo(item)
                if photo:
                    photos.append(photo)

            next_token = resp.get("nextPageToken")
            if not next_token:
                break
            body["pageToken"] = next_token

        return photos

    def _find_album_id(self, name: str) -> str | None:
        """Find album ID by name (case-insensitive)."""
        name_lower = name.lower()
        resp = self._service.albums().list(pageSize=50).execute()
        while True:
            for album in resp.get("albums", []):
                if album.get("title", "").lower() == name_lower:
                    return album["id"]
            next_token = resp.get("nextPageToken")
            if not next_token:
                break
            resp = self._service.albums().list(
                pageSize=50, pageToken=next_token
            ).execute()
        return None

    def get_metadata(self, photo_id: str) -> PhotoMetadata | None:
        """Get detailed metadata for a Google Photos media item."""
        self._ensure_authenticated()

        try:
            item = self._service.mediaItems().get(mediaItemId=photo_id).execute()
        except Exception as e:
            logger.error("Failed to get metadata for %s: %s", photo_id, e)
            return None

        meta = item.get("mediaMetadata", {})
        return PhotoMetadata(
            photo_id=photo_id,
            filename=item.get("filename", ""),
            date_taken=meta.get("creationTime", ""),
            camera_make=meta.get("photo", {}).get("cameraMake", ""),
            camera_model=meta.get("photo", {}).get("cameraModel", ""),
            focal_length=float(meta.get("photo", {}).get("focalLength", 0)),
            exposure_time=meta.get("photo", {}).get("exposureTime", ""),
            iso=int(meta.get("photo", {}).get("isoEquivalent", 0) or 0),
        )

    def get_thumbnail(
        self, photo_id: str, max_size: int = 512,
    ) -> str | None:
        """Download and resize Google Photos image to base64 thumbnail."""
        self._ensure_authenticated()

        try:
            import requests
            from .image_utils import open_image_bytes, thumbnail_to_base64

            item = self._service.mediaItems().get(mediaItemId=photo_id).execute()
            base_url = item.get("baseUrl")
            if not base_url:
                return None

            # Google Photos baseUrl + =w{max_size}-h{max_size} for server-side resize
            url = f"{base_url}=w{max_size}-h{max_size}"
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()

            image = open_image_bytes(resp.content)
            return thumbnail_to_base64(image, max_size)
        except Exception as e:
            logger.error("Failed to get thumbnail for %s: %s", photo_id, e)
            return None

    def search_photos(self, query: str, limit: int = 50) -> list[Photo]:
        """Search photos using Google Photos content categories and filters.

        Note: Google Photos API doesn't support free-text search.
        This searches by content category matching.
        """
        self._ensure_authenticated()

        # Map common queries to Google Photos content categories
        category_map = {
            "landscape": "LANDSCAPES",
            "portrait": "SELFIES",
            "selfie": "SELFIES",
            "food": "FOOD",
            "animal": "ANIMALS",
            "pet": "PETS",
            "flower": "FLOWERS",
            "receipt": "RECEIPTS",
            "screenshot": "SCREENSHOTS",
            "travel": "TRAVEL",
            "night": "NIGHT",
        }

        query_lower = query.lower()
        categories = []
        for keyword, cat in category_map.items():
            if keyword in query_lower:
                categories.append(cat)

        if not categories:
            # Fall back to listing recent photos
            return self.list_photos(limit=limit)

        body = {
            "pageSize": min(limit, 100),
            "filters": {
                "contentFilter": {
                    "includedContentCategories": categories[:6],  # max 6
                }
            },
        }

        photos: list[Photo] = []
        resp = self._service.mediaItems().search(body=body).execute()
        for item in resp.get("mediaItems", []):
            if len(photos) >= limit:
                break
            photo = self._to_photo(item)
            if photo:
                photos.append(photo)

        return photos

    def list_albums(self, limit: int = 50) -> list[dict]:
        """List all albums in the library."""
        self._ensure_authenticated()
        albums = []
        resp = self._service.albums().list(pageSize=min(limit, 50)).execute()
        while True:
            for album in resp.get("albums", []):
                albums.append({
                    "id": album["id"],
                    "title": album.get("title", ""),
                    "media_count": int(album.get("mediaItemsCount", 0)),
                    "cover_url": album.get("coverPhotoBaseUrl", ""),
                })
                if len(albums) >= limit:
                    return albums
            next_token = resp.get("nextPageToken")
            if not next_token:
                break
            resp = self._service.albums().list(
                pageSize=50, pageToken=next_token
            ).execute()
        return albums

    # ── helpers ──

    @staticmethod
    def _build_date_filter(
        date_from: str | None, date_to: str | None,
    ) -> dict:
        """Build Google Photos API dateFilter from ISO date strings."""
        result: dict = {}
        ranges: dict = {}

        if date_from:
            dt = datetime.fromisoformat(date_from)
            ranges["startDate"] = {
                "year": dt.year, "month": dt.month, "day": dt.day,
            }
        if date_to:
            dt = datetime.fromisoformat(date_to)
            ranges["endDate"] = {
                "year": dt.year, "month": dt.month, "day": dt.day,
            }

        if ranges:
            result["ranges"] = [ranges]
        return result

    @staticmethod
    def _to_photo(item: dict) -> Photo | None:
        """Convert Google Photos mediaItem to Photo model."""
        meta = item.get("mediaMetadata", {})

        # Skip videos
        if "video" in meta:
            return None

        width = int(meta.get("width", 0) or 0)
        height = int(meta.get("height", 0) or 0)

        return Photo(
            id=item["id"],
            filename=item.get("filename", ""),
            date_taken=meta.get("creationTime", ""),
            source="google_photos",
            path=item.get("productUrl", ""),
            width=width,
            height=height,
        )
