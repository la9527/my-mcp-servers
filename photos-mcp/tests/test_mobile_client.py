from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from PIL import Image
from starlette.testclient import TestClient

from photos_mcp.application import mobile_client as mobile_client_application
from photos_mcp.application.person_identity_repository import (
    FaceObservationInput,
    PersonIdentityRepository,
)
from photos_mcp.application.story_generation import (
    ensure_recommendation_story,
    ensure_scoped_story,
)
from photos_mcp.application.share_image_service import ShareImageService
from photos_mcp.infrastructure.mobile_client import MobileClientRepository
from photos_mcp.infrastructure.mobile_location import MobileLocationLedger
from photos_mcp.infrastructure.persistence.run_repository import RunRepository
from photos_mcp.infrastructure.persistence.state_store import PhotosMcpStateStore
from photos_mcp.interfaces.http.mobile_client import build_mobile_client_app
from photos_mcp.interfaces.http.mobile_location import build_mobile_location_app


def _pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _sign(key: ec.EllipticCurvePrivateKey, message: str) -> str:
    value = key.sign(message.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _fixture(
    tmp_path: Path,
    *,
    controls_enabled: bool = False,
    source_port=None,
    manual_starter=None,
    identity_repository=None,
):
    run_repository = RunRepository(tmp_path / "jobs.db")
    state_store = PhotosMcpStateStore(
        endpoint="http://127.0.0.1:8010/mcp",
        health_endpoint="http://127.0.0.1:8010/health",
        run_repository=run_repository,
    )
    state_store.set_daemon_status("ready")
    location_ledger = MobileLocationLedger(
        tmp_path / "location.db", encryption_key=b"l" * 32
    )
    ingest_key = ec.generate_private_key(ec.SECP256R1())
    token = location_ledger.create_enrollment_token()
    ingest_device = location_ledger.enroll_device(
        token=token, public_key_pem=_pem(ingest_key), label="test"
    )
    client_repository = MobileClientRepository(tmp_path / "mobile-client.db")

    recommendation_root = tmp_path / "recommendations"
    image_path = recommendation_root / "2026" / "2026-09-08" / "photo.jpg"
    image_path.parent.mkdir(parents=True)
    Image.new("RGB", (1200, 900), "#3c725f").save(image_path)
    run_repository.upsert_local_recommendation_asset(
        {
            "local_asset_id": "local-asset-mobile-000001",
            "content_hash": "a" * 64,
            "relative_path": "2026/2026-09-08/photo.jpg",
            "mime_type": "image/jpeg",
            "byte_size": image_path.stat().st_size,
            "capture_date_local": "2026-09-08",
            "source_path": "/private/should-never-leak.jpg",
            "latitude": 37.123456,
            "longitude": 127.987654,
        }
    )
    run_repository.upsert_recommendation_collection(
        {
            "collection_id": "collection-mobile-test",
            "analysis_run_id": "analysis-mobile-test",
            "policy_version": "scene-recommendations-v1",
            "provider": "apple_photos",
            "status": "completed",
        }
    )
    run_repository.upsert_recommendation_member(
        {
            "collection_id": "collection-mobile-test",
            "provider": "apple_photos",
            "provider_asset_id": "provider-private-asset-id",
            "photo_id": "private-photo-id",
            "local_asset_id": "local-asset-mobile-000001",
            "capture_date_local": "2026-09-08",
            "recommendation_slot": 1,
            "selection_reason_codes": ["best_quality"],
            "materialization_status": "completed",
        }
    )
    run_repository.upsert_automation_run(
        {
            "automation_run_id": "combined-mobile-test",
            "provider": "combined",
            "status": "completed",
            "terminal": True,
            "lookback_days": 10,
            "requested_limit": 1000,
            "timeout_seconds": 21600,
            "processed_count": 1,
            "recommended_count": 1,
            "materialized_count": 1,
            "child_run_ids": {"apple": "daily-apple-mobile"},
            "created_at": datetime(2026, 9, 8, tzinfo=UTC).isoformat(),
            "completed_at": datetime(2026, 9, 8, 1, tzinfo=UTC).isoformat(),
        }
    )
    run_repository.upsert_automation_run(
        {
            "automation_run_id": "daily-apple-mobile",
            "provider": "apple",
            "parent_run_id": "combined-mobile-test",
            "status": "completed",
            "terminal": True,
            "processed_count": 1,
            "recommendation_storage": {
                "recommended_count": 1,
                "materialized_count": 1,
                "failed_count": 0,
            },
        }
    )
    image_service = ShareImageService(
        run_repository,
        source_root=recommendation_root,
        cache_root=tmp_path / "cache",
    )
    apk_path = tmp_path / "PhotosMcp-Album.apk"
    apk_path.write_bytes(b"signed-test-apk")
    app = build_mobile_client_app(
        state_store=state_store,
        client_repository=client_repository,
        location_ledger=location_ledger,
        image_service=image_service,
        android_apk_path=apk_path,
        allow_test_client=True,
        controls_enabled=controls_enabled,
        source_port=source_port,
        manual_starter=manual_starter,
        identity_repository=identity_repository,
    )
    app.state.run_repository = run_repository
    return app, ingest_device, ingest_key, client_repository


def _owner_session(client: TestClient, ingest_device, ingest_key):
    owner_key = ec.generate_private_key(ec.SECP256R1())
    owner_pem = _pem(owner_key)
    owner_key_id = "owner-key-mobile-000001"
    challenge = client.post(
        "/mobile-client/v1/challenge",
        json={
            "device_id": ingest_device.device_id,
            "ingest_key_id": ingest_device.key_id,
            "purpose": "owner_enroll",
        },
    ).json()["data"]
    fingerprint = hashlib.sha256(owner_pem.encode("ascii")).hexdigest()
    enroll_message = "\n".join(
        (
            "OWNER-ENROLL-V1",
            challenge["challenge_id"],
            challenge["nonce"],
            ingest_device.device_id,
            ingest_device.key_id,
            owner_key_id,
            fingerprint,
        )
    )
    enrolled = client.post(
        "/mobile-client/v1/owner-enroll",
        json={
            "device_id": ingest_device.device_id,
            "ingest_key_id": ingest_device.key_id,
            "purpose": "owner_enroll",
            "challenge_id": challenge["challenge_id"],
            "nonce": challenge["nonce"],
            "owner_key_id": owner_key_id,
            "owner_public_key_pem": owner_pem,
            "signature": _sign(ingest_key, enroll_message),
        },
    )
    assert enrolled.status_code == 201, enrolled.text

    session_challenge = client.post(
        "/mobile-client/v1/challenge",
        json={
            "device_id": ingest_device.device_id,
            "ingest_key_id": ingest_device.key_id,
            "purpose": "owner_session",
        },
    ).json()["data"]
    session_message = "\n".join(
        (
            "OWNER-SESSION-V1",
            session_challenge["challenge_id"],
            session_challenge["nonce"],
            ingest_device.device_id,
            owner_key_id,
        )
    )
    response = client.post(
        "/mobile-client/v1/session",
        json={
            "device_id": ingest_device.device_id,
            "owner_key_id": owner_key_id,
            "challenge_id": session_challenge["challenge_id"],
            "nonce": session_challenge["nonce"],
            "signature": _sign(owner_key, session_message),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["access_token"], owner_key_id, owner_key, session_challenge


def _signed_command_headers(token, owner_key, *, path, body, prefix):
    created_at = datetime.now(UTC).isoformat()
    nonce = f"nonce-{prefix}-00000001"
    idempotency = f"{prefix}-command-00000001"
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    message = "\n".join(
        ("OWNER-COMMAND-V1", "POST", path, body_hash, nonce, idempotency, created_at)
    )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Idempotency-Key": idempotency,
        "X-Command-Nonce": nonce,
        "X-Command-Created-At": created_at,
        "X-Device-Signature": _sign(owner_key, message),
    }


def test_mobile_client_requires_tailnet_even_before_device_session(tmp_path, monkeypatch) -> None:
    _app, _ingest_device, _ingest_key, _repository = _fixture(tmp_path)
    monkeypatch.setenv("PHOTOS_MCP_OWNER_TAILSCALE_LOGINS", "owner@example.com")
    protected_app = build_mobile_client_app(
        state_store=None,
        client_repository=MobileClientRepository(tmp_path / "private.db"),
        location_ledger=MobileLocationLedger(
            tmp_path / "private-location.db", encryption_key=b"p" * 32
        ),
        allow_test_client=False,
    )
    with TestClient(protected_app) as client:
        assert client.get("/mobile-client/v1/capabilities").status_code == 403
        assert client.get("/mobile-client/v1/dashboard").status_code == 401
        assert client.get("/mobile-client/story").status_code == 401
        assert client.get("/mobile-client/download").status_code == 403

    # The public receiver remains a separate write-only application.
    public = build_mobile_location_app(
        ledger=MobileLocationLedger(tmp_path / "public.db", encryption_key=b"q" * 32)
    )
    with TestClient(public) as client:
        assert client.get("/mobile-client/v1/dashboard").status_code == 404
        assert client.get("/mobile-client/download").status_code == 404
        assert client.get("/mobile-location/v1/results").status_code == 404


def test_owner_key_is_separate_and_challenges_are_single_use(tmp_path) -> None:
    app, ingest_device, ingest_key, repository = _fixture(tmp_path)
    with TestClient(app, base_url="https://photos.example") as client:
        token, owner_key_id, owner_key, challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        assert owner_key_id != ingest_device.key_id
        assert repository.get_owner_device(ingest_device.device_id).owner_key_id == owner_key_id
        replay_message = "\n".join(
            (
                "OWNER-SESSION-V1",
                challenge["challenge_id"],
                challenge["nonce"],
                ingest_device.device_id,
                owner_key_id,
            )
        )
        replay = client.post(
            "/mobile-client/v1/session",
            json={
                "device_id": ingest_device.device_id,
                "owner_key_id": owner_key_id,
                "challenge_id": challenge["challenge_id"],
                "nonce": challenge["nonce"],
                "signature": _sign(owner_key, replay_message),
            },
        )
        assert replay.status_code == 401
        assert repository.validate_session(token, scope="story:read") is not None


def test_mobile_run_projection_exposes_bounded_failure_diagnostics(tmp_path) -> None:
    repository = RunRepository(tmp_path / "jobs.db")
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-mobile-failed",
            "provider": "combined",
            "status": "failed",
            "terminal": True,
            "gate_status": "failed",
            "child_run_ids": {
                "apple": "daily-apple-blocked",
                "google": "daily-google-auth",
            },
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "daily-apple-blocked",
            "provider": "apple",
            "status": "cancelled",
            "terminal": True,
            "error_code": "google_mcp_gate_failed",
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "daily-google-auth",
            "provider": "google_photos",
            "status": "failed",
            "terminal": True,
            "error_code": "authentication_required",
        }
    )

    projected = mobile_client_application.mobile_run_projection(
        repository, "combined-mobile-failed"
    )

    assert projected["error_code"] == "authentication_required"
    assert projected["error_stage"] == "google_mcp_readiness"
    assert projected["source_errors"] == [
        {
            "source": "apple",
            "status": "cancelled",
            "error_code": "google_mcp_gate_failed",
        },
        {
            "source": "google",
            "status": "failed",
            "error_code": "authentication_required",
        },
    ]


def test_mobile_projection_and_story_webview_are_private_and_redacted(tmp_path) -> None:
    app, ingest_device, ingest_key, _repository = _fixture(tmp_path)
    # Results are a projection of an explicitly persisted Story. Durable
    # recommendation copies alone must not resurrect a Story after deletion.
    ensure_recommendation_story(app.state.run_repository)
    with TestClient(app, base_url="https://photos.example") as client:
        token, _owner_key_id, _owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        headers = {"Authorization": f"Bearer {token}"}
        capabilities = client.get("/mobile-client/v1/capabilities")
        dashboard = client.get("/mobile-client/v1/dashboard", headers=headers)
        runs = client.get("/mobile-client/v1/runs", headers=headers)
        results = client.get("/mobile-client/v1/results", headers=headers)
        result_thumb = client.get(
            "/mobile-client/v1/results/assets/local-asset-mobile-000001/thumb",
            headers=headers,
        )
        events = client.get("/mobile-client/v1/events", headers=headers)
        download_page = client.get("/mobile-client/download")
        download_apk = client.get("/mobile-client/download/PhotosMcp-Album.apk")
        download_checksum = client.get(
            "/mobile-client/download/PhotosMcp-Album.apk.sha256"
        )

        assert capabilities.status_code == 200
        assert dashboard.status_code == 200
        assert dashboard.json()["data"]["daemon_status"] == "ready"
        assert runs.json()["data"][0]["run_id"] == "combined-mobile-test"
        assert len(results.json()["data"]) == 1
        assert result_thumb.status_code == 200
        assert result_thumb.headers["content-type"] == "image/jpeg"
        assert result_thumb.headers["cache-control"] == "no-store, private"
        assert client.get(
            "/mobile-client/v1/results/assets/local-asset-mobile-000001/thumb"
        ).status_code == 401
        assert client.get(
            "/mobile-client/v1/results/assets/not-a-current-asset/thumb",
            headers=headers,
        ).status_code == 404
        assert client.get(
            "/mobile-client/v1/results/assets/local-asset-mobile-000001/original",
            headers=headers,
        ).status_code == 404
        assert events.status_code == 200
        assert len(events.json()["data"]) == 1
        assert download_page.status_code == 200
        assert "PhotosMcp 앨범 0.6.2" in download_page.text
        assert 'download="PhotosMcp-Album-0.6.2.apk"' in download_page.text
        assert "Chrome으로 열기" in download_page.text
        assert download_apk.status_code == 200
        assert download_apk.content == b"signed-test-apk"
        assert download_apk.headers["content-type"] == "application/vnd.android.package-archive"
        assert hashlib.sha256(download_apk.content).hexdigest() in download_checksum.text
        event_id = events.json()["data"][0]["event_id"]
        acknowledged = client.post(
            "/mobile-client/v1/events/ack",
            headers=headers,
            json={"event_id": event_id},
        )
        assert acknowledged.status_code == 204
        assert client.get("/mobile-client/v1/events", headers=headers).json()["data"][0][
            "acknowledged"
        ] is True

        projected = json.dumps(
            {
                "dashboard": dashboard.json(),
                "runs": runs.json(),
                "results": results.json(),
            },
            ensure_ascii=False,
        )
        for private_value in (
            "/private/should-never-leak.jpg",
            "provider-private-asset-id",
            "private-photo-id",
            "37.123456",
            "127.987654",
        ):
            assert private_value not in projected

        exchange = client.post("/mobile-client/v1/web-exchange", headers=headers)
        code = exchange.json()["data"]["exchange_code"]
        bootstrap = client.post(
            "/mobile-client/story/bootstrap",
            data={"exchange_code": code},
            follow_redirects=False,
        )
        assert bootstrap.status_code == 303
        assert "Secure" in bootstrap.headers["set-cookie"]
        assert "HttpOnly" in bootstrap.headers["set-cookie"]
        assert "SameSite=strict" in bootstrap.headers["set-cookie"]

        story = client.get("/mobile-client/story")
        css = client.get("/mobile-client/story/story.css")
        js = client.get("/mobile-client/story/story.js")
        thumb = client.get(
            "/mobile-client/story/assets/local-asset-mobile-000001/thumb"
        )
        replay = client.post(
            "/mobile-client/story/bootstrap", data={"exchange_code": code}
        )

        assert story.status_code == 200
        assert "/mobile-client/story/story.css" in story.text
        assert "/mobile-client/story/assets/local-asset-mobile-000001/thumb" in story.text
        assert css.status_code == 200
        assert js.status_code == 200
        assert thumb.status_code == 200
        assert thumb.headers["content-type"] == "image/jpeg"
        assert replay.status_code == 401


def test_story_v3_people_projection_keeps_only_confirmed_presentation_fields() -> None:
    private_ref = "person_private_ref_001"
    story = {
        "schema_version": "recommendation-story-v3",
        "story_id": "story-mobile-people-001",
        "people_overview": [
            {
                "person_ref": private_ref,
                "identity_revision": 9,
                "display_name": "민지",
                "photo_count": 1,
                "similarity": 0.99,
            }
        ],
        "photos": [
            {
                "asset_id": "local-asset-mobile-people-001",
                "title": "함께한 하루",
                "person_refs": [private_ref],
                "confirmed_people": [
                    {
                        "person_ref": private_ref,
                        "identity_revision": 9,
                        "display_name": "민지",
                        "embedding": [0.1, 0.2],
                    }
                ],
                "people_caption": f"함께한 사람: 민지 · {private_ref}",
                "provider_asset_id": "provider-private-person-asset",
                "path": "/private/person-photo.jpg",
            }
        ],
        "chapters": [
            {
                "date": "2026-09-09",
                "title": "하루",
                "asset_ids": ["local-asset-mobile-people-001"],
                "person_refs": [private_ref],
                "confirmed_people": [
                    {
                        "person_ref": private_ref,
                        "identity_revision": 9,
                        "display_name": "민지",
                    }
                ],
                "people_caption": "함께한 사람: 민지",
            }
        ],
    }

    projected = mobile_client_application.mobile_story_projection(story)

    assert projected["people_overview"] == [
        {"display_name": "민지", "photo_count": 1}
    ]
    assert projected["photos"][0]["confirmed_people"] == [
        {"display_name": "민지"}
    ]
    assert projected["photos"][0]["people_caption"] == "함께한 사람: 민지"
    assert projected["chapters"][0]["confirmed_people"] == [
        {"display_name": "민지"}
    ]
    encoded = json.dumps(projected, ensure_ascii=False)
    for private_value in (
        private_ref,
        "identity_revision",
        "embedding",
        "similarity",
        "provider-private-person-asset",
        "/private/person-photo.jpg",
    ):
        assert private_value not in encoded

    legacy = mobile_client_application.mobile_story_projection(
        {**story, "schema_version": "recommendation-story-v2"}
    )
    assert legacy["people_overview"] == []
    assert "confirmed_people" not in legacy["photos"][0]
    assert "people_caption" not in legacy["chapters"][0]


def test_dashboard_reads_only_explicit_story_manifest_and_never_resurrects_deleted_story(
    tmp_path,
) -> None:
    repository = RunRepository(tmp_path / "story-wiring.db")
    empty = mobile_client_application.mobile_dashboard(repository, daemon_status="ready")

    assert empty["latest_story"] is None

    repository.upsert_story_manifest(
        {
            "schema_version": "recommendation-story-v3",
            "story_id": "story-mobile-wiring-001",
            "title": "명시적으로 저장한 Story",
            "status": "ready",
            "photos": [],
            "chapters": [],
            "people_overview": [],
        }
    )
    visible = mobile_client_application.mobile_dashboard(repository, daemon_status="ready")
    assert visible["latest_story"]["story_id"] == "story-mobile-wiring-001"

    repository.upsert_story_manifest(
        {
            "schema_version": "recommendation-story-v3",
            "story_id": "story-mobile-wiring-001",
            "title": "삭제한 Story",
            "status": "deleted",
            "photos": [],
            "chapters": [],
            "people_overview": [],
        }
    )
    deleted = mobile_client_application.mobile_dashboard(repository, daemon_status="ready")
    assert deleted["latest_story"] is None


def test_people_endpoints_require_owner_session_and_hide_unconfirmed_names(
    tmp_path,
) -> None:
    identities = PersonIdentityRepository(tmp_path / "person-identities.db")
    confirmed = identities.create_identity(
        person_identity_id="person_confirmed_mobile_001",
        display_name="민지",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    identities.set_story_name_consent(
        confirmed.person_identity_id,
        "owner",
        True,
        expected_identity_revision=confirmed.identity_revision,
    )
    identities.create_identity(
        person_identity_id="person_candidate_mobile_001",
        display_name="provider-raw-secret-name",
        identity_status="candidate",
        name_status="provider_asserted",
    )
    identities.create_identity(
        person_identity_id="person_unconsented_mobile_001",
        display_name="owner-name-without-consent",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    identities.create_identity(
        person_identity_id="person_conflicted_mobile_001",
        identity_status="conflicted",
    )
    app, ingest_device, ingest_key, _repository = _fixture(
        tmp_path, identity_repository=identities
    )

    with TestClient(app, base_url="https://photos.example") as client:
        assert client.get("/mobile-client/v1/people").status_code == 401
        assert (
            client.get("/mobile-client/v1/people/review-summary").status_code == 401
        )
        token, _owner_key_id, _owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        headers = {"Authorization": f"Bearer {token}"}

        people_response = client.get("/mobile-client/v1/people", headers=headers)
        summary_response = client.get(
            "/mobile-client/v1/people/review-summary", headers=headers
        )

        assert people_response.status_code == 200
        people = people_response.json()["data"]
        assert {item.get("display_name") for item in people} == {
            None,
            "민지",
            "owner-name-without-consent",
        }
        assert sum(item["identity_status"] == "candidate" for item in people) == 1
        assert sum(item["identity_status"] == "conflicted" for item in people) == 1
        assert all("person_identity_id" not in item for item in people)
        confirmed_people = [
            item for item in people if item["identity_status"] == "user_confirmed"
        ]
        assert all(item["consent_action_handle"].startswith("pah_") for item in confirmed_people)
        assert all("story_name_consent" in item for item in confirmed_people)
        assert all(
            "consent_action_handle" not in item
            for item in people
            if item["identity_status"] != "user_confirmed"
        )
        serialized = json.dumps(people_response.json(), ensure_ascii=False)
        assert "provider-raw-secret-name" not in serialized
        assert "person_confirmed_mobile_001" not in serialized
        assert "identity_revision" not in serialized
        for forbidden_key in (
            "embedding",
            "similarity",
            "path",
            "provider_asset_id",
        ):
            assert forbidden_key not in serialized

        assert summary_response.status_code == 200
        assert summary_response.json()["data"] == {
            "candidate_identity_count": 1,
            "conflicted_identity_count": 1,
            "candidate_membership_count": 0,
            "pending_lineage_hold_count": 0,
            "total_review_count": 2,
        }


def test_signed_person_consent_is_idempotent_and_revision_safe(tmp_path) -> None:
    identities = PersonIdentityRepository(tmp_path / "person-consent-identities.db")
    person = identities.create_identity(
        person_identity_id="person_consent_mobile_001",
        display_name="소유자 확인 이름",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    identities.create_identity(
        person_identity_id="person_candidate_no_action_001",
        display_name="provider-name-must-stay-private",
        identity_status="candidate",
        name_status="provider_asserted",
    )
    app, ingest_device, ingest_key, _repository = _fixture(
        tmp_path,
        identity_repository=identities,
    )
    path = "/mobile-client/v1/people/consent"

    with TestClient(app, base_url="https://photos.example") as client:
        token, _owner_key_id, owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        auth = {"Authorization": f"Bearer {token}"}
        people = client.get("/mobile-client/v1/people", headers=auth).json()["data"]
        confirmed = next(
            item for item in people if item.get("display_name") == "소유자 확인 이름"
        )
        candidate = next(
            item for item in people if item["identity_status"] == "candidate"
        )
        assert confirmed["story_name_consent"] == {
            "personal_story": False,
            "family_share": False,
        }
        assert "consent_action_handle" not in candidate
        assert "provider-name-must-stay-private" not in json.dumps(people)

        original_handle = confirmed["consent_action_handle"]
        tampered_handle = original_handle[:-1] + (
            "A" if original_handle[-1] != "A" else "B"
        )
        tampered_payload = {
            "schema_version": 1,
            "consent_action_handle": tampered_handle,
            "audience": "personal_story",
            "allowed": True,
        }
        tampered_body = json.dumps(tampered_payload, separators=(",", ":"))
        tampered = client.post(
            path,
            content=tampered_body,
            headers=_signed_command_headers(
                token,
                owner_key,
                path=path,
                body=tampered_body,
                prefix="person-tampered",
            ),
        )
        assert tampered.status_code == 404
        assert tampered.json()["error"] == "consent_action_unavailable"

        first_payload = {
            "schema_version": 1,
            "consent_action_handle": confirmed["consent_action_handle"],
            "audience": "personal_story",
            "allowed": True,
        }
        first_body = json.dumps(first_payload, separators=(",", ":"))
        first_headers = _signed_command_headers(
            token,
            owner_key,
            path=path,
            body=first_body,
            prefix="person-consent",
        )
        first = client.post(path, content=first_body, headers=first_headers)
        duplicate = client.post(path, content=first_body, headers=first_headers)

        assert first.status_code == 200, first.text
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["data"] == first.json()["data"]
        assert identities.current_consent(person.person_identity_id, "owner") is True
        assert first.json()["data"]["story_name_consent"] == {
            "personal_story": True,
            "family_share": False,
        }
        refreshed_handle = first.json()["data"]["consent_action_handle"]
        assert refreshed_handle != confirmed["consent_action_handle"]

        conflicting_body = json.dumps(
            {**first_payload, "allowed": False}, separators=(",", ":")
        )
        conflict = client.post(
            path,
            content=conflicting_body,
            headers=_signed_command_headers(
                token,
                owner_key,
                path=path,
                body=conflicting_body,
                prefix="person-consent",
            ),
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"] == "idempotency_key_conflict"

        stale_payload = {
            **first_payload,
            "audience": "family_share",
        }
        stale_body = json.dumps(stale_payload, separators=(",", ":"))
        stale = client.post(
            path,
            content=stale_body,
            headers=_signed_command_headers(
                token,
                owner_key,
                path=path,
                body=stale_body,
                prefix="person-stale",
            ),
        )
        assert stale.status_code == 409
        assert stale.json()["error"] == "stale_identity_revision"

        family_payload = {
            "schema_version": 1,
            "consent_action_handle": refreshed_handle,
            "audience": "family_share",
            "allowed": True,
        }
        family_body = json.dumps(family_payload, separators=(",", ":"))
        family = client.post(
            path,
            content=family_body,
            headers=_signed_command_headers(
                token,
                owner_key,
                path=path,
                body=family_body,
                prefix="person-family",
            ),
        )
        assert family.status_code == 200, family.text
        assert family.json()["data"]["story_name_consent"] == {
            "personal_story": True,
            "family_share": True,
        }
        assert identities.current_consent(person.person_identity_id, "family_share") is True

        unsigned = client.post(path, json=family_payload, headers=auth)
        assert unsigned.status_code == 400
        oversized_or_extra = {**family_payload, "person_identity_id": person.person_identity_id}
        assert client.post(path, json=oversized_or_extra, headers=auth).status_code == 400


def test_owner_consent_withdrawal_immediately_refreshes_persisted_stories(
    tmp_path,
) -> None:
    identities = PersonIdentityRepository(tmp_path / "person-story-withdrawal.db")
    person = identities.create_identity(
        person_identity_id="person_story_withdrawal_001",
        display_name="즉시 제거할 이름",
        identity_status="user_confirmed",
        name_status="user_confirmed",
    )
    observation = identities.register_face_observation(
        FaceObservationInput(
            provider=None,
            provider_asset_id=None,
            local_asset_id="local-asset-mobile-000001",
            model_family="test",
            model_version="1",
            embedding_dimension=4,
            model_fingerprint="test-v1",
            bbox_fingerprint="bbox-story-withdrawal",
        )
    )
    identities.set_membership(
        observation.face_observation_id,
        person.person_identity_id,
        membership_state="owner_confirmed",
        provenance="owner",
        decision_policy_version="owner-v1",
        expected_identity_revision=person.identity_revision,
    )
    current = identities.get_identity(person.person_identity_id)
    identities.set_story_name_consent(
        person.person_identity_id,
        "owner",
        True,
        expected_identity_revision=current.identity_revision,
    )
    app, ingest_device, ingest_key, _client_repository = _fixture(
        tmp_path,
        identity_repository=identities,
    )
    run_repository = app.state.run_repository
    global_story = ensure_recommendation_story(
        run_repository,
        identity_repository=identities,
    )
    scoped_story = ensure_scoped_story(
        run_repository,
        story_id="story-withdrawal-test-001",
        collection_ids={"collection-mobile-test"},
        date_from="2026-09-08",
        date_to="2026-09-08",
        identity_repository=identities,
    )
    assert "즉시 제거할 이름" in str(global_story)
    assert "즉시 제거할 이름" in str(scoped_story)

    with TestClient(app, base_url="https://photos.example") as client:
        token, _owner_key_id, owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        auth = {"Authorization": f"Bearer {token}"}
        person_projection = client.get(
            "/mobile-client/v1/people", headers=auth
        ).json()["data"][0]
        payload = {
            "schema_version": 1,
            "consent_action_handle": person_projection["consent_action_handle"],
            "audience": "personal_story",
            "allowed": False,
        }
        body = json.dumps(payload, separators=(",", ":"))
        response = client.post(
            "/mobile-client/v1/people/consent",
            content=body,
            headers=_signed_command_headers(
                token,
                owner_key,
                path="/mobile-client/v1/people/consent",
                body=body,
                prefix="person-story-withdrawal",
            ),
        )

        assert response.status_code == 200, response.text
        assert "즉시 제거할 이름" not in str(
            run_repository.get_story_manifest(global_story["story_id"])
        )
        assert "즉시 제거할 이름" not in str(
            run_repository.get_story_manifest(scoped_story["story_id"])
        )
        detail = client.get(
            f"/mobile-client/v1/stories/{scoped_story['story_id']}",
            headers=auth,
        )
        assert "즉시 제거할 이름" not in detail.text


def test_people_endpoints_report_unavailable_without_identity_repository(tmp_path) -> None:
    app, ingest_device, ingest_key, _repository = _fixture(tmp_path)
    with TestClient(app, base_url="https://photos.example") as client:
        token, _owner_key_id, _owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/mobile-client/v1/people", headers=headers).status_code == 503
        assert (
            client.get(
                "/mobile-client/v1/people/review-summary", headers=headers
            ).json()["error"]
            == "people_unavailable"
        )


def test_revoking_owner_invalidates_api_and_web_sessions(tmp_path) -> None:
    app, ingest_device, ingest_key, repository = _fixture(tmp_path)
    with TestClient(app, base_url="https://photos.example") as client:
        token, _owner_key_id, _owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        headers = {"Authorization": f"Bearer {token}"}
        exchange = client.post("/mobile-client/v1/web-exchange", headers=headers).json()
        client.post(
            "/mobile-client/story/bootstrap",
            data={"exchange_code": exchange["data"]["exchange_code"]},
            follow_redirects=False,
        )
        assert client.get("/mobile-client/story").status_code == 200
        assert repository.revoke_device(ingest_device.device_id) is True
        assert client.get("/mobile-client/v1/dashboard", headers=headers).status_code == 401
        assert client.get("/mobile-client/story").status_code == 401


def test_mobile_server_uses_the_canonical_run_repository(monkeypatch, tmp_path) -> None:
    from photos_mcp import mobile_client_server

    expected = tmp_path / "canonical-jobs.db"
    monkeypatch.setattr(mobile_client_server, "default_run_repository_path", lambda: expected)

    app = mobile_client_server.create_app()
    service = next(
        route.endpoint.__self__
        for route in app.routes
        if getattr(route, "path", "") == "/mobile-client/v1/dashboard"
    )

    assert service.state_store.run_repository.path == expected


def test_mobile_server_binds_exact_dates_into_google_picker_worker(monkeypatch, tmp_path) -> None:
    import asyncio
    from photos_mcp import mobile_client_server

    monkeypatch.setattr(
        mobile_client_server, "default_run_repository_path", lambda: tmp_path / "jobs.db"
    )
    commands = []

    async def fake_combined(**_kwargs):
        return {
            "automation_run_id": "combined-manual-picker",
            "children": {
                "google": {
                    "automation_run_id": "daily-google-picker",
                    "picker_worker_required": True,
                    "user_action": {"request_id": "action-google-picker"},
                }
            },
        }

    monkeypatch.setattr(mobile_client_server, "start_combined_curation", fake_combined)
    monkeypatch.setattr(
        mobile_client_server.subprocess,
        "Popen",
        lambda command, **kwargs: commands.append((command, kwargs)),
    )
    app = mobile_client_server.create_app()
    service = next(
        route.endpoint.__self__
        for route in app.routes
        if getattr(route, "path", "") == "/mobile-client/v1/manual-curations"
    )
    asyncio.run(
        service._manual_starter(
            {
                "sources": ["google"],
                "limit": 20,
                "provider_limits": {"google": 20},
                "scope_kind": "capture_date_bounded",
                "date_from": "2026-08-17",
                "date_to": "2026-08-18",
                "timeout_seconds": 21600,
            }
        )
    )
    command = commands[0][0]
    assert command[command.index("--date-from") + 1] == "2026-08-17"
    assert command[command.index("--date-to") + 1] == "2026-08-18"
    assert command[command.index("--browser-control-mode") + 1] == "qwen-agent"
    assert command[command.index("--preselect-count") + 1] == "20"
    assert command[command.index("--timeout-seconds") + 1] == "21600"
    assert command[command.index("--model-mission-timeout-seconds") + 1] == "600"


def test_manual_preview_exposes_selection_contract_and_blocks_unsupported_people_modes(
    tmp_path,
) -> None:
    class Source:
        async def list_photos(self, source, **_filters):
            assert source == "apple"
            return []

    app, ingest_device, ingest_key, _repository = _fixture(
        tmp_path,
        controls_enabled=True,
        source_port=Source(),
    )
    with TestClient(app, base_url="https://photos.example") as client:
        token, _owner_key_id, _owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        auth = {"Authorization": f"Bearer {token}"}
        base_payload = {
            "schema_version": 1,
            "date_from": "2026-09-01",
            "date_to": "2026-09-07",
            "timezone": "Asia/Seoul",
            "sources": ["apple"],
            "limit": 20,
            "provider_limits": {"apple": 20},
            "exclude_screenshots": True,
            "timeout_seconds": 21600,
            "reanalyze": False,
            "publication_policy": "none",
            "story_policy": "run_scoped",
        }

        people = client.post(
            "/mobile-client/v1/manual-curations/preview",
            json={**base_payload, "selection_mode": "people_present"},
            headers=auth,
        )
        google_people = client.post(
            "/mobile-client/v1/manual-curations/preview",
            json={
                **base_payload,
                "sources": ["google"],
                "provider_limits": {"google": 20},
                "selection_mode": "people_present",
            },
            headers=auth,
        )
        specific_person = client.post(
            "/mobile-client/v1/manual-curations/preview",
            json={**base_payload, "selection_mode": "specific_person"},
            headers=auth,
        )

        assert people.status_code == 200, people.text
        assert people.json()["data"]["scope"]["selection_mode"] == "people_present"
        assert people.json()["data"]["scope"]["selection_profile"] == "person"
        assert google_people.status_code == 422
        assert google_people.json()["error"] == "google_people_selection_not_supported"
        assert specific_person.status_code == 422
        assert specific_person.json()["error"] == "specific_person_not_supported"


def test_signed_manual_command_returns_explicit_google_people_policy_error(tmp_path) -> None:
    started = []

    async def starter(request):
        started.append(request)
        return {"automation_run_id": "must-not-start"}

    app, ingest_device, ingest_key, _repository = _fixture(
        tmp_path,
        controls_enabled=True,
        manual_starter=starter,
    )
    with TestClient(app, base_url="https://photos.example") as client:
        token, _owner_key_id, owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        payload = {
            "schema_version": 1,
            "date_from": "2026-09-01",
            "date_to": "2026-09-07",
            "timezone": "Asia/Seoul",
            "sources": ["google"],
            "selection_mode": "people_present",
            "limit": 20,
            "provider_limits": {"google": 20},
            "exclude_screenshots": True,
            "timeout_seconds": 21600,
            "reanalyze": False,
            "publication_policy": "none",
            "story_policy": "run_scoped",
        }
        body = json.dumps(payload, separators=(",", ":"))
        created_at = datetime.now(UTC).isoformat()
        nonce = "nonce-google-people-0001"
        idempotency = "google-people-command-0001"
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        message = "\n".join(
            (
                "OWNER-COMMAND-V1",
                "POST",
                "/mobile-client/v1/manual-curations",
                body_hash,
                nonce,
                idempotency,
                created_at,
            )
        )

        response = client.post(
            "/mobile-client/v1/manual-curations",
            content=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency,
                "X-Command-Nonce": nonce,
                "X-Command-Created-At": created_at,
                "X-Device-Signature": _sign(owner_key, message),
            },
        )

        assert response.status_code == 422
        assert response.json()["error"] == "google_people_selection_not_supported"
        assert started == []


def test_manual_command_requires_owner_signature_and_is_idempotent(tmp_path) -> None:
    class Source:
        async def list_photos(self, source, **filters):
            assert source == "apple"
            return [{"id": "apple-preview"}]

    async def starter(_request):
        return {"automation_run_id": "combined-signed-manual"}

    app, ingest_device, ingest_key, _repository = _fixture(
        tmp_path,
        controls_enabled=True,
        source_port=Source(),
        manual_starter=starter,
    )
    with TestClient(app, base_url="https://photos.example") as client:
        token, _owner_key_id, owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        auth = {"Authorization": f"Bearer {token}"}
        payload = {
            "schema_version": 1,
            "date_from": "2026-09-01",
            "date_to": "2026-09-07",
            "timezone": "Asia/Seoul",
            "sources": ["apple"],
            "limit": 20,
            "provider_limits": {"apple": 20},
            "exclude_screenshots": True,
            "timeout_seconds": 21600,
            "reanalyze": False,
            "publication_policy": "none",
            "story_policy": "run_scoped",
        }
        preview = client.post(
            "/mobile-client/v1/manual-curations/preview", json=payload, headers=auth
        )
        assert preview.status_code == 200
        assert preview.json()["data"]["providers"]["apple"]["count"] == 1

        body = json.dumps(payload, separators=(",", ":"))
        created_at = datetime.now(UTC).isoformat()
        nonce = "nonce-manual-command-0001"
        idempotency = "manual-command-0001"
        body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        message = "\n".join(
            (
                "OWNER-COMMAND-V1",
                "POST",
                "/mobile-client/v1/manual-curations",
                body_hash,
                nonce,
                idempotency,
                created_at,
            )
        )
        command_headers = {
            **auth,
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency,
            "X-Command-Nonce": nonce,
            "X-Command-Created-At": created_at,
            "X-Device-Signature": _sign(owner_key, message),
        }
        first = client.post(
            "/mobile-client/v1/manual-curations", content=body, headers=command_headers
        )
        duplicate = client.post(
            "/mobile-client/v1/manual-curations", content=body, headers=command_headers
        )
        unsigned = client.post(
            "/mobile-client/v1/manual-curations", content=body, headers=auth
        )
        assert first.status_code == 202, first.text
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json()["data"]["operation_id"] == first.json()["data"]["operation_id"]
        assert unsigned.status_code == 400


def test_story_reanalysis_and_delete_require_signed_owner_commands(tmp_path) -> None:
    async def starter(_request):
        return {"automation_run_id": "combined-story-reanalysis"}

    app, ingest_device, ingest_key, _client_repository = _fixture(
        tmp_path, controls_enabled=True, manual_starter=starter
    )
    repository = app.state.run_repository
    request = {
        "date_from": "2026-09-01",
        "date_to": "2026-09-02",
        "timezone": "Asia/Seoul",
        "sources": ["apple"],
        "limit": 20,
        "provider_limits": {"apple": 20},
        "timeout_seconds": 21600,
        "scope_kind": "capture_date_bounded",
        "publication_policy": "none",
        "story_policy": "run_scoped",
        "reanalyze": False,
    }
    operation, _ = repository.enqueue_curation_operation(
        {
            "operation_id": "manual-op-story-source",
            "idempotency_key": "manual-story-source-key",
            "request_hash": "source-request-hash",
            "origin": "android",
            "status": "completed",
            "request": request,
        }
    )
    repository.upsert_automation_run(
        {
            "automation_run_id": "combined-story-source",
            "provider": "combined",
            "operation_id": operation["operation_id"],
            "status": "completed",
            "terminal": True,
        }
    )
    story_id = "story-manual-source-test"
    repository.upsert_story_manifest(
        {
            "story_id": story_id,
            "title": "재분석 대상",
            "status": "ready",
            "scope": {"origin_run_id": "combined-story-source"},
            "photos": [],
        }
    )
    repository.upsert_shared_story_package(
        {
            "share_id": "share-story-source-test",
            "story_id": story_id,
            "status": "active",
            "expires_at": "2026-10-08T00:00:00+00:00",
            "session_version": 1,
        }
    )

    with TestClient(app, base_url="https://photos.example") as client:
        token, _owner_key_id, owner_key, _challenge = _owner_session(
            client, ingest_device, ingest_key
        )
        body = json.dumps({"schema_version": 1}, separators=(",", ":"))
        reanalyze_path = f"/mobile-client/v1/stories/{story_id}/reanalyze"
        reanalyze = client.post(
            reanalyze_path,
            content=body,
            headers=_signed_command_headers(
                token, owner_key, path=reanalyze_path, body=body, prefix="reanalyze"
            ),
        )
        assert reanalyze.status_code == 202, reanalyze.text
        new_operation = repository.get_curation_operation(
            reanalyze.json()["data"]["operation_id"]
        )
        assert new_operation["request"]["reanalyze"] is True
        assert new_operation["request"]["date_from"] == "2026-09-01"

        delete_path = f"/mobile-client/v1/stories/{story_id}/delete"
        deleted = client.post(
            delete_path,
            content=body,
            headers=_signed_command_headers(
                token, owner_key, path=delete_path, body=body, prefix="delete-story"
            ),
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json()["data"]["revoked_share_count"] == 1
        auth = {"Authorization": f"Bearer {token}"}
        assert client.get(f"/mobile-client/v1/stories/{story_id}", headers=auth).status_code == 404
        assert story_id not in {
            item["story_id"]
            for item in client.get("/mobile-client/v1/stories", headers=auth).json()["data"]
        }
        assert repository.get_shared_story_package("share-story-source-test")["status"] == "revoked"
