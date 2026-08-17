from typing import Any

from app.media import S3CompatibleMediaObjects


class _RecordingS3Client:
    def __init__(self, *, persistent_exists: bool = False) -> None:
        self.persistent_exists = persistent_exists
        self.head_requests: list[dict[str, str]] = []
        self.copy_requests: list[dict[str, object]] = []
        self.delete_requests: list[dict[str, str]] = []

    def head_object(self, **request: Any) -> object:
        self.head_requests.append({str(key): str(value) for key, value in request.items()})
        if not self.persistent_exists:
            raise FileNotFoundError(str(request.get("Key") or ""))
        return {"ContentLength": 1}

    def copy_object(self, **request: Any) -> object:
        self.copy_requests.append(dict(request))
        return {"CopyObjectResult": {}}

    def delete_object(self, **request: Any) -> object:
        self.delete_requests.append({str(key): str(value) for key, value in request.items()})
        return {"DeleteMarker": True}


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
