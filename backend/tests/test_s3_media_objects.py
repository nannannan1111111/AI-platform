from typing import Any

import pytest

from app.media import MediaObjectPromotionFailed, S3CompatibleMediaObjects


class _RecordingS3Client:
    def __init__(self, *, persistent_exists: bool = False) -> None:
        self.persistent_exists = persistent_exists
        self.head_requests: list[dict[str, str]] = []
        self.copy_requests: list[dict[str, object]] = []
        self.delete_requests: list[dict[str, str]] = []
        self.put_requests: list[dict[str, object]] = []
        self.get_requests: list[dict[str, str]] = []
        self.objects: dict[str, bytes] = {}

    def head_object(self, **request: Any) -> object:
        self.head_requests.append({str(key): str(value) for key, value in request.items()})
        key = str(request.get("Key") or "")
        if key not in self.objects and not (self.persistent_exists and key.startswith("persistent/")):
            raise FileNotFoundError(str(request.get("Key") or ""))
        return {"ContentLength": len(self.objects.get(key, b"x")), "Metadata": {}}

    def copy_object(self, **request: Any) -> object:
        self.copy_requests.append(dict(request))
        return {"CopyObjectResult": {}}

    def delete_object(self, **request: Any) -> object:
        self.delete_requests.append({str(key): str(value) for key, value in request.items()})
        return {"DeleteMarker": True}

    def put_object(self, **request: Any) -> object:
        self.put_requests.append(dict(request))
        self.objects[str(request["Key"])] = bytes(request["Body"])
        return {}

    def get_object(self, **request: Any) -> object:
        self.get_requests.append({str(key): str(value) for key, value in request.items()})
        return {"Body": self.objects[str(request["Key"])]}


def test_s3_compatible_media_objects_deletes_by_injected_bucket_and_object_key() -> None:
    client = _RecordingS3Client()
    objects = S3CompatibleMediaObjects(client, bucket="generated-media-test")

    objects.delete("temporary/account-space-1/task-1/result.png")

    assert client.delete_requests == [
        {
            "Bucket": "generated-media-test",
            "Key": "temporary/account-space-1/task-1/result.png",
        }
    ]


def test_s3_promotion_replay_uses_existing_persistent_object_without_recopying() -> None:
    client = _RecordingS3Client(persistent_exists=True)
    objects = S3CompatibleMediaObjects(client, bucket="generated-media-test")

    objects.promote("temporary/result.png", "persistent/account-space-1/content-hash")

    assert client.head_requests == [
        {
            "Bucket": "generated-media-test",
            "Key": "persistent/account-space-1/content-hash",
        }
    ]
    assert client.copy_requests == []
    assert client.delete_requests == [
        {
            "Bucket": "generated-media-test",
            "Key": "temporary/result.png",
        }
    ]


def test_s3_media_objects_puts_and_reads_with_stable_key() -> None:
    client = _RecordingS3Client()
    objects = S3CompatibleMediaObjects(client, bucket="generated-media-test")

    stored = objects.put_temporary(
        account_space_id="account-1",
        task_id="task-1",
        result_reference="result-1",
        content=b"image-bytes",
        mime_type="image/png",
    )

    assert stored.size_bytes == len(b"image-bytes")
    assert objects.read(stored.object_key) == b"image-bytes"
    assert client.put_requests[0]["ContentType"] == "image/png"
    assert client.put_requests[0]["Metadata"] == {"content_hash": stored.content_hash}


def test_s3_promotion_does_not_copy_on_non_not_found_head_failure() -> None:
    class BrokenHeadClient(_RecordingS3Client):
        def head_object(self, **request: Any) -> object:
            raise ConnectionError("object store unavailable")

    objects = S3CompatibleMediaObjects(BrokenHeadClient(), bucket="generated-media-test")

    with pytest.raises(MediaObjectPromotionFailed):
        objects.promote("temporary/result.png", "persistent/result.png")
