"""Verify the configured GCS bucket with an upload/read/delete round trip."""

from datetime import UTC, datetime
from uuid import uuid4

from app.services.storage import get_blob_storage


def main() -> None:
    storage = get_blob_storage()
    name = f"smoke-tests/{uuid4()}.txt"
    payload = f"LimON GCS smoke test at {datetime.now(UTC).isoformat()}".encode()
    uploaded = None

    try:
        uploaded = storage.upload(name, payload, content_type="text/plain; charset=utf-8")
        downloaded = storage.download(name)
        if downloaded != payload:
            raise RuntimeError("GCS smoke test downloaded different content than it uploaded")
        print(f"GCS smoke test passed: gs://{uploaded.bucket}/{uploaded.name}")
    finally:
        if uploaded is not None and uploaded.generation is not None:
            storage.delete(uploaded.name, generation=uploaded.generation)


if __name__ == "__main__":
    main()
