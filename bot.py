import os, re, json, subprocess, tempfile, hashlib
from pathlib import Path
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

IA_API = "https://archive.org/advancedsearch.php"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def search_legal_audio(rows=10):
    # We only select items whose metadata explicitly says a reusable license.
    q = '(licenseurl:"*creativecommons*" OR licenseurl:"*publicdomain*") AND mediatype:audio'
    params = {
        "q": q, "fl[]": ["identifier,title,creator,licenseurl,description,year"],
        "rows": rows, "page": 1, "output": "json"
    }
    r = requests.get(IA_API, params=params, timeout=30)
    r.raise_for_status()
    docs = r.json()["response"]["docs"]
    return [d for d in docs if d.get("identifier") and d.get("licenseurl")]

def get_item(identifier):
    r = requests.get(f"https://archive.org/metadata/{identifier}", timeout=30)
    r.raise_for_status()
    return r.json()

def choose_audio_file(meta):
    files = meta.get("files", [])
    choices = []
    for f in files:
        name = f.get("name","")
        fmt = str(f.get("format","")).lower()
        if name.lower().endswith((".mp3",".ogg",".wav",".flac")) and "metadata" not in name.lower():
            choices.append((name, f.get("size",0)))
    if not choices:
        return None
    # Prefer MP3.
    choices.sort(key=lambda x: (not x[0].lower().endswith(".mp3"), len(x[0])))
    return choices[0][0]

def extract_lyrics(meta):
    # Only use lyrics that are already supplied as item metadata/text.
    for key in ("lyrics","lyric","description"):
        val = meta.get("metadata",{}).get(key)
        if isinstance(val, str) and len(val.split()) >= 8:
            # Avoid treating a tiny catalog description as lyrics.
            if any(w in val.lower() for w in ["verse", "chorus", "\n"]):
                return val
    return None

def download_audio(identifier, filename, out):
    url = f"https://archive.org/download/{identifier}/{filename}"
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(1024*1024):
                if chunk:
                    f.write(chunk)

def render(audio, lyrics, title, out):
    lyric_file = out.with_suffix(".txt")
    lyric_file.write_text(lyrics, encoding="utf-8")
    # Simple readable lyric video. One metadata lyric block per video.
    vf = (
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        "text='{}':fontcolor=white:fontsize=52:x=(w-text_w)/2:y=90,"
        "drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf:"
        "textfile='{}':fontcolor=white:fontsize=40:line_spacing=14:"
        "box=1:boxcolor=black@0.45:boxborderw=30:x=(w-text_w)/2:y=(h-text_h)/2"
    ).format(
        title.replace("\\","\\\\").replace(":","\\:").replace("'","\\'"),
        str(lyric_file).replace("\\","/").replace(":","\\:")
    )
    subprocess.run([
        "ffmpeg","-y","-i",str(audio),
        "-f","lavfi","-i","color=c=#111827:s=1920x1080:r=30",
        "-filter_complex",f"[1:v]{vf}[v]","-map","[v]","-map","0:a:0",
        "-c:v","libx264","-preset","veryfast","-crf","23",
        "-c:a","aac","-b:a","192k","-shortest","-movflags","+faststart",str(out)
    ], check=True)

def youtube_upload(video, title, description, privacy="private"):
    data = json.loads(os.environ["YOUTUBE_TOKEN_JSON"])
    creds = Credentials.from_authorized_user_info(data, SCOPES)
    yt = build("youtube","v3",credentials=creds)
    body = {
        "snippet":{"title":title[:100],"description":description[:5000],"categoryId":"10"},
        "status":{"privacyStatus":privacy,"selfDeclaredMadeForKids":False}
    }
    media = MediaFileUpload(str(video), mimetype="video/mp4", resumable=True)
    req = yt.videos().insert(part="snippet,status",body=body,media_body=media)
    response = None
    while response is None:
        _, response = req.next_chunk()
    return response["id"]

def main():
    seen = set(os.environ.get("SEEN_IDS","").split(",")) if os.environ.get("SEEN_IDS") else set()
    candidates = search_legal_audio(20)
    picked = None
    for item in candidates:
        if item["identifier"] not in seen:
            picked = item
            break
    if not picked:
        print("No new eligible audio item found.")
        return

    meta = get_item(picked["identifier"])
    lyrics = extract_lyrics(meta)
    filename = choose_audio_file(meta)
    if not lyrics or not filename:
        print("Skipping: no embedded lyrics metadata or supported audio file.")
        print(picked["identifier"])
        return

    title = f'{picked.get("creator","Unknown Artist")} - {picked.get("title","Untitled")} (Lyrics)'
    with tempfile.TemporaryDirectory() as td:
        audio = Path(td) / Path(filename).name
        video = Path(td) / "lyric_video.mp4"
        download_audio(picked["identifier"], filename, audio)
        render(audio, lyrics, title, video)
        desc = (
            "Automatically generated lyric video for legally reusable audio.\n\n"
            f"Source: https://archive.org/details/{picked['identifier']}\n"
            f"License: {picked.get('licenseurl','')}"
        )
        vid = youtube_upload(video, title, desc, os.environ.get("YOUTUBE_PRIVACY","private"))
        print("UPLOADED", vid)
        print("SOURCE_ID", picked["identifier"])

if __name__ == "__main__":
    main()
