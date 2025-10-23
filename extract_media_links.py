import re

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif", ".webp")
VIDEO_DOMAINS = ("youtube.com", "youtu.be", "vimeo.com", "reddit.com/video")

def extract_media_links(text):
    """Return a list of media URLs (images/videos) found in the comment text."""
    urls = re.findall(r"https?://\S+", text)
    media = []
    for url in urls:
        lower = url.lower()
        if lower.endswith(IMAGE_EXTS) or any(d in lower for d in VIDEO_DOMAINS):
            media.append(url)
    return media
