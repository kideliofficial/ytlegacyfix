import os, struct

# ---- tiny protobuf encoder, schemas taken from player.txt ----
def varint(n):
    if n < 0: n += 1 << 64
    out = bytearray()
    while True:
        b = n & 0x7f; n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n: return bytes(out)

def enc(schema, **kw):
    out = b""
    for name, val in kw.items():
        if val is None: continue
        num, typ = schema[name]
        for v in (val if isinstance(val, list) else [val]):
            if typ == 0x0:                       out += varint(num << 3) + varint(1 if v else 0)
            elif typ in (7, 8, 0xb, 0xc, 0x11):  out += varint(num << 3) + varint(int(v))
            elif typ == 0x3:                     out += varint(num << 3 | 5) + struct.pack("<f", v)
            elif typ == 0xe:
                b = v.encode(); out += varint(num << 3 | 2) + varint(len(b)) + b
            elif typ == 0xf:                     out += varint(num << 3 | 2) + varint(len(v)) + v
    return out

FORMAT = dict(itag=(1,7), url=(2,0xe), mimeType=(5,0xe), bitrate=(6,7), width=(7,7),
              height=(8,7), lastModified=(11,8), contentLength=(12,8), quality=(16,0xe))
STREAMING = dict(expiresInSeconds=(1,8), formatsArray=(2,0xf))
PLAYABILITY = dict(status=(1,0x11), reason=(2,0xe), playableInBackground=(4,0))
DETAILS = dict(videoId=(1,0xe), title=(15,0xe), lengthSeconds=(16,0xc), isLive=(17,0),
               channelId=(19,0xe), shortDescription=(21,0xe), isCrawlable=(22,0),
               allowRatings=(31,0), viewCount=(32,0xe), author=(33,0xe),
               isLiveDefaultBroadcast=(34,0), isLowLatencyLiveStream=(35,0), isPrivate=(37,0))
PLAYER = dict(playabilityStatus=(2,0xf), streamingData=(4,0xf), videoDetails=(11,0xf))

STATUS_OK = int(os.environ.get("YTLF_STATUS_OK", "0"))   # 0 matches the captured last-known-working response
STATUS_ERROR = 2   # guess (UNKNOWN=0, OK=1, ERROR=2); unverified   # VERIFY with: strings -a ytapp/Payload/YouTube.app/YouTube | grep -E "PlayabilityStatus_Status" | sort -u

def build_player(video_id, info, stream_url):
    """Legacy YTIPlayerResponse (protobuf bytes). info = yt-dlp info dict, stream_url = URL the phone can reach."""
    fmt = enc(FORMAT, itag=18, url=stream_url,
              mimeType='video/mp4; codecs="avc1.42001E, mp4a.40.2"',
              bitrate=int((info.get("tbr") or 500) * 1000),
              width=info.get("width") or 640, height=info.get("height") or 360,
              contentLength=info.get("filesize") or info.get("filesize_approx") or 0,
              quality="medium")
    return enc(PLAYER,
        playabilityStatus=enc(PLAYABILITY, status=STATUS_OK, playableInBackground=False),
        streamingData=enc(STREAMING, expiresInSeconds=21000, formatsArray=[fmt]),
        videoDetails=enc(DETAILS, videoId=video_id, title=info.get("title") or "",
            lengthSeconds=int(info.get("duration") or 0), isLive=False,
            channelId=info.get("channel_id") or "", author=info.get("uploader") or "",
            shortDescription=(info.get("description") or "")[:500], isCrawlable=True,
            allowRatings=True, viewCount=str(info.get("view_count") or 0),
            isLiveDefaultBroadcast=False, isLowLatencyLiveStream=False, isPrivate=False))


def build_player_error(video_id, reason):
    """Legacy YTIPlayerResponse that says 'cannot play' so the app shows a message instead of choking."""
    return enc(PLAYER,
        playabilityStatus=enc(PLAYABILITY, status=STATUS_ERROR, reason=reason, playableInBackground=False),
        videoDetails=enc(DETAILS, videoId=video_id, title="", isLive=False))
