"""End-to-end check that GCS audio upload presigning actually works.

Unlike the unit tests (which mock the GCS boundary), this touches real GCP:
it uses your Application Default Credentials to sign a URL and then PUTs a
real payload to it. Run it after configuring LIMON_GCS_BUCKET etc. in .env:

    uv run python scripts/verify_gcs_upload.py

Success prints the object key and a 200; you'll then see the object in the
bucket (console or `gcloud storage ls`). It creates one small test object
under audio/verify-<uuid>/... which you can delete afterwards.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

from app.core.config import get_settings
from app.services import storage as storage_service

# A tiny valid-enough payload; content-type must match what we presign for.
_CONTENT_TYPE = "audio/mp4"
_PAYLOAD = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32  # minimal m4a-ish bytes


def main() -> int:
    settings = get_settings()
    if settings.gcs_bucket is None:
        print("FAIL: LIMON_GCS_BUCKET is not set (check your .env).")
        return 1

    print(f"Bucket:        {settings.gcs_bucket}")
    print(f"Signer SA:     {settings.gcs_signer_service_account or '(ADC identity)'}")

    print("\n1. Generating a signed upload URL via the real service...")
    presigned = storage_service.presign_audio_upload(user_id="verify", content_type=_CONTENT_TYPE)
    print(f"   object_key:  {presigned.object_key}")
    print(f"   expires_at:  {presigned.expires_at.isoformat()}")
    print(f"   url:         {presigned.upload_url[:90]}...")

    print("\n2. PUTting a test payload straight to GCS...")
    request = urllib.request.Request(
        presigned.upload_url,
        data=_PAYLOAD,
        method="PUT",
        headers={"Content-Type": _CONTENT_TYPE},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        print(f"   HTTP {exc.code}")
        print(f"FAIL: upload rejected.\n{exc.read().decode(errors='replace')[:500]}")
        return 1
    print(f"   HTTP {status}")

    print("\nSUCCESS ✅  The file is now in the bucket. Verify with:")
    print(f"   gcloud storage ls gs://{settings.gcs_bucket}/{presigned.object_key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
