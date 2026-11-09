# YTLegacyFix

Compatibility tweak and proxy server for the final YouTube app on iOS 7.

The project contains the Theos tweak (`tweak/`), the playback/API proxy (`server/`), and the Cydia APT repository files (`repo/`). See [HOSTING.md](HOSTING.md) to deploy the repo and proxy without keeping a Mac running. See [START_HERE.md](START_HERE.md) for local development and device setup.

The release package must be built with the proxy hostname assigned by the hosting provider. Do not publish a package built with `proxy.example.invalid` or a private LAN address.

## Current hosted setup

- Cydia source: <https://raw.githubusercontent.com/kideliofficial/ytlegacyfix/main/repo/>
- Proxy: `https://ytlegacyfix-proxy-7pro.onrender.com`
- Current package: `0.1.0-8`, built for the proxy hostname above.

The public Cydia feed is served from this repository, so it does not need a Mac or a Render static site. The proxy runs on Render's free web service and may take about a minute to wake after inactivity.

TubeReplacer-derived sign-in compatibility source is covered by GPL-3.0; its license and attribution notice are included in `tweak/`.
