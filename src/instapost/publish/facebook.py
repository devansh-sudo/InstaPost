"""Facebook Page Reels via the Pages API (start upload, hosted-file upload, finish)."""

import time

import httpx

from instapost.config import get_settings
from instapost.publish import tokens
from instapost.publish.instagram import Published, PublishError


class Facebook:
    def __init__(self, client: httpx.Client | None = None, poll_seconds: float = 10,
                 max_wait_seconds: float = 600):
        s = get_settings()
        token = tokens.get("fb_page_token")
        if not (s.fb_page_id and token):
            raise PublishError("FB_PAGE_ID and FB_PAGE_TOKEN are required")
        self.page_id = s.fb_page_id
        self.base = s.fb_api_base
        self.http = client or httpx.Client(timeout=120)
        self.token = token
        self.poll_seconds = poll_seconds
        self.max_wait = max_wait_seconds

    def _check(self, r: httpx.Response, what: str) -> dict:
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code >= 400 or "error" in body:
            msg = body.get("error", {}).get("message", r.text[:300])
            raise PublishError(tokens.redact(f"Facebook {what} {r.status_code}: {msg}"))
        return body

    def reel(self, video_url: str, description: str) -> Published:
        auth = {"Authorization": f"OAuth {self.token}"}
        start = self._check(self.http.post(f"{self.base}/{self.page_id}/video_reels",
                                           headers=auth, data={"upload_phase": "start"}), "start")
        video_id, upload_url = start["video_id"], start["upload_url"]
        self._check(self.http.post(upload_url, headers={**auth, "file_url": video_url}),
                    "upload")
        self._check(self.http.post(f"{self.base}/{self.page_id}/video_reels", headers=auth, data={
            "upload_phase": "finish", "video_id": video_id, "video_state": "PUBLISHED",
            "description": description}), "finish")

        permalink = None
        deadline = time.monotonic() + self.max_wait
        while time.monotonic() < deadline:
            info = self._check(self.http.get(f"{self.base}/{video_id}", headers=auth,
                                             params={"fields": "status,permalink_url"}), "status")
            status = info.get("status", {})
            if status.get("video_status") == "error":
                raise PublishError(f"Facebook processing failed: {status}")
            if status.get("publishing_phase", {}).get("status") == "complete" or \
                    status.get("video_status") == "ready":
                link = info.get("permalink_url")
                permalink = f"https://www.facebook.com{link}" if link and link.startswith("/") \
                    else link
                break
            time.sleep(self.poll_seconds)
        return Published(remote_id=video_id, permalink=permalink)
