"""Instagram publishing via the official Instagram API with Instagram Login.

Flow for every format: create a media container from a public URL, wait until Instagram has
processed it, then publish the container. The token is sent in the Authorization header, never
in the URL, so it cannot leak through error messages or proxy logs.
"""

import time
from dataclasses import dataclass

import httpx

from instapost.config import get_settings
from instapost.publish import tokens


class PublishError(RuntimeError):
    pass


@dataclass
class Published:
    remote_id: str
    permalink: str | None
    container_id: str | None = None


class Instagram:
    def __init__(self, client: httpx.Client | None = None, poll_seconds: float = 10,
                 max_wait_seconds: float = 900):
        s = get_settings()
        token = tokens.get("ig_access_token")
        if not (s.ig_user_id and token):
            raise PublishError("IG_USER_ID and IG_ACCESS_TOKEN are required")
        self.user_id = s.ig_user_id
        self.base = s.ig_api_base
        self.http = client or httpx.Client(timeout=60)
        self.headers = {"Authorization": f"Bearer {token}"}
        self.poll_seconds = poll_seconds
        self.max_wait = max_wait_seconds

    def _call(self, method: str, path: str, **params) -> dict:
        r = self.http.request(method, f"{self.base}/{path}", headers=self.headers,
                              params=params if method == "GET" else None,
                              data=params if method != "GET" else None)
        body = r.json() if r.headers.get("content-type", "").startswith(("application/json",
                                                                         "text/javascript")) else {}
        if r.status_code >= 400 or "error" in body:
            err = body.get("error", {})
            raise PublishError(tokens.redact(
                f"Instagram {r.status_code}: {err.get('message', r.text[:300])} "
                f"(code {err.get('code')}, subcode {err.get('error_subcode')})"))
        return body

    # --- containers ----------------------------------------------------------------------

    def _wait(self, container_id: str) -> None:
        deadline = time.monotonic() + self.max_wait
        while True:
            status = self._call("GET", container_id, fields="status_code,status")
            code = status.get("status_code")
            if code in ("FINISHED", "PUBLISHED"):
                return
            if code in ("ERROR", "EXPIRED"):
                raise PublishError(f"container {container_id} {code}: {status.get('status')}")
            if time.monotonic() > deadline:
                raise PublishError(f"container {container_id} still {code} after waiting")
            time.sleep(self.poll_seconds)

    def _publish(self, container_id: str) -> Published:
        self._wait(container_id)
        media_id = self._call("POST", f"{self.user_id}/media_publish",
                              creation_id=container_id)["id"]
        permalink = None
        try:
            permalink = self._call("GET", media_id, fields="permalink").get("permalink")
        except PublishError:
            pass  # published fine; the link is only a convenience
        return Published(remote_id=media_id, permalink=permalink, container_id=container_id)

    def resume(self, container_id: str) -> Published:
        """Publish a container created in an earlier, interrupted attempt."""
        return self._publish(container_id)

    def reel(self, video_url: str, caption: str, cover_url: str | None = None,
             on_container=None) -> Published:
        params = {"media_type": "REELS", "video_url": video_url, "caption": caption,
                  "share_to_feed": "true"}
        if cover_url:
            params["cover_url"] = cover_url
        cid = self._call("POST", f"{self.user_id}/media", **params)["id"]
        if on_container:
            on_container(cid)
        return self._publish(cid)

    def carousel(self, image_urls: list[str], caption: str, on_container=None) -> Published:
        if not 2 <= len(image_urls) <= 10:
            raise PublishError("a carousel needs 2 to 10 images")
        children = []
        for url in image_urls:
            child = self._call("POST", f"{self.user_id}/media", image_url=url,
                               is_carousel_item="true")["id"]
            self._wait(child)
            children.append(child)
        cid = self._call("POST", f"{self.user_id}/media", media_type="CAROUSEL",
                         children=",".join(children), caption=caption)["id"]
        if on_container:
            on_container(cid)
        return self._publish(cid)

    def story(self, image_url: str, on_container=None) -> Published:
        cid = self._call("POST", f"{self.user_id}/media", media_type="STORIES",
                         image_url=image_url)["id"]
        if on_container:
            on_container(cid)
        return self._publish(cid)

    # --- account -------------------------------------------------------------------------

    def publishing_limit(self) -> dict:
        data = self._call("GET", f"{self.user_id}/content_publishing_limit",
                          fields="quota_usage,config").get("data", [{}])
        return data[0] if data else {}

    def me(self) -> dict:
        return self._call("GET", "me", fields="user_id,username,account_type,followers_count")


def refresh_token(client: httpx.Client | None = None) -> int:
    """Exchange the current long-lived token for a fresh 60-day one. Returns seconds left."""
    token = tokens.get("ig_access_token")
    if not token:
        raise PublishError("no Instagram token to refresh")
    http = client or httpx.Client(timeout=30)
    r = http.get("https://graph.instagram.com/refresh_access_token",
                 params={"grant_type": "ig_refresh_token", "access_token": token})
    if r.status_code >= 400:
        raise PublishError(tokens.redact(f"token refresh failed {r.status_code}: {r.text[:300]}"))
    body = r.json()
    tokens.save("ig_access_token", body["access_token"], body.get("expires_in"))
    return int(body.get("expires_in", 0))
