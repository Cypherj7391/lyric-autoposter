import os
import re
import json
import subprocess
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

WORK = Path("work")
WORK.mkdir(exist_ok=True)

YOUTUBE_TOKEN_JSON = os.environ.get("YOUTUBE_TOKEN_JSON", "")
YOUTUBE_PRIVACY = os.environ.get("YOUTUBE_PRIVACY", "private")

IA_ADVANCED = "https://archive.org/advancedsearch.php"
IA_METADATA = "https://archive.org/metadata/"
IA_DOWNLOAD = "https://archive.org/download/"

LICENSE_WORDS = (
    "creativecommons.org",
    "creativecommons",
    "publicdomain",
    "public domain",
    "cc0",
)

AUDIO_EXTS = {".mp3", ".ogg", ".wav", ".flac", ".m4a", ".aac"}

session = requests.Session()
session.headers.update({"User-Agent": "lyric-autoposter/1.1"})


def clean_text(value):
    if isinstance(value, list):
        value = "\n".join(str(x) for x in value)
    if value is None:
        return ""
    value = re.sub(r"<[^>]+>", " ", str(value))
    value = re.sub(r"\r\n?", "\n", value)
    return value.strip()


def has_reusable_license(metadata):
    fields = []
    for key in ("licenseurl", "license", "rights", "description"):
        fields.append(clean_text(metadata.get(key, "")).lower())
    combined = " ".join(fields)
    return any(word in combined for word in LICENSE_WORDS)


def extract_lyrics(metadata):
    for key in ("lyrics", "lyric"):
        text = clean_text(metadata.get(key, ""))
        if len(text.split()) >= 20:
            return text

    description = clean_text(metadata.get("description", ""))
    if len(description.split()) >= 30:
        lower = description.lower()
        if any(x in lower for x in ("lyrics", "lyric", "verse", "chorus", "refrain")):
            return description

    return ""


def search_candidates():
    for page in range(1, 6):
        params = {
            "q": "mediatype:audio",
            "fl[]": ["identifier", "title"],
            "rows": 100,
            "page": page,
            "output": "json",
            "sort[]": "downloads desc",
        }

        try:
            response = session.get(IA_ADVANCED, params=params, timeout=30)
            response.raise_for_status()
            docs = response.json().get("response", {}).get("docs", [])
        except Exception as exc:
            print(f"Archive search error on page {page}: {exc}")
            continue

        if not docs:
            break

        print(f"Checked Archive search page {page}: {len(docs)} audio records")

        for doc in docs:
            identifier = doc.get("identifier")
            if identifier:
                yield identifier


def get_item(identifier):
    response = session.get(IA_METADATA + identifier, timeout=45)
    response.raise_for_status()
    return response.json()


def choose_audio(metadata):
    candidates = []

    for item in metadata.get("files", []):
        name = item.get("name", "")
        ext = Path(name).suffix.lower()
        if ext in AUDIO_EXTS:
            try:
                size = int(item.get("size", 0) or 0)
            except (TypeError, ValueError):
                size = 0
            candidates.append((ext, size, name))

    if not candidates:
        return None

    priority = {".mp3": 0, ".m4a": 1, ".ogg": 2, ".wav": 3, ".flac": 4, ".aac": 5}
    candidates.sort(key=lambda x: (priority.get(x[0], 99), -x[1]))

    for ext, size, name in candidates:
        if size == 0 or size >= 20_000:
            return name

    return candidates[0][2]


def download_audio(identifier, filename):
    safe_name = Path(filename).name
    output = WORK / safe_name
    url = IA_DOWNLOAD + identifier + "/" + safe_name

    print(f"Downloading: {url}")

    with session.get(url, stream=True, timeout=90) as response:
        response.raise_for_status()
        with open(output, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)

    return output


def make_video(audio_path, title, lyrics):
    video_path = WORK / "lyric_video.mp4"

    # Use FFmpeg's drawtext textfile feature instead of the previous
    # subtitles/force_style filter. This avoids the filter parsing bug
    # seen in GitHub Actions.
    lyrics_path = WORK / "lyrics_display.txt"
    lyrics_path.write_text(lyrics, encoding="utf-8")

    # Escape characters that are special to FFmpeg's filter parser.
    safe_title = (
        title.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "%%")
        .replace("[", "\\[")
        .replace("]", "\\]")
    )
    safe_lyrics_path = str(lyrics_path).replace("\\", "\\\\").replace(":", "\\:")

    # The lyrics are displayed as a centered scrolling text block.
    # This is deliberately simple and robust on GitHub's Ubuntu runner.
    filter_graph = (
        "[1:v]"
        "drawtext="
        "fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"text='{safe_title}':"
        "fontcolor=white:"
        "fontsize=48:"
        "x=(w-text_w)/2:"
        "y=70:"
        "box=1:"
        "boxcolor=black@0.45:"
        "boxborderw=18,"
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
        f"textfile='{safe_lyrics_path}':"
        "fontcolor=white:"
        "fontsize=30:"
        "line_spacing=12:"
        "x=(w-text_w)/2:"
        "y=h/2-text_h/2"
        "[v]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(audio_path),
        "-f", "lavfi",
        "-i", "color=c=0x101018:s=1920x1080:r=30",
        "-filter_complex", filter_graph,
        "-map", "[v]",
        "-map", "0:a:0",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(video_path),
    ]

    print("Rendering lyric video...")
    print("FFmpeg filter mode: drawtext (stable mode)")
    subprocess.run(cmd, check=True)

    return video_path


def youtube_upload(video_path, title, description):
    if not YOUTUBE_TOKEN_JSON:
        raise RuntimeError("YOUTUBE_TOKEN_JSON GitHub secret is missing.")

    token_info = json.loads(YOUTUBE_TOKEN_JSON)
    credentials = Credentials.from_authorized_user_info(
        token_info,
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )

    youtube = build("youtube", "v3", credentials=credentials)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "categoryId": "10",
        },
        "status": {
            "privacyStatus": YOUTUBE_PRIVACY,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
    )

    print("Uploading to YouTube...")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        _, response = request.next_chunk()

    return response["id"]


def main():
    print("Searching for openly licensed audio with lyrics...")

    for identifier in search_candidates():
        try:
            metadata = get_item(identifier)
            meta = metadata.get("metadata", {})

            if not has_reusable_license(meta):
                continue

            lyrics = extract_lyrics(meta)
            if not lyrics:
                continue

            audio_name = choose_audio(metadata)
            if not audio_name:
                continue

            title = clean_text(meta.get("title", identifier)) or identifier

            print(f"FOUND ELIGIBLE ITEM: {identifier} — {title}")

            audio_path = download_audio(identifier, audio_name)
            video_path = make_video(audio_path, title, lyrics)

            description = (
                f"Source archive item: {identifier}\n"
                f"Title: {title}\n"
                "This video was generated automatically from an item whose "
                "archive metadata identifies a Creative Commons/public-domain "
                "license. Verify the source license before publishing publicly."
            )

            video_id = youtube_upload(video_path, title, description)

            print(f"SUCCESS! Uploaded YouTube video: {video_id}")
            print(f"Source archive item: {identifier}")
            return

        except Exception as exc:
            print(f"Skipping {identifier}: {exc}")

    print("No new eligible audio item found.")


if __name__ == "__main__":
    main()
