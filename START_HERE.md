# YTLegacyFix - build and hosting guide

The server and Cydia repository can be hosted remotely so the Mac does not have to stay on. See [HOSTING.md](HOSTING.md) for deployment. These instructions cover local development and device installation.

## 0. What you need
- Xcode (App Store) + `xcode-select --install`, and Homebrew (https://brew.sh)
- Jailbroken iOS 7-9 device on the SAME Wi-Fi as the Mac, with OpenSSH installed from Cydia
- Your device model (decides the build option in tweak/Makefile)
- Terminal commands:  brew install python libimobiledevice dpkg

## 1. Local development server

## 2. Start the server
    cd ytlegacyfix/server
    python3 -m venv venv && . venv/bin/activate
    pip install -r requirements.txt
    ipconfig getifaddr en0                  # your Mac's current LAN address
    ./run.sh          # detects your Mac IP and turns on the old-app fixes below
    # or manually:  YTLF_PUBLIC_BASE=http://<mac-lan-address>:8080 caffeinate -i python server.py
Allow incoming connections if macOS asks. Reserve this IP for the Mac in your router so it never changes.
Test in a browser:  http://<mac-lan-address>:8080/healthz  then  http://<mac-lan-address>:8080/yt/play?v=<any video id>
If /yt/play fails, fix that first:  pip install -U yt-dlp

## 3. Install Theos
    bash -c "$(curl -fsSL https://raw.githubusercontent.com/theos/theos/master/bin/install-theos)"
Open a new Terminal and check:  echo $THEOS

## 4. Configure and build
1. Build with the proxy hostname. For a local test server, use `PROXY_SCHEME=http PROXY_HOST=<mac-lan-address> PROXY_PORT=8080`; for the hosted service, use its HTTPS hostname and port 443.
2. tweak/Makefile builds armv7+arm64 by default (needs iPhoneOS9.3.sdk in $THEOS/sdks). For 64-bit-only devices (iPhone 5s+) use:  make package ARM64ONLY=1
3. Build:
       cd tweak && make clean package PROXY_SCHEME=https PROXY_HOST=<proxy-hostname> PROXY_PORT=443
   Output: tweak/packages/*.deb
If the 32-bit build fails to link on the new Xcode, send me the error.

## 5. Install on the phone
Find the phone IP in Settings > Wi-Fi. Modern macOS SSH is too new for iOS 7-9, so use these flags:
    scp -O -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa packages/*.deb root@<PHONE-IP>:/tmp/
    ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa root@<PHONE-IP> "dpkg -i /tmp/*.deb && killall YouTube"
Default root password is "alpine" - change it afterwards with `passwd`.
If SSH still refuses, add:  -o KexAlgorithms=+diffie-hellman-group1-sha1

## 6. Test and capture
Open YouTube on the phone: home, search, a video, sign-in.
- Server log:  tail -f ytlegacyfix/server/capture.jsonl   (auth headers redacted; skim before sharing)
- Phone log (USB):  idevicesyslog | grep YTLF
No [YTLF] request lines = the app uses a different network stack; send me what you see.

## 7. Iterate
Send me the first failing entries (update-required response, the player request/response, the sign-in error).
Fixes go in server/server.py (transform_request / transform_response / player_response) or tweak/Tweak.x.
After a Tweak.x change: rebuild and repeat step 5. After a server.py change: restart the server.

Server-side knobs (environment variables, set on the same line as the python command; none are needed unless YouTube rejects the old app):
    YTLF_PB_VERSION      client version written into protobuf requests (default 19.29.1; "" = leave alone)
    YTLF_PB_OSVER        OS version written into protobuf requests (default: leave alone), e.g. 17.5.1
    YTLF_PB_DROP_MS=1    drop the app's "ms" token block from the protobuf context
    YTLF_UA              override the User-Agent header sent to YouTube
    YTLF_DROP_HEADERS    comma-separated headers to strip, e.g. X-Goog-Device-Auth
Example (old app gets 400 FAILED_PRECONDITION):
    YTLF_UA="com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X; en_US)" \
    YTLF_DROP_HEADERS="X-Goog-Device-Auth" YTLF_PB_DROP_MS=1 YTLF_PB_OSVER=17.5.1 \
    YTLF_PUBLIC_BASE=http://$(ipconfig getifaddr en0):8080 python server.py
The tweak's NSBundle version spoof does not change what this app puts on the wire (the capture still showed 11.19), so the version fix lives in the server.

## 8. Cydia repo
See [HOSTING.md](HOSTING.md) for the hosted Cydia source. Do not add the Mac's LAN address as a public source.
