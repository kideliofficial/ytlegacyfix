// YTLegacyFix - routes the old YouTube app's traffic through your own server,
// spoofs the reported app version, and fixes the Google sign-in web view UA.
#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>
#import <AVFoundation/AVFoundation.h>
#import <MediaPlayer/MediaPlayer.h>

// ===================== CONFIG: EDIT THESE =====================
#ifndef YT_PROXY_SCHEME
#define YT_PROXY_SCHEME  @"https"
#endif
#ifndef YT_PROXY_HOST
#define YT_PROXY_HOST    @"proxy.example.invalid"  // set PROXY_HOST when building a release package
#endif
#ifndef YT_PROXY_PORT
#define YT_PROXY_PORT    443
#endif
#define YT_SPOOF_VERSION @"19.29.1"         // version string the app reports
#define YT_SPOOF_UA      @"Mozilla/5.0 (iPhone; CPU iPhone OS 7_1_2 like Mac OS X) AppleWebKit/537.51.2 (KHTML, like Gecko) Version/7.0 Mobile/11D257 Safari/9537.53"
// ==============================================================

static BOOL YTLFShouldProxy(NSString *host) {
    if (!host.length) return NO;
    NSString *h = host.lowercaseString;
    if ([h isEqualToString:YT_PROXY_HOST]) return NO;
    // Keep the app sign-in, account chooser, and identity bootstrap on Google.
    NSArray *suffixes = @[@"youtube.com", @"googleapis.com", @"googlevideo.com", @"ytimg.com", @"ggpht.com"];
    for (NSString *s in suffixes) {
        if ([h isEqualToString:s] || [h hasSuffix:[@"." stringByAppendingString:s]]) return YES;
    }
    return NO;
}

static NSURLRequest *YTLFRewrite(NSURLRequest *req) {
    NSURL *u = req.URL;
    if (!u) return req;
    // SSO uses Google cookies and must complete against Google directly.
    if ([u.host.lowercaseString isEqualToString:@"m.youtube.com"] &&
        ([u.path isEqualToString:@"/ytscframe"] ||
         [u.path isEqualToString:@"/getAccountSwitcherEndpoint"] ||
         [u.path isEqualToString:@"/feed/library"] ||
         [u.path isEqualToString:@"/signin"])) return req;
    if (!YTLFShouldProxy(u.host)) return req;
    NSURLComponents *c = [NSURLComponents componentsWithURL:u resolvingAgainstBaseURL:NO];
    NSString *origHost = u.host;
    NSString *origScheme = u.scheme ?: @"https";
    c.scheme = YT_PROXY_SCHEME;
    c.host = YT_PROXY_HOST;
    c.port = @(YT_PROXY_PORT);
    NSMutableURLRequest *m = [[req mutableCopy] autorelease];
    m.URL = c.URL;
    [m setValue:origHost forHTTPHeaderField:@"X-Orig-Host"];
    [m setValue:origScheme forHTTPHeaderField:@"X-Orig-Scheme"];
    NSLog(@"[YTLF] %@ %@ -> %@", req.HTTPMethod, u.absoluteString, c.URL.absoluteString);
    return m;
}

// ---------- Networking: NSURLConnection ----------
%hook NSURLConnection
+ (NSData *)sendSynchronousRequest:(NSURLRequest *)r returningResponse:(NSURLResponse **)resp error:(NSError **)err {
    return %orig(YTLFRewrite(r), resp, err);
}
+ (void)sendAsynchronousRequest:(NSURLRequest *)r queue:(NSOperationQueue *)q completionHandler:(void (^)(NSURLResponse *, NSData *, NSError *))h {
    %orig(YTLFRewrite(r), q, h);
}
+ (NSURLConnection *)connectionWithRequest:(NSURLRequest *)r delegate:(id)d {
    return %orig(YTLFRewrite(r), d);
}
- (instancetype)initWithRequest:(NSURLRequest *)r delegate:(id)d {
    return %orig(YTLFRewrite(r), d);
}
- (instancetype)initWithRequest:(NSURLRequest *)r delegate:(id)d startImmediately:(BOOL)s {
    return %orig(YTLFRewrite(r), d, s);
}
%end

// ---------- Networking: NSURLSession ----------
%hook NSURLSession
- (NSURLSessionDataTask *)dataTaskWithRequest:(NSURLRequest *)r {
    return %orig(YTLFRewrite(r));
}
- (NSURLSessionDataTask *)dataTaskWithRequest:(NSURLRequest *)r completionHandler:(void (^)(NSData *, NSURLResponse *, NSError *))h {
    return %orig(YTLFRewrite(r), h);
}
- (NSURLSessionDataTask *)dataTaskWithURL:(NSURL *)u {
    return [self dataTaskWithRequest:[NSURLRequest requestWithURL:u]];
}
- (NSURLSessionDataTask *)dataTaskWithURL:(NSURL *)u completionHandler:(void (^)(NSData *, NSURLResponse *, NSError *))h {
    return [self dataTaskWithRequest:[NSURLRequest requestWithURL:u] completionHandler:h];
}
- (NSURLSessionUploadTask *)uploadTaskWithRequest:(NSURLRequest *)r fromData:(NSData *)d {
    return %orig(YTLFRewrite(r), d);
}
- (NSURLSessionUploadTask *)uploadTaskWithRequest:(NSURLRequest *)r fromData:(NSData *)d completionHandler:(void (^)(NSData *, NSURLResponse *, NSError *))h {
    return %orig(YTLFRewrite(r), d, h);
}
- (NSURLSessionDownloadTask *)downloadTaskWithRequest:(NSURLRequest *)r {
    return %orig(YTLFRewrite(r));
}
- (NSURLSessionDownloadTask *)downloadTaskWithRequest:(NSURLRequest *)r completionHandler:(void (^)(NSURL *, NSURLResponse *, NSError *))h {
    return %orig(YTLFRewrite(r), h);
}
%end

// ---------- "Update required": spoof the app version ----------
%hook NSBundle
- (id)objectForInfoDictionaryKey:(NSString *)key {
    id v = %orig;
    if (self == [NSBundle mainBundle] && [key isEqualToString:@"CFBundleShortVersionString"]) {
        return YT_SPOOF_VERSION;
    }
    return v;
}
- (NSDictionary *)infoDictionary {
    NSDictionary *d = %orig;
    if (self == [NSBundle mainBundle] && d) {
        NSMutableDictionary *m = [[d mutableCopy] autorelease];
        m[@"CFBundleShortVersionString"] = YT_SPOOF_VERSION;
        return m;
    }
    return d;
}
%end

// Sign-in stays inside the app. signin.x bridges the legacy Google SSO flow
// into the app's native identity/keychain callbacks.
%hook UIWebView
- (void)loadRequest:(NSURLRequest *)request {
    NSString *h = request.URL.host.lowercaseString;
    if ([h hasSuffix:@"google.com"] || [h hasSuffix:@"youtube.com"]) {
        NSMutableURLRequest *m = [[request mutableCopy] autorelease];
        [m setValue:YT_SPOOF_UA forHTTPHeaderField:@"User-Agent"];
        %orig(m);
        return;
    }
    %orig;
}
%end

// ---------- Playback: log what the player is handed (rewrite once known) ----------
%hook AVPlayerItem
+ (instancetype)playerItemWithURL:(NSURL *)url {
    NSLog(@"[YTLF] AVPlayerItem URL: %@", url);
    return %orig;
}
- (instancetype)initWithURL:(NSURL *)url {
    NSLog(@"[YTLF] AVPlayerItem initWithURL: %@", url);
    return %orig;
}
%end

%hook MPMoviePlayerController
- (void)setContentURL:(NSURL *)url {
    NSLog(@"[YTLF] MPMoviePlayer URL: %@", url);
    %orig;
}
%end

%ctor {
    // UIWebView picks its UA from this default; covers sub-requests too.
    [[NSUserDefaults standardUserDefaults] registerDefaults:@{@"UserAgent": YT_SPOOF_UA}];
    NSLog(@"[YTLF] loaded, proxy %@://%@:%d", YT_PROXY_SCHEME, YT_PROXY_HOST, YT_PROXY_PORT);
}
