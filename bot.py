import os, re, json, subprocess
from pathlib import Path
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

WORK = Path('work'); WORK.mkdir(exist_ok=True)
YOUTUBE_TOKEN_JSON = os.environ.get('YOUTUBE_TOKEN_JSON','')
YOUTUBE_PRIVACY = os.environ.get('YOUTUBE_PRIVACY','private')
IA_ADVANCED='https://archive.org/advancedsearch.php'; IA_METADATA='https://archive.org/metadata/'; IA_DOWNLOAD='https://archive.org/download/'
LICENSE_WORDS=('creativecommons.org','creativecommons','publicdomain','public domain','cc0')
AUDIO_EXTS={'.mp3','.ogg','.wav','.flac','.m4a','.aac'}
s=requests.Session(); s.headers.update({'User-Agent':'lyric-autoposter/1.1'})

def clean(v):
    if isinstance(v,list): v='\n'.join(map(str,v))
    if v is None:return ''
    v=re.sub(r'<[^>]+>',' ',str(v)); return re.sub(r'\r\n?','\n',v).strip()

def reusable(meta):
    x=' '.join(clean(meta.get(k,'' )).lower() for k in ('licenseurl','license','rights','description'))
    return any(w in x for w in LICENSE_WORDS)

def lyrics(meta):
    for k in ('lyrics','lyric'):
        t=clean(meta.get(k,''))
        if len(t.split())>=20:return t
    t=clean(meta.get('description',''))
    if len(t.split())>=30 and any(w in t.lower() for w in ('lyrics','lyric','verse','chorus','refrain')):return t
    return ''

def candidates():
    for page in range(1,6):
        p={'q':'mediatype:audio','fl[]':['identifier','title'],'rows':100,'page':page,'output':'json','sort[]':'downloads desc'}
        try:
            r=s.get(IA_ADVANCED,params=p,timeout=30); r.raise_for_status(); docs=r.json().get('response',{}).get('docs',[])
        except Exception as e:
            print(f'Archive search error on page {page}: {e}'); continue
        if not docs: break
        print(f'Checked Archive search page {page}: {len(docs)} audio records')
        for d in docs:
            if d.get('identifier'): yield d['identifier']

def item(i):
    r=s.get(IA_METADATA+i,timeout=30); r.raise_for_status(); return r.json()

def audio(meta):
    cs=[]
    for f in meta.get('files',[]):
        n=f.get('name',''); e=Path(n).suffix.lower()
        if e in AUDIO_EXTS: cs.append((e,int(f.get('size',0) or 0),n))
    if not cs:return None
    pri={'.mp3':0,'.m4a':1,'.ogg':2,'.wav':3,'.flac':4,'.aac':5}; cs.sort(key=lambda x:(pri.get(x[0],99),-x[1]))
    return next((n for e,z,n in cs if z==0 or z>=20000),cs[0][2])

def download(i,n):
    n=Path(n).name; out=WORK/n; url=IA_DOWNLOAD+i+'/'+n; print('Downloading:',url)
    with s.get(url,stream=True,timeout=60) as r:
        r.raise_for_status()
        with open(out,'wb') as f:
            for c in r.iter_content(1024*1024):
                if c:f.write(c)
    return out

def make_video(a,title,lyr):
    v=WORK/'lyric_video.mp4'; lp=WORK/'lyrics.txt'; lp.write_text(lyr,encoding='utf-8')
    title=title.replace('\\','\\\\').replace("'","\\'").replace(':','\\:')
    sub=str(lp).replace('\\','\\\\').replace(':','\\:')
    vf=("drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:text='%s':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=70:box=1:boxcolor=black@0.45:boxborderw=18," % title)+("subtitles=%s:force_style='FontName=DejaVu Sans,FontSize=26,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=10,MarginV=180'" % sub)
    cmd=['ffmpeg','-y','-i',str(a),'-f','lavfi','-i','color=c=0x101018:s=1920x1080:r=30','-filter_complex',f'[1:v]{vf}[v]','-map','[v]','-map','0:a:0','-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-shortest',str(v)]
    print('Rendering lyric video...'); subprocess.run(cmd,check=True); return v

def upload(v,title,desc):
    if not YOUTUBE_TOKEN_JSON: raise RuntimeError('YOUTUBE_TOKEN_JSON GitHub secret is missing.')
    c=Credentials.from_authorized_user_info(json.loads(YOUTUBE_TOKEN_JSON),scopes=['https://www.googleapis.com/auth/youtube.upload'])
    yt=build('youtube','v3',credentials=c)
    body={'snippet':{'title':title[:100],'description':desc[:5000],'categoryId':'10'},'status':{'privacyStatus':YOUTUBE_PRIVACY,'selfDeclaredMadeForKids':False}}
    req=yt.videos().insert(part='snippet,status',body=body,media_body=MediaFileUpload(str(v),mimetype='video/mp4',resumable=True))
    resp=None
    while resp is None: _,resp=req.next_chunk()
    return resp['id']

def main():
    print('Searching for openly licensed audio with lyrics...')
    for ident in candidates():
        try:
            data=item(ident); meta=data.get('metadata',{})
            if not reusable(meta): continue
            lyr=lyrics(meta)
            if not lyr: continue
            an=audio(data)
            if not an: continue
            title=clean(meta.get('title',ident)) or ident
            print(f'FOUND ELIGIBLE ITEM: {ident} — {title}')
            ap=download(ident,an); vp=make_video(ap,title,lyr)
            desc=f'Source archive item: {ident}\nTitle: {title}\nThis video was generated from an item whose archive metadata identifies a Creative Commons/public-domain license. Verify the source license before publishing publicly.'
            vid=upload(vp,title,desc); print('SUCCESS! Uploaded YouTube video:',vid); return
        except Exception as e: print(f'Skipping {ident}: {e}')
    print('No new eligible audio item found.')
    print('The bot searched multiple pages of openly licensed audio and could not find an item containing both usable audio and lyrics in its metadata.')

if __name__=='__main__': main()
