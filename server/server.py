"""
YTLegacyFix server.
 - Reverse-proxies the old YouTube app's requests to real YouTube (host in X-Orig-Host)
 - Logs every request/response to capture.jsonl (secrets redacted) so protocol quirks can be fixed
 - /yt/stream/<id>.mp4 : resolves a progressive MP4 with yt-dlp and proxies it with Range support
 - /yt/play?v=<id>     : browser test page for the stream endpoint
Run:  YTLF_PUBLIC_BASE=http://<LAN-IP>:8080 python server.py
"""
import os, json, time, base64, logging, glob, threading
from flask import Flask, request, Response, abort, stream_with_context, send_file
import requests
import yt_dlp
from playback import build_player, build_player_error
import socket
import urllib3.util.connection as _u3c

# googlevideo URLs are bound to the IP that yt-dlp used when it asked YouTube for them. If yt-dlp talks over one IP
# family (IPv4/IPv6) and `requests` fetches the video over the other, googlevideo answers 403 with an empty body.
# Forcing IPv4 everywhere makes both use the same address.
FORCE_IPV4 = os.environ.get("YTLF_FORCE_IPV4", "1") == "1"
if FORCE_IPV4:
    _u3c.allowed_gai_family = lambda: socket.AF_INET

app = Flask(__name__)
app.logger.setLevel(logging.INFO)   # so the search/query log lines show up in the terminal
LOG_PATH = os.environ.get("YTLF_LOG", "capture.jsonl")
PUBLIC_BASE = os.environ.get("YTLF_PUBLIC_BASE", "http://127.0.0.1:8080").rstrip("/")
PLAYER_OVERRIDE = os.environ.get("YTLF_PLAYER_OVERRIDE", "0") == "1"
PLAYER_ENCODE = os.environ.get("YTLF_PLAYER_ENCODE", "1") == "1"   # answer youtubei/v1/player with a legacy protobuf response
NEXT_ENCODE = os.environ.get("YTLF_NEXT_ENCODE", "1") == "1"   # legacy watch-page renderers for the iOS 7 app
SAVE_DIR = os.environ.get("YTLF_SAVE_DIR", "")   # if set, save FULL request/response bodies here (for decoding)
ALLOWED = ("youtube.com", "googleapis.com", "googlevideo.com", "ytimg.com", "ggpht.com")
PASSTHROUGH = ("googlevideo.com", "ytimg.com", "ggpht.com")   # big media/images: stream untouched, no logging
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
       "trailers", "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding"}
REDACT = {"authorization", "cookie", "set-cookie", "x-goog-visitor-id", "x-goog-api-key", "x-goog-device-auth"}

# What the server tells YouTube the client is. Bump versions if YouTube rejects them.
CLIENT_OVERRIDE = {   # taken from yt-dlp's current iOS client (Nov 2026: YouTube rejects old versions with 400 FAILED_PRECONDITION)
    "clientName": "IOS",
    "clientVersion": "21.26.4",
    "deviceMake": "Apple",
    "deviceModel": "iPhone16,2",
    "osName": "iPhone",
    "osVersion": "18.3.2.22D82",
}

# ---------------- logging ----------------
def _redact(h):
    return {k: ("<redacted>" if k.lower() in REDACT else v) for k, v in h.items()}

def _preview(b, n=4096):
    b = b or b""
    try:
        return b[:n].decode("utf-8")
    except UnicodeDecodeError:
        return "b64:" + base64.b64encode(b[:n]).decode()

def log_exchange(host, path, req_headers, req_body, status, resp_headers, resp_body):
    rec = {"ts": time.time(), "method": request.method, "host": host, "path": "/" + path,
           "query": request.query_string.decode(), "req_headers": _redact(req_headers),
           "req_body": _preview(req_body), "status": status,
           "resp_headers": _redact(resp_headers), "resp_body": _preview(resp_body)}
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(rec) + "\n")

# ---------------- minimal schema-less protobuf codec ----------------
# Layout seen in capture: top.1 = context { 1 = client {16 id, 17 version, 19 osVersion, ...}, 9 = {"ms" token}, ... }
PB_VERSION = os.environ.get("YTLF_PB_VERSION", "21.26.4")   # client field 17 ("" = leave alone)
PB_OSVER   = os.environ.get("YTLF_PB_OSVER", "")            # client field 19 ("" = leave alone)
PB_DROP_MS = os.environ.get("YTLF_PB_DROP_MS", "0") == "1"  # drop context field 9 (the "ms" token)
HDR_UA   = os.environ.get("YTLF_UA", "")                                   # override User-Agent ("" = leave alone)
HDR_DROP = [h.strip().lower() for h in os.environ.get("YTLF_DROP_HEADERS", "").split(",") if h.strip()]

def _rv(b, i):
    x = s = 0
    while True:
        c = b[i]; i += 1
        x |= (c & 0x7F) << s
        if not c & 0x80:
            return x, i
        s += 7

def _wv(x):
    out = bytearray()
    while True:
        c = x & 0x7F; x >>= 7
        if x:
            out.append(c | 0x80)
        else:
            out.append(c)
            return bytes(out)

def pb_parse(b):
    i, out = 0, []
    while i < len(b):
        tag, i = _rv(b, i)
        f, w = tag >> 3, tag & 7
        if w == 0:
            v, i = _rv(b, i)
        elif w == 1:
            v = b[i:i + 8]; i += 8
        elif w == 2:
            n, i = _rv(b, i)
            if i + n > len(b):
                raise ValueError("length overruns buffer")
            v = b[i:i + n]; i += n
        elif w == 5:
            v = b[i:i + 4]; i += 4
        else:
            raise ValueError("unsupported wire type %d" % w)
        out.append((f, w, v))
    return out

def pb_build(fields):
    out = bytearray()
    for f, w, v in fields:
        out += _wv((f << 3) | w)
        if w == 0:
            out += _wv(v)
        elif w == 2:
            out += _wv(len(v)) + v
        else:
            out += v
    return bytes(out)

def rewrite_pb_context(body):
    top, new_top = pb_parse(body), []
    for f, w, v in top:
        if f == 1 and w == 2:                      # context
            new_ctx = []
            for f2, w2, v2 in pb_parse(v):
                if f2 == 9 and PB_DROP_MS:
                    continue
                if f2 == 1 and w2 == 2:            # client
                    cl = []
                    for f3, w3, v3 in pb_parse(v2):
                        if f3 == 17 and w3 == 2 and PB_VERSION:
                            v3 = PB_VERSION.encode()
                        elif f3 == 19 and w3 == 2 and PB_OSVER:
                            v3 = PB_OSVER.encode()
                        cl.append((f3, w3, v3))
                    v2 = pb_build(cl)
                new_ctx.append((f2, w2, v2))
            v = pb_build(new_ctx)
        new_top.append((f, w, v))
    return pb_build(new_top)

def save_exchange(path, status, resp_headers, req_body, resp_body):
    """Full (untruncated) bodies to files, so responses can be decoded offline."""
    if not SAVE_DIR:
        return
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)
        base = os.path.join(SAVE_DIR, "%d_%s" % (int(time.time() * 1000), path.replace("/", "_")[:80]))
        open(base + ".req.bin", "wb").write(req_body or b"")
        open(base + ".resp.bin", "wb").write(resp_body or b"")
        ctype = next((v for k, v in resp_headers.items() if k.lower() == "content-type"), None)
        with open(os.path.join(SAVE_DIR, "index.txt"), "a") as f:
            f.write("%s status=%s ctype=%s req=%d resp=%d\n" % (os.path.basename(base), status, ctype,
                                                              len(req_body or b""), len(resp_body or b"")))
    except Exception as e:
        app.logger.warning("save_exchange failed: %s", e)


# ---------------- legacy search response (for the 2015 YouTube app) ----------------
# Field numbers read from the 11.19.7 binary with fields.py:
#   YTISearchResponse.contents = 4
#   YTISearchResponseSupportedRenderers.sectionListRenderer = 49399797
#   YTISectionListRenderer.contents = 1 (repeated) -> YTISectionListSupportedRenderers.itemSectionRenderer = 50195462
#   YTIItemSectionRenderer.contents = 1 (repeated) -> YTIItemSectionSupportedRenderers.compactVideoRenderer = 50630979
#   YTICompactVideoRenderer: 1 videoId, 2 thumbnail, 3 title, 4 longBylineText, 5 publishedTimeText,
#                            6 viewCountText, 7 lengthText, 8 navigationEndpoint, 10 shortBylineText,
#                            23 shortViewCountText   (an older fields.py picked the wrong table; these are the real ones)
#   YTINavigationEndpoint.watchEndpoint = 48687757 -> YTIWatchEndpoint.videoId = 1
#   YTIFormattedString.runs = 1 -> YTIStringRun.text = 1 ; YTIThumbnailDetails.thumbnails = 1 -> {1 url, 2 width, 3 height}
SEARCH_ENCODE = os.environ.get("YTLF_SEARCH_ENCODE", "1") == "1"
SEARCH_QUERY_FIELD = int(os.environ.get("YTLF_SEARCH_QUERY_FIELD", "2"))   # YTISearchRequest.query (verify with fields.py)
SEARCH_COUNT = int(os.environ.get("YTLF_SEARCH_COUNT", "20"))
F_RESP_CONTENTS, F_SECTION_LIST, F_ITEM_SECTION = 4, 49399797, 50195462
F_COMPACT_VIDEO, F_WATCH_ENDPOINT = 50630979, 48687757

def _s(f, text):  return (f, 2, text.encode("utf-8"))
def _m(f, fields): return (f, 2, pb_build(fields))
def _i(f, n):     return (f, 0, int(n))
def _text(f, text): return _m(f, [_m(1, [_s(1, text)])])        # YTIFormattedString{runs:[{text}]}

def fmt_duration(sec):
    sec = int(sec); h, r = divmod(sec, 3600); m, s = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)

def fmt_views(n):
    return "{:,} views".format(int(n))

def short_views(n):
    n = int(n)
    for div, suf in ((10**9, "B"), (10**6, "M"), (10**3, "K")):
        if n >= div:
            v = "%.1f" % (n / div)
            return "%s%s views" % (v[:-2] if v.endswith(".0") else v, suf)
    return "%d views" % n

_SEARCH_CACHE = {}
_LAST_WATCH_ID = None
def yt_search(q):
    hit = _SEARCH_CACHE.get(q)
    if hit and hit[0] > time.time():
        return hit[1]
    opts = {"quiet": True, "skip_download": True, "extract_flat": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info("ytsearch%d:%s" % (SEARCH_COUNT, q), download=False)
    out = []
    for e in (info or {}).get("entries") or []:
        vid = e.get("id") or ""
        if len(vid) != 11:                       # channel rows and other non-video results
            continue
        out.append({"id": vid, "title": e.get("title") or "",
                    "channel": e.get("channel") or e.get("uploader") or "",
                    "duration": e.get("duration"), "views": e.get("view_count")})
    _SEARCH_CACHE[q] = (time.time() + 600, out)
    nodur = sum(1 for v in out if not v["duration"])
    noviews = sum(1 for v in out if v["views"] is None)
    app.logger.info("yt-dlp %r: %d videos (%d without duration, %d without views); first: %r",
                    q, len(out), nodur, noviews, out[0] if out else None)
    return out

def compact_video(v):
    """YTICompactVideoRenderer. Field numbers verified with the fixed fields.py (33 of 33 properties):
    1 videoId, 2 thumbnail, 3 title, 4 longBylineText, 5 publishedTimeText, 6 viewCountText, 7 lengthText,
    8 navigationEndpoint, 10 shortBylineText, 23 shortViewCountText, 32 thumbnailOverlaysArray."""
    vid = v["id"]
    thumbs = [_m(1, [_s(1, "https://i.ytimg.com/vi/%s/mqdefault.jpg" % vid), _i(2, 320), _i(3, 180)]),
              _m(1, [_s(1, "https://i.ytimg.com/vi/%s/hqdefault.jpg" % vid), _i(2, 480), _i(3, 360)])]
    f = [_s(1, vid), _m(2, thumbs), _text(3, v["title"]),
         _text(4, v["channel"]), _text(10, v["channel"]),
         _m(8, [_m(F_WATCH_ENDPOINT, [_s(1, vid)])])]
    if v.get("views") is not None:
        f += [_text(6, fmt_views(v["views"])), _text(23, short_views(v["views"]))]
    if v.get("duration"):
        f.append(_text(7, fmt_duration(v["duration"])))
    f.sort(key=lambda t: t[0])
    return f

def _item_section(renderer_id, renderer_fields):
    """One legacy itemSectionRenderer containing a supported child renderer."""
    renderer = pb_build(renderer_fields)
    content = pb_build([(renderer_id, 2, renderer)])
    item = pb_build([(1, 2, content)])
    return pb_build([(F_ITEM_SECTION, 2, item)])

def _video_sections(videos):
    """A sectionListRenderer body holding one item section with compact video cards."""
    entries = [(1, 2, pb_build([(F_COMPACT_VIDEO, 2, pb_build(compact_video(v)))]))
               for v in videos]
    item_section = pb_build(entries)
    return pb_build([(1, 2, pb_build([(F_ITEM_SECTION, 2, item_section)]))])

def build_search_response(videos):
    """YTISearchResponse{contents{sectionListRenderer{contents[itemSectionRenderer]}}}"""
    entries = [(1, 2, pb_build([(F_COMPACT_VIDEO, 2, pb_build(compact_video(v)))])) for v in videos]
    item_section = pb_build(entries)
    section_list = pb_build([(1, 2, pb_build([(F_ITEM_SECTION, 2, item_section)]))])
    supported = pb_build([(F_SECTION_LIST, 2, section_list)])
    return pb_build([(F_RESP_CONTENTS, 2, supported)])

def build_browse_response(videos):
    """Legacy YTIBrowseResponse with a section list instead of current tab renderers."""
    section_list = pb_build([(F_SECTION_LIST, 2, _video_sections(videos))])
    return pb_build([(9, 2, section_list)])  # YTIBrowseResponse.contents

def _legacy_comment(comment):
    """Convert one yt-dlp comment dictionary to the app's legacy comment renderer."""
    fields = []
    if comment.get("author"):
        fields.append(_text(1, comment["author"]))
    avatar = comment.get("author_thumbnail")
    if avatar:
        fields.append(_m(2, [_m(1, [_s(1, avatar), _i(2, 48), _i(3, 48)])]))
    if comment.get("text"):
        fields.append(_text(4, comment["text"]))
    if comment.get("timestamp"):
        try:
            fields.append(_text(5, time.strftime("%b %-d, %Y", time.localtime(comment["timestamp"]))))
        except (TypeError, ValueError, OverflowError):
            pass
    if comment.get("like_count") is not None:
        fields.append(_i(7, comment["like_count"]))
    if comment.get("id"):
        fields.append(_s(9, comment["id"]))
    if comment.get("author_id"):
        fields.append(_s(21, comment["author_id"]))
    renderer = pb_build(fields)
    thread = pb_build([(1, 2, pb_build([(62285947, 2, renderer)]))])
    return _m(1, [_m(62285833, pb_parse(thread))])

def build_watch_next_response(info, recommendations, comments=()):
    """Legacy watch page: metadata/actions, comment header, then related videos."""
    title = info.get("title") or ""
    channel = info.get("channel") or info.get("uploader") or ""
    channel_id = info.get("channel_id") or ""
    owner = [_text(2, channel)]
    if info.get("channel_follower_count") is not None:
        count = int(info["channel_follower_count"])
        owner.append(_text(5, "%s subscribers" % short_views(count).replace(" views", "")))
    # The 11.19.7 app expects the nested subscribeButtonRenderer in owner field 6.
    # A simple enabled button is enough to render the channel row; authenticated
    # subscribe actions are intentionally outside this compatibility response.
    subscribe = [_text(1, "Subscribe"), _i(3, 0), _i(4, 1)]
    if channel_id:
        subscribe.append(_s(7, channel_id))
    owner.append(_m(6, [_m(55419609, subscribe)]))

    likes = info.get("like_count")
    dislikes = info.get("dislike_count")
    like_button = [_m(1, [_s(1, info.get("id") or "")])]
    if likes is not None:
        like_button += [_i(3, likes), _text(4, short_views(likes).replace(" views", "")),
                        _text(5, short_views(likes).replace(" views", ""))]
    if dislikes is not None:
        like_button += [_i(7, dislikes), _text(8, short_views(dislikes).replace(" views", "")),
                         _text(9, short_views(dislikes).replace(" views", ""))]
    like_button.append(_i(13, 1))

    date_text = ""
    upload_date = info.get("release_date") or info.get("upload_date")
    if upload_date and len(str(upload_date)) == 8 and str(upload_date).isdigit():
        try:
            date_text = time.strftime("%b %-d, %Y", time.strptime(str(upload_date), "%Y%m%d"))
        except ValueError:
            date_text = ""
    elif info.get("release_timestamp"):
        date_text = time.strftime("%b %-d, %Y", time.localtime(info["release_timestamp"]))

    comments_count = info.get("comment_count")
    # Section-list renderer IDs are read from this app's protobuf schema:
    # videoPrimaryInfoRenderer=100048340, videoSecondaryInfoRenderer=102519231.
    primary = [_text(1, title)]
    if info.get("view_count") is not None:
        views = int(info["view_count"])
        view_count = [_text(1, fmt_views(views)), _text(4, short_views(views))]
        primary.append(_m(3, [_m(70044402, view_count)]))
    actions = [_m(1, [_m(60091038, like_button)])]
    primary.append(_m(4, [_m(100349614, actions)]))

    secondary = [_m(1, [_m(51779708, owner)])]  # owner.videoOwnerRenderer
    if date_text:
        secondary.append(_text(2, date_text))
    description = (info.get("description") or "")[:1200]
    if description:
        secondary.append(_text(3, description))
    secondary.append(_m(4, [_m(55419609, subscribe)]))
    sections = [_item_section(100048340, primary), _item_section(102519231, secondary)]

    if comments_count is not None:
        count = "Comments · %s" % format(int(comments_count), ",")
    else:
        count = "Comments"
    # Keep a legacy comment section with its real count. The old app can render
    # the section header while comment retrieval stays separate.
    comment_section = [_m(4, [_m(87566086, [_text(1, count)])])]
    comment_section.extend(_legacy_comment(c) for c in comments if c.get("text"))
    sections.append(_item_section(62241549, comment_section))
    for video in recommendations:
        if video.get("id") and video["id"] != info.get("id"):
            sections.append(_item_section(F_COMPACT_VIDEO, compact_video(video)))
    section_list = pb_build([(1, 2, section) for section in sections])
    # singleColumnWatchNextResults.results is itself the supported-renderer
    # oneof containing sectionListRenderer; don't add another field-1 wrapper.
    single_column = pb_build([(1, 2, pb_build([(F_SECTION_LIST, 2, section_list)]))])
    return pb_build([(7, 2, pb_build([(51779735, 2, single_column)]))])  # contents.singleColumnWatchNextResults

def extract_search_query(req_body):
    """Pull the query string out of a YTISearchRequest (protobuf, or JSON for modern clients)."""
    if not req_body:
        return None
    if req_body[:1] == b"{":
        try:
            return json.loads(req_body).get("query") or None
        except ValueError:
            return None
    try:
        fields = pb_parse(req_body)
    except Exception:
        return None
    cands = []
    for f, w, v in fields:
        if f == 1 or w != 2:                    # field 1 is the context
            continue
        try:
            t = v.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if t and t.isprintable():
            cands.append((f, t))
    app.logger.info("search request string fields: %r (using field %d)", cands, SEARCH_QUERY_FIELD)
    for f, t in cands:
        if f == SEARCH_QUERY_FIELD:
            return t
    return None

@app.route("/yt/search.json")
def search_json():
    """Debug: the yt-dlp results behind a search, e.g. /yt/search.json?q=cats"""
    return Response(json.dumps(yt_search(request.args.get("q", "")), indent=1), mimetype="application/json")

@app.route("/yt/search.pb")
def search_pb():
    """Debug: the protobuf we would send for ?q=..."""
    return Response(build_search_response(yt_search(request.args.get("q", ""))),
                    mimetype="application/x-protobuf")

# ---------------- transforms: this is where fixes go ----------------
def transform_request(host, path, headers, body):
    """Fix the request on its way to YouTube (e.g. 'update required' version fields)."""
    ctype = headers.get("Content-Type", "")
    # header fixes (headers is mutated in place and is what gets sent upstream)
    if HDR_UA:
        headers["User-Agent"] = HDR_UA
    for name in HDR_DROP:
        for k in [k for k in headers if k.lower() == name]:
            del headers[k]
    if "json" in ctype and body:
        try:
            j = json.loads(body)
            client = j.get("context", {}).get("client")
            if isinstance(client, dict):
                client.update(CLIENT_OVERRIDE)
                body = json.dumps(j).encode()
        except ValueError:
            pass
    # protobuf bodies (old iOS app): rewrite client fields and rebuild length prefixes
    if body and "json" not in ctype and "youtubei/" in path:
        try:
            body = rewrite_pb_context(body)
        except Exception as e:
            app.logger.warning("pb rewrite failed, sending original: %s", e)
    return body

def extract_player_video_id(req_body):
    """YTIPlayerRequest.videoId = field 2 (string)."""
    if not req_body:
        return None
    if req_body[:1] == b"{":
        try:
            return json.loads(req_body).get("videoId") or None
        except ValueError:
            return None
    try:
        for f, w, v in pb_parse(req_body):
            if f == 2 and w == 2:
                t = v.decode("utf-8")
                if len(t) == 11:
                    return t
    except Exception:
        pass
    return None

def _legacy_proto_response(headers):
    out = {k: v for k, v in headers.items()
           if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")}
    out["Content-Type"] = "application/x-protobuf"
    return out

def transform_response(host, path, status, headers, body, req_body=None):
    """Downgrade responses whose modern renderer fields the old app cannot parse."""
    global _LAST_WATCH_ID
    if path.endswith("youtubei/v1/browse") and req_body and b"FEwhat_to_watch" in req_body:
        try:
            videos = yt_search(os.environ.get("YTLF_HOME_QUERY", "trending videos"))[:12]
            if videos:
                body = build_browse_response(videos)
                app.logger.info("home browse FEwhat_to_watch -> %d legacy video cards", len(videos))
                return 200, _legacy_proto_response(headers), body
        except Exception as e:
            app.logger.warning("home feed encode failed, passing upstream response through: %s", e)
    if NEXT_ENCODE and path.endswith("youtubei/v1/next"):
        vid = extract_player_video_id(req_body) if req_body else None
        if vid:
            _LAST_WATCH_ID = vid
        vid = vid or _LAST_WATCH_ID
        if vid:
            try:
                info = resolve(vid)
                query = " ".join(x for x in (info.get("title"), info.get("uploader")) if x)
                try:
                    related = [v for v in yt_search(query) if v.get("id") != vid][:12] if query else []
                except Exception as e:
                    app.logger.warning("related-video lookup failed for %s: %s", vid, e)
                    related = []
                comments = resolve_comments(vid)
                body = build_watch_next_response(info, related, comments)
                app.logger.info("watch next %s -> metadata, %d comments, %d legacy related cards",
                                vid, len(comments), len(related))
                return 200, _legacy_proto_response(headers), body
            except Exception as e:
                app.logger.warning("watch-next encode failed for %s, passing upstream response through: %s", vid, e)
    if SEARCH_ENCODE and path.endswith("youtubei/v1/search"):
        q = extract_search_query(req_body)
        if q:
            try:
                videos = yt_search(q)
                body = build_search_response(videos)
                headers = {k: v for k, v in headers.items()
                           if k.lower() not in ("content-length", "content-encoding")}
                if not any(k.lower() == "content-type" for k in headers):
                    headers["Content-Type"] = "application/x-protobuf"
                app.logger.info("search %r -> %d videos, %d bytes", q, len(videos), len(body))
                return 200, headers, body
            except Exception as e:
                app.logger.warning("search encode failed, passing upstream response through: %s", e)
        else:
            app.logger.info("search request with no query (continuation?); passing through")
    if PLAYER_ENCODE and path.endswith("youtubei/v1/player"):
        vid = extract_player_video_id(req_body)
        if vid:
            try:
                info = resolve(vid)
                body = build_player(vid, info, "%s/yt/stream/%s.mp4" % (PUBLIC_BASE, vid))
                headers = {k: v for k, v in headers.items()
                           if k.lower() not in ("content-length", "content-encoding")}
                headers["Content-Type"] = "application/x-protobuf"
                app.logger.info("player %s -> %r, %d bytes, format=%s %sp %s size=%s", vid, info.get("title"), len(body),
                                info.get("format_id"), info.get("height"), info.get("ext"),
                                info.get("filesize") or info.get("filesize_approx"))
                return 200, headers, body
            except Exception as e:
                app.logger.warning("player encode failed for %s: %s", vid, e)
                body = build_player_error(vid, "Video unavailable through the proxy (see server log)")
                headers = {k: v for k, v in headers.items()
                           if k.lower() not in ("content-length", "content-encoding")}
                headers["Content-Type"] = "application/x-protobuf"
                return 200, headers, body
        else:
            app.logger.info("player request with no videoId; passing through")
    return status, headers, body

# ---------------- stream resolver ----------------
_CACHE = {}
# Progressive (audio+video in one file) over plain HTTP only. HLS/DASH urls cannot be proxied as an mp4.
FMT = ("18/"
       "best[ext=mp4][vcodec^=avc1][acodec!=none][protocol^=http][height<=480]/"
       "best[ext=mp4][vcodec^=avc1][acodec!=none][protocol^=http][height<=720]/"
       "best[ext=mp4][acodec!=none][protocol^=http]")
# YouTube now hides most formats until a JS "n challenge" is solved. yt-dlp needs a JS runtime (deno) plus
# its challenge-solver script. Install deno (brew install deno); the solver script is fetched from github.
REMOTE_COMPONENTS = os.environ.get("YTLF_REMOTE_COMPONENTS", "ejs:github")   # "" to disable
YTDLP_CLIENTS = os.environ.get("YTLF_YTDLP_CLIENTS", "")                    # e.g. "tv,web_safari" (comma list)

def _ydl_opts(**extra):
    o = {"quiet": True, "no_warnings": False, "skip_download": True, "noplaylist": True}
    if FORCE_IPV4:
        o["source_address"] = "0.0.0.0"          # yt-dlp's equivalent of -4
    if REMOTE_COMPONENTS:
        o["remote_components"] = [c.strip() for c in REMOTE_COMPONENTS.split(",") if c.strip()]
    if YTDLP_CLIENTS:
        o["extractor_args"] = {"youtube": {"player_client": [c.strip() for c in YTDLP_CLIENTS.split(",") if c.strip()]}}
    o.update(extra)
    return o

def _log_formats(vid):
    """When selection fails, log what YouTube actually offered so the format string can be fixed."""
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(ignore_no_formats_error=True, quiet=True)) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
        rows = ["%s %s %sp v=%s a=%s %s" % (f.get("format_id"), f.get("ext"), f.get("height") or "-",
                                          (f.get("vcodec") or "-")[:8], (f.get("acodec") or "-")[:8], f.get("protocol"))
                for f in (info.get("formats") or [])]
        app.logger.warning("formats offered for %s (%d): %s", vid, len(rows), " | ".join(rows[:40]) or "none")
    except Exception as e:
        app.logger.warning("could not list formats for %s: %s", vid, e)

def resolve(vid):
    hit = _CACHE.get(vid)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(format=FMT)) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
    except Exception:
        _log_formats(vid)
        raise
    _CACHE[vid] = (time.time() + 1500, info)
    return info

_COMMENTS_CACHE = {}
_COMMENTS_GUARD = threading.Lock()
_COMMENTS_LOCKS = {}
COMMENT_LIMIT = int(os.environ.get("YTLF_COMMENT_LIMIT", "8"))

def resolve_comments(vid):
    """Fetch a small first page of comments for the legacy watch-page renderer."""
    hit = _COMMENTS_CACHE.get(vid)
    if hit and hit[0] > time.time():
        return hit[1]
    with _COMMENTS_GUARD:
        lock = _COMMENTS_LOCKS.setdefault(vid, threading.Lock())
    with lock:
        hit = _COMMENTS_CACHE.get(vid)
        if hit and hit[0] > time.time():
            return hit[1]
        yt_args = {"max_comments": [str(max(COMMENT_LIMIT, 0))]}
        if YTDLP_CLIENTS:
            yt_args["player_client"] = [c.strip() for c in YTDLP_CLIENTS.split(",") if c.strip()]
        try:
            with yt_dlp.YoutubeDL(_ydl_opts(getcomments=True, extractor_args={"youtube": yt_args},
                                             socket_timeout=15)) as ydl:
                info = ydl.extract_info("https://www.youtube.com/watch?v=%s" % vid, download=False)
            comments = (info or {}).get("comments") or []
            comments = [c for c in comments if c.get("text")][:max(COMMENT_LIMIT, 0)]
            _COMMENTS_CACHE[vid] = (time.time() + 600, comments)
            return comments
        except Exception as e:
            app.logger.warning("comments %s: fetch failed: %s", vid, str(e).replace("\n", " ")[:240])
            _COMMENTS_CACHE[vid] = (time.time() + 90, [])
            return []

# googlevideo rejects (403) some un-ranged / open-ended requests, which is what the phone sends. So we always
# fetch upstream in bounded chunks (like yt-dlp does) and re-assemble the answer the phone asked for.
CHUNK = int(os.environ.get("YTLF_CHUNK", str(8 * 1024 * 1024)))
MINFETCH = int(os.environ.get("YTLF_MINFETCH", str(1024 * 1024)))   # never ask upstream for less than this (tiny ranges like bytes=0-1 get 403)
_SIZE = {}   # vid -> total bytes

def _upstream_range(url, h, a, b):
    hh = dict(h)
    hh["Range"] = "bytes=%d-%d" % (a, b)
    hh["Accept-Encoding"] = "identity"
    return requests.get(url, headers=hh, stream=True, timeout=30)

def _total_size(vid, info, h):
    if vid in _SIZE:
        return _SIZE[vid]
    total = info.get("filesize")                 # exact size from yt-dlp, when it has one
    if not total:
        r = _upstream_range(info["url"], h, 0, MINFETCH - 1)
        cr = r.headers.get("Content-Range", "")
        if r.status_code not in (200, 206) or "/" not in cr:
            raise RuntimeError("size probe failed: upstream %s %r" % (r.status_code, r.content[:200]))
        r.close()
        total = int(cr.rsplit("/", 1)[1])
    _SIZE[vid] = int(total)
    return _SIZE[vid]

def _parse_range(hdr, total):
    """'bytes=a-b' / 'bytes=a-' / 'bytes=-n' -> (start, end) clamped to the file, or None if absent/garbled."""
    if not hdr or not hdr.startswith("bytes="):
        return None
    try:
        s, _, e = hdr[6:].split(",")[0].strip().partition("-")
        if s == "":
            return max(total - int(e), 0), total - 1
        return int(s), (min(int(e), total - 1) if e else total - 1)
    except ValueError:
        return None

def stream_proxy(vid):          # YTLF_STREAM_MODE=proxy: fetch from googlevideo ourselves (was returning 403)
    try:
        info = resolve(vid)
        h = dict(info.get("http_headers", {}))
        total = _total_size(vid, info, h)
    except Exception as e:
        app.logger.warning("stream %s: resolve/size failed: %s", vid, e)
        return None
    rng_hdr = request.headers.get("Range")
    rng = _parse_range(rng_hdr, total)
    start, end = rng if rng else (0, total - 1)
    app.logger.info("stream %s Range=%r -> bytes %d-%d of %d", vid, rng_hdr, start, end, total)
    if start > end or start >= total:
        return Response(status=416, headers={"Content-Range": "bytes */%d" % total})
    out = {"Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1)}
    if rng:
        out["Content-Range"] = "bytes %d-%d/%d" % (start, end, total)

    # Validate the first upstream range before sending headers to the phone. Once a
    # 206 response has started, Flask cannot replace it with the yt-dlp fallback.
    first_stop = min(start + CHUNK - 1, end)
    first_fetch_stop = min(max(first_stop, start + MINFETCH - 1), total - 1)
    try:
        first_response = _upstream_range(info["url"], h, start, first_fetch_stop)
    except Exception as e:
        app.logger.warning("stream %s: initial proxy range failed: %s; falling back to yt-dlp", vid, e)
        return None
    if first_response.status_code != 206:
        app.logger.warning("stream %s: upstream %s for initial range bytes=%d-%d; falling back to yt-dlp",
                           vid, first_response.status_code, start, first_fetch_stop)
        first_response.close()
        return None

    def gen():
        pos = start
        while pos <= end:
            stop = min(pos + CHUNK - 1, end)                       # what the phone needs from this chunk
            fetch_stop = min(max(stop, pos + MINFETCH - 1), total - 1)   # what we ask upstream for (at least MINFETCH)
            if pos == start:
                r = first_response
            else:
                r = _upstream_range(info["url"], h, pos, fetch_stop)
            if r.status_code not in (200, 206):
                app.logger.warning("stream %s: upstream %s for bytes=%d-%d: %r", vid, r.status_code, pos, fetch_stop, r.content[:300])
                return
            need, got = stop - pos + 1, 0
            for blk in r.iter_content(65536):
                take = min(len(blk), need - got)
                got += take
                yield blk if take == len(blk) else blk[:take]
                if got >= need:
                    break
            r.close()
            if got != need:
                app.logger.warning("stream %s: short chunk %d/%d for bytes=%d-%d", vid, got, need, pos, stop)
                return
            pos = stop + 1

    return Response(gen(), status=206 if rng else 200, headers=out)

# ---------------- default stream mode: let yt-dlp download the file, then serve it from disk ----------------
# Fetching googlevideo URLs ourselves kept getting an empty 403. yt-dlp's own downloader is the code that is kept
# working against YouTube's rules, so it downloads the (small, itag 18) file into cache/ and Flask serves it with
# proper Range support. If one yt-dlp client fails, the next one is tried; the one that works is remembered.
CACHE_DIR = os.environ.get("YTLF_CACHE_DIR", "cache")
STREAM_MODE = os.environ.get("YTLF_STREAM_MODE", "proxy")      # "proxy" streams as bytes arrive; set "file" to download fully first
CLIENT_TRIES = [("" if c.strip() == "default" else c.strip())
                for c in os.environ.get("YTLF_CLIENT_TRIES", "default,tv,android_vr,web_safari").split(",") if c.strip()]
_GOOD_CLIENT = [None]
_DL_LOCKS, _DL_GUARD = {}, threading.Lock()

def _cached_file(vid):
    files = [p for p in glob.glob(os.path.join(CACHE_DIR, glob.escape(vid) + ".*"))
             if not p.endswith((".part", ".ytdl", ".temp"))]
    return files[0] if files else None

def _prune(keep=15):
    files = sorted((p for p in glob.glob(os.path.join(CACHE_DIR, "*")) if not p.endswith((".part", ".ytdl"))),
                   key=os.path.getmtime, reverse=True)
    for p in files[keep:]:
        try:
            os.remove(p)
        except OSError:
            pass

def _download(vid, client):
    os.makedirs(CACHE_DIR, exist_ok=True)
    opts = _ydl_opts(format=FMT, skip_download=False, outtmpl=os.path.join(CACHE_DIR, "%(id)s.%(ext)s"))
    if client:
        opts["extractor_args"] = {"youtube": {"player_client": [client]}}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info("https://www.youtube.com/watch?v=%s" % vid, download=True)
        return (info.get("requested_downloads") or [{}])[0].get("filepath") or ydl.prepare_filename(info)

def ensure_file(vid):
    path = _cached_file(vid)
    if path:
        return path
    with _DL_GUARD:
        lock = _DL_LOCKS.setdefault(vid, threading.Lock())
    with lock:                                   # the phone sends two requests at once; only one downloads
        path = _cached_file(vid)
        if path:
            return path
        order = ([_GOOD_CLIENT[0]] if _GOOD_CLIENT[0] is not None else []) + \
                [c for c in CLIENT_TRIES if c != _GOOD_CLIENT[0]]
        errors = []
        for client in order:
            try:
                path = _download(vid, client)
                _GOOD_CLIENT[0] = client
                app.logger.info("stream %s: downloaded with client=%s -> %s (%d bytes)", vid, client or "default",
                                path, os.path.getsize(path))
                _prune()
                return path
            except Exception as e:
                msg = str(e).replace("\n", " ")[:300]
                app.logger.warning("stream %s: download with client=%s failed: %s", vid, client or "default", msg)
                errors.append("%s: %s" % (client or "default", msg))
        raise RuntimeError(" | ".join(errors))

@app.route("/yt/stream/<vid>.mp4")
def stream(vid):
    if STREAM_MODE == "proxy":
        response = stream_proxy(vid)
        if response is not None:
            return response
        # Proxy failures happen before response headers are sent, so the original
        # yt-dlp downloader can still provide a reliable (slower) playback path.
        try:
            path = ensure_file(vid)
        except Exception as e:
            return Response("stream and download failed: %s" % e, status=502)
        app.logger.info("stream %s: proxy unavailable; serving yt-dlp cache %s", vid, path)
        return send_file(path, mimetype="video/mp4", conditional=True, max_age=0)
    try:
        path = ensure_file(vid)
    except Exception as e:
        return Response("download failed: %s" % e, status=502)
    app.logger.info("stream %s Range=%r -> serving %s", vid, request.headers.get("Range"), path)
    return send_file(path, mimetype="video/mp4", conditional=True, max_age=0)

@app.route("/yt/play")
def play():
    vid = request.args.get("v", "")
    return f'<!doctype html><meta name=viewport content="width=device-width"><video src="/yt/stream/{vid}.mp4" controls autoplay playsinline style="width:100%"></video>'

@app.route("/yt/debug/<vid>")
def debug_stream(vid):
    """Open in a browser: tries several ways of fetching the video from googlevideo and prints each status."""
    from urllib.parse import urlparse, parse_qs
    try:
        info = resolve(vid)
    except Exception as e:
        return Response("resolve failed: %s" % e, mimetype="text/plain")
    url = info["url"]
    base = dict(info.get("http_headers", {}))
    q = parse_qs(urlparse(url).query)
    out = ["format=%s %sp ext=%s protocol=%s" % (info.get("format_id"), info.get("height"), info.get("ext"), info.get("protocol")),
           "url host=%s  ip param=%s  has pot=%s  expire=%s" % (urlparse(url).hostname, q.get("ip"), "pot" in q, q.get("expire")),
           "yt-dlp headers: %r" % base, "FORCE_IPV4=%s" % FORCE_IPV4, ""]

    def with_family(fam, fn):
        orig = _u3c.allowed_gai_family
        if fam is not None:
            _u3c.allowed_gai_family = lambda: fam
        try:
            return fn()
        finally:
            _u3c.allowed_gai_family = orig

    def my_ip(fam, endpoint):
        try:
            return with_family(fam, lambda: requests.get(endpoint, timeout=8).text.strip())
        except Exception as e:
            return "unreachable (%s)" % type(e).__name__
    out.append("public IPv4 seen: %s" % my_ip(socket.AF_INET, "https://api.ipify.org"))
    out.append("public IPv6 seen: %s" % my_ip(socket.AF_INET6, "https://api6.ipify.org"))
    out.append("")

    def attempt(label, headers, fam=None):
        def go():
            r = requests.get(url, headers=headers, stream=True, timeout=15)
            body = b"" if r.status_code in (200, 206) else r.content[:80]
            res = "%s %s range=%s body=%r" % (r.status_code, r.headers.get("Content-Type"), r.headers.get("Content-Range"), body)
            r.close()
            return res
        try:
            out.append("%-34s -> %s" % (label, with_family(fam, go)))
        except Exception as e:
            out.append("%-34s -> error %s: %s" % (label, type(e).__name__, e))

    attempt("A yt-dlp headers, bytes=0-1", dict(base, Range="bytes=0-1"))
    attempt("B Accept-Encoding identity, 0-1", dict(base, Range="bytes=0-1", **{"Accept-Encoding": "identity"}))
    attempt("C yt-dlp headers, bytes=0-1023", dict(base, Range="bytes=0-1023"))
    attempt("D no Range", dict(base))
    attempt("E IPv4 forced, bytes=0-1", dict(base, Range="bytes=0-1"), socket.AF_INET)
    attempt("F IPv6 forced, bytes=0-1", dict(base, Range="bytes=0-1"), socket.AF_INET6)
    attempt("G only User-Agent + Range", {"User-Agent": base.get("User-Agent", "Mozilla/5.0"), "Range": "bytes=0-1"})
    return Response("\n".join(out), mimetype="text/plain")

@app.route("/healthz")
def healthz():
    return "ok"

# ---------------- optional: answer /player ourselves (JSON clients only) ----------------
def player_response(vid):
    info = resolve(vid)
    return {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {"videoId": vid, "title": info.get("title", ""),
                         "lengthSeconds": str(int(info.get("duration") or 0)),
                         "author": info.get("uploader", "")},
        "streamingData": {"expiresInSeconds": "21540", "formats": [{
            "itag": 18, "url": f"{PUBLIC_BASE}/yt/stream/{vid}.mp4",
            "mimeType": 'video/mp4; codecs="avc1.42001E, mp4a.40.2"',
            "width": info.get("width") or 640, "height": info.get("height") or 360,
            "approxDurationMs": str(int((info.get("duration") or 0) * 1000)),
            "bitrate": int((info.get("tbr") or 500) * 1000)}]},
    }

# ---------------- generic forwarder ----------------
METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]

@app.route("/", defaults={"p": ""}, methods=METHODS)
@app.route("/<path:p>", methods=METHODS)
def forward(p):
    host = request.headers.get("X-Orig-Host", "")
    if not any(host == d or host.endswith("." + d) for d in ALLOWED):
        abort(403)
    body = request.get_data()
    if not host.endswith(PASSTHROUGH):
        app.logger.info("REQ %s %s/%s reqlen=%d", request.method, host, p, len(body))
    headers = {k: v for k, v in request.headers if k.lower() not in HOP and not k.lower().startswith("x-orig")}
    headers["Accept-Encoding"] = "identity"
    req_logged = dict(headers)

    if PLAYER_OVERRIDE and p.endswith("youtubei/v1/player") and "json" in headers.get("Content-Type", ""):
        try:
            vid = json.loads(body).get("videoId")
            if vid:
                data = json.dumps(player_response(vid)).encode()
                log_exchange(host, p, req_logged, body, 200, {"Content-Type": "application/json"}, data)
                return Response(data, 200, {"Content-Type": "application/json"})
        except Exception as e:
            app.logger.warning("player override failed: %s", e)

    body = transform_request(host, p, headers, body)
    req_logged = dict(headers)
    up = requests.request(request.method, f"https://{host}/{p}", params=request.args,
                          headers=headers, data=body, stream=True, timeout=60, allow_redirects=True)  # follow redirects here: the phone can't reach the real hosts

    if host.endswith(PASSTHROUGH):   # big media / thumbnails: pass through untouched
        out = {k: v for k, v in up.headers.items() if k.lower() in ("content-type", "content-length", "content-range", "accept-ranges")}
        return Response(stream_with_context(up.iter_content(65536)), status=up.status_code, headers=out)

    content = up.content
    status, rh, content = transform_response(host, p, up.status_code, dict(up.headers), content, req_body=body)
    log_exchange(host, p, req_logged, body, status, rh, content)
    save_exchange(p, status, rh, body, content)
    if status >= 400:
        app.logger.warning("UPSTREAM %s %s/%s ctype=%s reqlen=%d resp=%r", status, host, p,
                           headers.get("Content-Type"), len(body or b""), content[:400])
    out = {k: v for k, v in rh.items() if k.lower() not in HOP and k.lower() != "content-encoding"}
    return Response(content, status=status, headers=out)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), threaded=True)
