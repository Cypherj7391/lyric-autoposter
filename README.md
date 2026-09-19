# Free Online Automatic Lyric Poster V3

This version is designed to run online without your computer being on.

Every 6 hours, GitHub Actions runs:
1. Search Internet Archive for audio whose metadata explicitly indicates a Creative Commons/public-domain style license.
2. Skip items already processed (V3 prototype does not persist the seen list yet).
3. Require lyrics to be supplied in the item's metadata; otherwise skip it.
4. Download the permitted audio.
5. Render a lyric video with FFmpeg.
6. Upload the video to your YouTube channel through the YouTube Data API.

## YouTube setup

You need a Google Cloud project with YouTube Data API v3 enabled and an OAuth 2.0 token authorized for `youtube.upload`.

For this prototype, put the OAuth authorized-user JSON into a GitHub Actions secret named `YOUTUBE_TOKEN_JSON`.

The upload defaults to PRIVATE for safety. Change the workflow environment only after testing.

## GitHub setup

1. Create a PUBLIC GitHub repository (public Actions are generally free within GitHub's applicable limits).
2. Upload all files from this project.
3. Add repository secret `YOUTUBE_TOKEN_JSON`.
4. Run the workflow manually once.
5. The scheduled workflow then runs every 6 hours.

## Important limitations

- "Free" is not unlimited: GitHub Actions has usage/retention limits.
- The scheduler may be delayed; it is not a guaranteed real-time service.
- The source finder intentionally avoids downloading random copyrighted songs.
- Lyrics are only taken when they are already present in the source item's metadata. It does not scrape copyrighted lyric sites.
- YouTube may restrict uploads from unverified API projects to private viewing until the project passes Google's audit.
- For a real production bot, persist processed identifiers so it never repeats an item.

## Safety/copyright

Only publish material that you are authorized to publish. The bot is deliberately limited to sources/metadata that indicate reuse rights.
