# YTLegacyFix

Compatibility tweak and proxy server for the final YouTube app on iOS 7.

The project contains the Theos tweak (`tweak/`), the playback/API proxy (`server/`), and the Cydia APT repository files (`repo/`). See [HOSTING.md](HOSTING.md) to deploy the repo and proxy without keeping a Mac running. See [START_HERE.md](START_HERE.md) for local development and device setup.

The release package must be built with the proxy hostname assigned by the hosting provider. Do not publish a package built with `proxy.example.invalid` or a private LAN address.

## Files

- **Tweak source (Theos project):** [`tweak/`](tweak/)
- **Built Cydia package:** [`YTLegacyFix 0.1.0-8 (.deb)`](repo/debs/com.example.ytlegacyfix_0.1.0-8-1+debug_iphoneos-arm.deb)
- **Cydia package index:** [`repo/Packages`](repo/Packages)
- **Cydia source URL:** <https://ytlegacyfix-cydia-trjl.onrender.com/>

## Current hosted setup

- Cydia source: <https://ytlegacyfix-cydia-trjl.onrender.com/>
- Proxy: `https://ytlegacyfix-proxy-7pro.onrender.com`
- Current package: `0.1.0-8`, built for the proxy hostname above.

The Cydia feed is published by the Render static site, so it does not need a Mac to stay on. The proxy runs on Render's free web service and may take about a minute to wake after inactivity.

TubeReplacer-derived sign-in compatibility source is covered by GPL-3.0; its license and attribution notice are included in `tweak/`.
