"""Public, short-lived URLs for media files.

Meta's APIs do not accept uploads for Instagram: they download the file from a public URL.
`R2Host` uploads to a private Cloudflare R2 bucket and returns a presigned URL valid for one
hour. `LocalHost` signs a URL to this server's /media route (needs PUBLIC_BASE_URL).
"""

import hashlib
import hmac
import time
from pathlib import Path
from typing import Protocol

from instapost.config import get_settings

URL_TTL_SECONDS = 3600
CONTENT_TYPES = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".png": "image/png"}


class MediaHost(Protocol):
    def url_for(self, path: Path) -> str: ...


class R2Host:
    def __init__(self):
        import boto3

        s = get_settings()
        missing = [k for k in ("r2_account_id", "r2_access_key_id", "r2_secret_access_key",
                               "r2_bucket") if not getattr(s, k)]
        if missing:
            raise RuntimeError(f"R2 media hosting needs: {', '.join(missing).upper()}")
        self.bucket = s.r2_bucket
        self.client = boto3.client(
            "s3", endpoint_url=f"https://{s.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=s.r2_access_key_id, aws_secret_access_key=s.r2_secret_access_key,
            region_name="auto")

    def url_for(self, path: Path) -> str:
        key = f"media/{path.parent.name}/{path.name}"
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs={
            "ContentType": CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")})
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=URL_TTL_SECONDS)


def _signature(rel: str, expires: int) -> str:
    secret = (get_settings().session_secret or "").encode()
    if not secret:
        raise RuntimeError("SESSION_SECRET must be set to sign media URLs")
    return hmac.new(secret, f"{rel}|{expires}".encode(), hashlib.sha256).hexdigest()


class LocalHost:
    def url_for(self, path: Path) -> str:
        s = get_settings()
        if not s.public_base_url:
            raise RuntimeError("PUBLIC_BASE_URL must be set for media_host=local")
        rel = path.resolve().relative_to(s.output_dir.resolve()).as_posix()
        expires = int(time.time()) + URL_TTL_SECONDS
        base = s.public_base_url.rstrip("/")
        return f"{base}/media/{rel}?e={expires}&s={_signature(rel, expires)}"


def verify_local(rel: str, expires: int, sig: str) -> bool:
    return expires > time.time() and hmac.compare_digest(_signature(rel, expires), sig)


def get_media_host() -> MediaHost:
    return R2Host() if get_settings().media_host == "r2" else LocalHost()
