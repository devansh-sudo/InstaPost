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


def login(port: int = 8085) -> None:
    """Run once on the VPS through an SSH tunnel: ssh -L 8085:localhost:8085 <vps>."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not _client_secret().exists():
        raise PublishError(f"put the OAuth client JSON at {_client_secret()}")
    flow = InstalledAppFlow.from_client_secrets_file(str(_client_secret()), SCOPES)
    creds = flow.run_local_server(host="localhost", port=port, open_browser=False,
                                  authorization_prompt_message="Open this link in your browser "
                                  "(with the SSH tunnel running):\n{url}\n")
    path = _token_file()
    path.write_text(creds.to_json())
    os.chmod(path, 0o600)


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not is_configured():
        raise PublishError("YouTube is not connected; run `instapost youtube-login`")
    creds = Credentials.from_authorized_user_file(str(_token_file()), SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        _token_file().write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


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
