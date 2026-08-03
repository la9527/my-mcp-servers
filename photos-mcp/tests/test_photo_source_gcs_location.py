from photos_mcp.vendor_loader import load_vendor_server


def test_gcs_location_preserves_bucket_and_prefix() -> None:
    module = load_vendor_server("photo-source")

    assert module._parse_gcs_location("gs://sample-bucket/photos/2026") == (
        "sample-bucket",
        "photos/2026",
    )
    assert module._parse_gcs_location("sample-bucket") == ("sample-bucket", "")
