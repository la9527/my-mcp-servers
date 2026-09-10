#!/usr/bin/env python3
"""Send one signed fake GPS sidecar and remove its temporary device state."""

from __future__ import annotations

import argparse
import base64
from datetime import UTC, datetime
import hashlib
import json
import secrets
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from photos_mcp.infrastructure.mobile_location import MobileLocationLedger
from photos_mcp.interfaces.http.mobile_location import canonical_batch_message


DEFAULT_BASE = "http://127.0.0.1:18793/mobile-location"


def _post(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, dict]:
    request = Request(url, data=body, method="POST", headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except (ValueError, json.JSONDecodeError):
            payload = {"error": "non_json_error"}
        return exc.code, payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe the signed GPS-only receiver")
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_url = args.base_url.rstrip("/")
    ledger = MobileLocationLedger()
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    token = ledger.create_enrollment_token()
    enrollment_body = json.dumps(
        {
            "schema_version": 1,
            "token": token,
            "public_key_pem": public_pem,
            "device_label": "temporary-security-probe",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    status, enrolled = _post(
        f"{base_url}/v1/enroll",
        enrollment_body,
        {"Content-Type": "application/json"},
    )
    if status != 201:
        print(json.dumps({"stage": "enroll", "status": status, **enrolled}))
        return 1

    body = json.dumps(
        {
            "schema_version": 1,
            "manifests": [
                {
                    "device_asset_key": hashlib.sha256(b"temporary-security-probe").hexdigest(),
                    "captured_at": datetime.now(UTC).isoformat(),
                    "width": 4032,
                    "height": 3024,
                    "mime_type": "image/jpeg",
                    "strong_content_digest": hashlib.sha256(b"fake-image-digest").hexdigest(),
                    "perceptual_hash": "0123456789abcdef",
                    "latitude": 0.123456,
                    "longitude": -140.654321,
                    "accuracy_m": 10.0,
                    "extractor_version": "security-probe-1",
                }
            ],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    sent_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    nonce = secrets.token_urlsafe(24)
    idempotency_key = f"probe_{secrets.token_urlsafe(18)}"
    body_sha = hashlib.sha256(body).hexdigest()
    signature = private_key.sign(
        canonical_batch_message(
            body_sha256=body_sha,
            sent_at=sent_at,
            nonce=nonce,
            sequence=1,
            idempotency_key=idempotency_key,
        ),
        ec.ECDSA(hashes.SHA256()),
    )
    status, result = _post(
        f"{base_url}/v1/batches",
        body,
        {
            "Content-Type": "application/json",
            "X-Photos-Device-Id": enrolled["device_id"],
            "X-Photos-Key-Id": enrolled["key_id"],
            "X-Photos-Sent-At": sent_at,
            "X-Photos-Nonce": nonce,
            "X-Photos-Sequence": "1",
            "Idempotency-Key": idempotency_key,
            "X-Photos-Signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        },
    )
    ledger.revoke_device(enrolled["device_id"])
    purged = ledger.purge_revoked_device(enrolled["device_id"])
    ledger.close()
    safe_result = {
        "stage": "batch",
        "status": status,
        "accepted": int(result.get("accepted") or 0),
        "duplicate": bool(result.get("duplicate")),
        "temporary_state_purged": purged,
    }
    print(json.dumps(safe_result, separators=(",", ":")))
    return 0 if status == 200 and safe_result["accepted"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
