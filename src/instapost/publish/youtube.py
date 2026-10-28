"""YouTube Shorts upload with the official YouTube Data API v3.

One-time login: `instapost youtube-login` (see docs/SETUP_META_YOUTUBE.md). The refresh token
is stored in secrets/youtube_token.json with mode 600.
"""

import os
from pathlib import Path

from instapost.config import get_settings
from instapost.publish.instagram import Published, PublishError

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _client_secret() -> Path:
    return get_settings().secrets_dir / "youtube_client_secret.json"


def _token_file() -> Path:
    return get_settings().secrets_dir / "youtube_token.json"


def is_configured() -> bool:
    return _token_file().exists()


def login(port: int = 8085, bind_addr: str | None = None, paste: bool = True,
          read=input) -> None:
    """Connect the channel once.

    paste=True (default, no tunnel needed): you approve in any browser; it then fails to open
    http://localhost:<port>/?code=... and you paste that address back here. The one-time code
    in it only works together with this run's secret (PKCE), and only once.
    paste=False: a local web server receives the redirect (needs an SSH tunnel to the server).
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not _client_secret().exists():
        raise PublishError(f"put the OAuth client JSON at {_client_secret()}")
    flow = InstalledAppFlow.from_client_secrets_file(str(_client_secret()), SCOPES)
    if paste:
        flow.redirect_uri = f"http://localhost:{port}/"
        # prompt=consent: Google returns a refresh token even if you approved before.
        url, _ = flow.authorization_url(access_type="offline", prompt="consent")
        print("1. Open this link in a browser (your Mac or phone), log in with the channel's\n"
              "   Google account, pick the channel, allow all three permissions:\n\n"
              f"{url}\n\n"
              "2. The browser then shows \"This site can't be reached\" for localhost. That is\n"
              "   expected. Copy the whole address from the address bar (it contains code=...)\n"
              "   and paste it here.\n")
        answer = read("Address: ").strip()
        if "code=" not in answer:
            raise PublishError("that is not the address with code=... from the browser; "
                               "run youtube-login again and copy the full address")
        if "error=" in answer:
            raise PublishError(f"Google refused: {answer.split('error=')[1].split('&')[0]}")
        # oauthlib insists on https; the loopback address is http by design.
        flow.fetch_token(authorization_response=answer.replace("http://", "https://", 1))
        creds = flow.credentials
    else:
        # bind_addr 0.0.0.0 inside Docker, so the published port reaches it; the redirect URL
        # still says localhost, which your browser reaches through the SSH tunnel.
        creds = flow.run_local_server(host="localhost", bind_addr=bind_addr, port=port,
                                      open_browser=False, prompt="consent",
                                      authorization_prompt_message="Open this link in your "
                                      "browser (with the SSH tunnel running):\n{url}\n")
    if not creds.refresh_token:
        raise PublishError("Google returned no refresh token; run youtube-login again")
    path = _token_file()
    path.write_text(creds.to_json())
    os.chmod(path, 0o600)


def credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    if not is_configured():
        raise PublishError("YouTube is not connected; run `instapost youtube-login`")
    creds = Credentials.from_authorized_user_file(str(_token_file()), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        _token_file().write_text(creds.to_json())
    return creds


def _service(api: str = "youtube", version: str = "v3"):
    from googleapiclient.discovery import build

    return build(api, version, credentials=credentials(), cache_discovery=False)


def channel() -> dict:
    """The channel the stored login uploads to (a cheap live check)."""
    items = _service().channels().list(part="snippet,statistics", mine=True).execute()
    items = items.get("items", [])
    if not items:
        raise PublishError("this Google account has no YouTube channel; log in with the "
                           "channel's account (or pick the channel when Google asks)")
    c = items[0]
    return {"id": c["id"], "title": c["snippet"]["title"],
            "subscribers": c.get("statistics", {}).get("subscriberCount")}


def upload_short(video: Path, title: str, description: str, tags: list[str]) -> Published:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {"title": title[:100], "description": description[:4900],
                    "tags": [t.lstrip("#") for t in tags][:15], "categoryId": "22",
                    "defaultLanguage": "hi", "defaultAudioLanguage": "hi"},
        "status": {"privacyStatus": get_settings().youtube_privacy,
                   "selfDeclaredMadeForKids": False,
                   # Backgrounds are AI-generated: disclose honestly.
                   "containsSyntheticMedia": True},
    }
    try:
        request = _service().videos().insert(
            part="snippet,status", body=body,
            media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True))
        response = None
        while response is None:
            _, response = request.next_chunk()
    except HttpError as e:
        raise PublishError(f"YouTube upload failed: {e.status_code} {e.reason}") from e
    vid = response["id"]
    return Published(remote_id=vid, permalink=f"https://youtube.com/shorts/{vid}")
