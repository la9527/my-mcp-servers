from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
import hashlib
import json
import sqlite3

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from starlette.testclient import TestClient

from photos_mcp.infrastructure.mobile_location import MobileLocationLedger
from photos_mcp.interfaces.http.mobile_location import (
    SIGNED_PATH,
    build_mobile_location_app,
    canonical_batch_message,
)


NOW = datetime(2026, 9, 7, 13, 30, tzinfo=UTC)


def _key_pair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    return private_key, public_pem


def _manifest(**overrides):
    item = {
        "device_asset_key": "a" * 64,
        "captured_at": "2026-09-07T10:20:30+09:00",
        "width": 4080,
        "height": 3060,
        "mime_type": "image/jpeg",
        "strong_content_digest": "b" * 64,
        "perceptual_hash": "c" * 16,
        "latitude": 37.123456,
        "longitude": 127.654321,
        "altitude_m": 42.5,
        "accuracy_m": 8.0,
        "extractor_version": "android-bridge-1",
    }
    item.update(overrides)
    return item


def _json_body(payload) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _signed_headers(
    private_key,
    device,
    body: bytes,
    *,
    nonce: str = "nonce_0123456789abcdefghij",
    sequence: int = 1,
    idempotency_key: str = "batch_0123456789abcdef",
    sent_at: str = "2026-09-07T13:30:00Z",
):
    body_sha = hashlib.sha256(body).hexdigest()
    signature = private_key.sign(
        canonical_batch_message(
            body_sha256=body_sha,
            sent_at=sent_at,
            nonce=nonce,
            sequence=sequence,
            idempotency_key=idempotency_key,
        ),
        ec.ECDSA(hashes.SHA256()),
    )
    return {
        "Content-Type": "application/json",
        "X-Photos-Device-Id": device["device_id"],
        "X-Photos-Key-Id": device["key_id"],
        "X-Photos-Sent-At": sent_at,
        "X-Photos-Nonce": nonce,
        "X-Photos-Sequence": str(sequence),
        "Idempotency-Key": idempotency_key,
        "X-Photos-Signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
    }


def _enroll(client: TestClient, ledger: MobileLocationLedger, public_pem: str):
    token = ledger.create_enrollment_token()
    response = client.post(
        "/mobile-location/v1/enroll",
        json={
            "schema_version": 1,
            "token": token,
            "public_key_pem": public_pem,
            "device_label": "personal phone",
        },
    )
    assert response.status_code == 201
    return response.json(), token


def test_enrollment_is_one_time_and_accepts_only_p256(tmp_path) -> None:
    ledger = MobileLocationLedger(
        tmp_path / "mobile.sqlite3",
        encryption_key=b"k" * 32,
        now_fn=lambda: NOW,
    )
    app = build_mobile_location_app(ledger=ledger, now_fn=lambda: NOW)
    private_key, public_pem = _key_pair()
    del private_key
    with TestClient(app) as client:
        device, token = _enroll(client, ledger, public_pem)
        reused = client.post(
            "/mobile-location/v1/enroll",
            json={"token": token, "public_key_pem": public_pem},
        )
        bad_curve = ec.generate_private_key(ec.SECP384R1()).public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        fresh = ledger.create_enrollment_token()
        rejected_curve = client.post(
            "/mobile-location/v1/enroll",
            json={"token": fresh, "public_key_pem": bad_curve},
        )

    assert device["device_id"].startswith("dev_")
    assert reused.status_code == 401
    assert rejected_curve.status_code == 401
    ledger.close()


def test_enrollment_with_the_same_device_key_is_idempotent(tmp_path) -> None:
    ledger = MobileLocationLedger(
        tmp_path / "mobile.sqlite3",
        encryption_key=b"i" * 32,
        now_fn=lambda: NOW,
    )
    app = build_mobile_location_app(ledger=ledger, now_fn=lambda: NOW)
    _private_key, public_pem = _key_pair()
    with TestClient(app) as client:
        first, _token = _enroll(client, ledger, public_pem)
        second_token = ledger.create_enrollment_token()
        second_response = client.post(
            "/mobile-location/v1/enroll",
            json={
                "schema_version": 1,
                "token": second_token,
                "public_key_pem": public_pem,
                "device_label": "same personal phone",
            },
        )

    assert second_response.status_code == 201
    assert second_response.json() == first
    assert ledger._conn.execute(
        "SELECT COUNT(*) FROM mobile_location_devices"
    ).fetchone()[0] == 1
    assert ledger._conn.execute(
        "SELECT used_at FROM mobile_enrollment_tokens WHERE token_hash = ?",
        (hashlib.sha256(second_token.encode()).hexdigest(),),
    ).fetchone()[0]
    ledger.close()


def test_expired_enrollment_and_oversized_or_encoded_body_are_rejected(tmp_path) -> None:
    clock = [NOW]
    ledger = MobileLocationLedger(
        tmp_path / "mobile.sqlite3",
        encryption_key=b"q" * 32,
        now_fn=lambda: clock[0],
    )
    app = build_mobile_location_app(ledger=ledger, now_fn=lambda: clock[0])
    _private_key, public_pem = _key_pair()
    token = ledger.create_enrollment_token(ttl=timedelta(minutes=1))
    clock[0] = NOW + timedelta(minutes=2)
    with TestClient(app) as client:
        expired = client.post(
            "/mobile-location/v1/enroll",
            json={"token": token, "public_key_pem": public_pem},
        )
        encoded = client.post(
            "/mobile-location/v1/enroll",
            content=b"{}",
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        )
        oversized = client.post(
            "/mobile-location/v1/enroll",
            content=b"{" + b"x" * (1024 * 1024) + b"}",
            headers={"Content-Type": "application/json"},
        )
    assert expired.status_code == 401
    assert encoded.status_code == 400
    assert oversized.status_code == 400
    ledger.close()


def test_signed_batch_encrypts_exact_location_and_is_idempotent(tmp_path) -> None:
    database = tmp_path / "mobile.sqlite3"
    ledger = MobileLocationLedger(database, encryption_key=b"e" * 32, now_fn=lambda: NOW)
    app = build_mobile_location_app(ledger=ledger, now_fn=lambda: NOW)
    private_key, public_pem = _key_pair()
    body = _json_body({"schema_version": 1, "manifests": [_manifest()]})
    with TestClient(app) as client:
        device, _token = _enroll(client, ledger, public_pem)
        headers = _signed_headers(private_key, device, body)
        first = client.post(SIGNED_PATH, content=body, headers=headers)
        duplicate_headers = _signed_headers(
            private_key,
            device,
            body,
            nonce="nonce_abcdefghijklmnopqrstuvwxyz",
        )
        duplicate = client.post(SIGNED_PATH, content=body, headers=duplicate_headers)

    assert first.status_code == 200
    assert first.json()["accepted"] == 1
    assert first.json()["duplicate"] is False
    assert duplicate.status_code == 200
    assert duplicate.json() == {**first.json(), "duplicate": True}
    private = ledger.list_decrypted_manifests()
    assert len(private) == 1
    assert private[0]["latitude"] == 37.123456
    assert private[0]["longitude"] == 127.654321
    ledger.close()

    raw_database = database.read_bytes()
    assert b"37.123456" not in raw_database
    assert b"127.654321" not in raw_database
    assert b"personal phone" not in raw_database
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT encrypted_location, encryption_nonce FROM android_asset_manifests"
        ).fetchone()
    assert row is not None and len(row[0]) > 16 and len(row[1]) == 12


def test_bad_signature_stale_request_and_extra_image_field_are_rejected(tmp_path) -> None:
    ledger = MobileLocationLedger(
        tmp_path / "mobile.sqlite3", encryption_key=b"x" * 32, now_fn=lambda: NOW
    )
    app = build_mobile_location_app(ledger=ledger, now_fn=lambda: NOW)
    private_key, public_pem = _key_pair()
    body = _json_body({"schema_version": 1, "manifests": [_manifest()]})
    with TestClient(app) as client:
        device, _token = _enroll(client, ledger, public_pem)
        bad_headers = _signed_headers(private_key, device, body)
        bad_headers["X-Photos-Signature"] = "not-a-valid-signature"
        bad = client.post(SIGNED_PATH, content=body, headers=bad_headers)

        stale_headers = _signed_headers(
            private_key,
            device,
            body,
            sent_at=(NOW - timedelta(minutes=11)).isoformat(),
        )
        stale = client.post(SIGNED_PATH, content=body, headers=stale_headers)

        with_image = _json_body(
            {
                "schema_version": 1,
                "manifests": [_manifest(image_base64="private-image-data")],
            }
        )
        image_headers = _signed_headers(private_key, device, with_image)
        image = client.post(SIGNED_PATH, content=with_image, headers=image_headers)

    assert bad.status_code == 401
    assert stale.status_code == 401
    assert image.status_code == 400
    assert ledger.list_decrypted_manifests() == []
    ledger.close()


def test_sequence_conflict_revoke_and_route_surface(tmp_path) -> None:
    ledger = MobileLocationLedger(
        tmp_path / "mobile.sqlite3", encryption_key=b"z" * 32, now_fn=lambda: NOW
    )
    app = build_mobile_location_app(ledger=ledger, now_fn=lambda: NOW)
    private_key, public_pem = _key_pair()
    first_body = _json_body({"schema_version": 1, "manifests": [_manifest()]})
    second_body = _json_body(
        {"schema_version": 1, "manifests": [_manifest(device_asset_key="d" * 64)]}
    )
    with TestClient(app) as client:
        device, _token = _enroll(client, ledger, public_pem)
        first = client.post(
            SIGNED_PATH,
            content=first_body,
            headers=_signed_headers(private_key, device, first_body),
        )
        conflict = client.post(
            SIGNED_PATH,
            content=second_body,
            headers=_signed_headers(private_key, device, second_body),
        )
        assert ledger.revoke_device(device["device_id"]) is True
        revoked = client.post(
            SIGNED_PATH,
            content=second_body,
            headers=_signed_headers(
                private_key,
                device,
                second_body,
                sequence=2,
                idempotency_key="batch_2222222222222222",
            ),
        )
        health = client.get("/mobile-location/health")
        no_list = client.get("/mobile-location/v1/batches")
        no_assets = client.get("/mobile-location/v1/assets")

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert revoked.status_code == 401
    assert health.status_code == 204 and health.content == b""
    assert no_list.status_code == 405
    assert no_assets.status_code == 404
    assert "access-control-allow-origin" not in first.headers
    assert ledger.purge_revoked_device(device["device_id"]) is True
    assert ledger.list_decrypted_manifests() == []
    ledger.close()


def test_enrollment_attempts_are_rate_limited(tmp_path) -> None:
    ledger = MobileLocationLedger(
        tmp_path / "mobile.sqlite3", encryption_key=b"r" * 32, now_fn=lambda: NOW
    )
    app = build_mobile_location_app(ledger=ledger, now_fn=lambda: NOW)
    _private_key, public_pem = _key_pair()
    with TestClient(app) as client:
        responses = [
            client.post(
                "/mobile-location/v1/enroll",
                json={"token": "invalid_enrollment_token_00000000", "public_key_pem": public_pem},
            )
            for _ in range(11)
        ]
    assert all(response.status_code == 401 for response in responses[:10])
    assert responses[10].status_code == 429
    ledger.close()


def test_device_batch_rate_limit_applies_after_authentication(tmp_path) -> None:
    ledger = MobileLocationLedger(
        tmp_path / "mobile.sqlite3", encryption_key=b"s" * 32, now_fn=lambda: NOW
    )
    app = build_mobile_location_app(ledger=ledger, now_fn=lambda: NOW)
    private_key, public_pem = _key_pair()
    body = _json_body({"schema_version": 1, "manifests": [_manifest()]})
    with TestClient(app) as client:
        device, _token = _enroll(client, ledger, public_pem)
        responses = []
        for index in range(21):
            responses.append(
                client.post(
                    SIGNED_PATH,
                    content=body,
                    headers=_signed_headers(
                        private_key,
                        device,
                        body,
                        nonce=f"nonce_{index:04d}_abcdefghijklmnop",
                    ),
                )
            )
    assert all(response.status_code == 200 for response in responses[:20])
    assert responses[20].status_code == 429
    assert len(ledger.list_decrypted_manifests()) == 1
    ledger.close()
