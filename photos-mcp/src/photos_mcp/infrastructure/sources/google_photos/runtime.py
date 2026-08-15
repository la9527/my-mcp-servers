"""Production composition root for Google Photos Picker and uploads."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from photos_mcp.application.cloud_selection_service import CloudSelectionService
from photos_mcp.application.google_photos_import_service import (
    GooglePhotosImportService,
    start_google_materialized_classification,
)
from photos_mcp.domain.models.source import PhotoProvider, SourceDescriptor
from photos_mcp.infrastructure.credentials.keychain import KeychainCredentialStore
from photos_mcp.infrastructure.runtime.paths import photos_mcp_cache_root, photos_mcp_runtime_root
from photos_mcp.infrastructure.sources.google_photos.connection import GoogleOAuthConnectionService
from photos_mcp.infrastructure.sources.google_photos.content import GooglePickedContentAdapter
from photos_mcp.infrastructure.sources.google_photos.http import (
    GoogleHttpTransport,
    UrllibGoogleHttpTransport,
)
from photos_mcp.infrastructure.sources.google_photos.import_repository import GoogleImportLeaseRepository
from photos_mcp.infrastructure.sources.google_photos.library_destination import (
    GoogleAppCreatedLibraryDestination,
    GooglePhotosLibraryClient,
)
from photos_mcp.infrastructure.sources.google_photos.oauth import (
    GoogleOAuthTokenProvider,
    GooglePickerCredentialRepository,
)
from photos_mcp.infrastructure.sources.google_photos.picker import GooglePhotosPickerAdapter
from photos_mcp.infrastructure.sources.google_photos.session_repository import PickerSessionRepository
from photos_mcp.infrastructure.sources.google_photos.upload_repository import GoogleUploadReceiptRepository


@dataclass(frozen=True, slots=True)
class GooglePhotosRuntimeSettings:
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "photos-mcp:/oauth/google"
    account_id: str = "default"

    @classmethod
    def from_environment(cls) -> "GooglePhotosRuntimeSettings":
        return cls(
            client_id=os.environ.get("PHOTOS_MCP_GOOGLE_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("PHOTOS_MCP_GOOGLE_CLIENT_SECRET", "").strip(),
            redirect_uri=os.environ.get(
                "PHOTOS_MCP_GOOGLE_REDIRECT_URI",
                "photos-mcp:/oauth/google",
            ).strip(),
            account_id=os.environ.get("PHOTOS_MCP_GOOGLE_ACCOUNT_ID", "default").strip()
            or "default",
        )

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.redirect_uri)


@dataclass(slots=True)
class GooglePhotosRuntime:
    settings: GooglePhotosRuntimeSettings
    source: SourceDescriptor
    connection: GoogleOAuthConnectionService
    importer: GooglePhotosImportService
    destination: GoogleAppCreatedLibraryDestination
    picker_sessions: PickerSessionRepository
    import_leases: GoogleImportLeaseRepository
    upload_receipts: GoogleUploadReceiptRepository

    def close(self) -> None:
        self.picker_sessions.close()
        self.import_leases.close()
        self.upload_receipts.close()


def build_google_photos_runtime(
    *,
    settings: GooglePhotosRuntimeSettings | None = None,
    transport: GoogleHttpTransport | None = None,
    credential_store=None,
    state_store=None,
    runtime_root: str | Path | None = None,
    cache_root: str | Path | None = None,
) -> GooglePhotosRuntime:
    settings = settings or GooglePhotosRuntimeSettings.from_environment()
    transport = transport or UrllibGoogleHttpTransport()
    credentials = GooglePickerCredentialRepository(credential_store or KeychainCredentialStore())
    token_provider = GoogleOAuthTokenProvider(
        account_id=settings.account_id,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        credential_repository=credentials,
        transport=transport,
    )
    connection = GoogleOAuthConnectionService(
        account_id=settings.account_id,
        client_id=settings.client_id,
        client_secret=settings.client_secret,
        redirect_uri=settings.redirect_uri,
        credential_repository=credentials,
        transport=transport,
    )
    root = Path(runtime_root or photos_mcp_runtime_root()) / "google-photos"
    cache = Path(cache_root or photos_mcp_cache_root()) / "google-photos-imports"
    root.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    cache.chmod(0o700)
    picker = GooglePhotosPickerAdapter(
        access_token=token_provider.access_token,
        transport=transport,
    )
    sessions = PickerSessionRepository(root / "picker-sessions.sqlite3")
    leases = GoogleImportLeaseRepository(root / "import-leases.sqlite3")
    receipts = GoogleUploadReceiptRepository(root / "upload-receipts.sqlite3")
    selection = CloudSelectionService(picker, sessions)
    content = GooglePickedContentAdapter(
        resolve_url=picker.resolve_content_url,
        fetch_bytes=picker.fetch_content_bytes,
        cache_root=cache,
    )

    async def start_classification(paths, selection_profile, mode, limit):
        return await start_google_materialized_classification(
            paths,
            selection_profile,
            mode,
            limit,
            state_store=state_store,
        )

    importer = GooglePhotosImportService(
        selection=selection,
        content_adapter=content,
        leases=leases,
        classification_starter=start_classification,
    )
    library = GooglePhotosLibraryClient(
        access_token=token_provider.access_token,
        transport=transport,
        receipts=receipts,
    )
    return GooglePhotosRuntime(
        settings=settings,
        source=SourceDescriptor(
            source_id=f"google-photos:{settings.account_id}",
            provider=PhotoProvider.GOOGLE_PHOTOS,
            account_id=settings.account_id,
            display_name="Google Photos",
        ),
        connection=connection,
        importer=importer,
        destination=GoogleAppCreatedLibraryDestination(client=library),
        picker_sessions=sessions,
        import_leases=leases,
        upload_receipts=receipts,
    )
