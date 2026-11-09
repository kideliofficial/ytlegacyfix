# YTLegacyFix

Compatibility tweak and proxy server for the final YouTube app on iOS 7.

The project contains the Theos tweak (`tweak/`), the playback/API proxy (`server/`), and the Cydia APT repository files (`repo/`). See [HOSTING.md](HOSTING.md) to deploy the repo and proxy without keeping a Mac running. See [START_HERE.md](START_HERE.md) for local development and device setup.

The release package must be built with the proxy hostname assigned by the hosting provider. Do not publish a package built with `proxy.example.invalid` or a private LAN address.

TubeReplacer-derived sign-in compatibility source is covered by GPL-3.0; its license and attribution notice are included in `tweak/`.
