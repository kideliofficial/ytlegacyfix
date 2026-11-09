# Hosting

This project deploys the playback proxy from a public Git repository. The Cydia feed is served directly from GitHub Raw, so it does not need a separate hosting service.

## Current URLs

- Proxy: `https://ytlegacyfix-proxy-7pro.onrender.com`
- Cydia source: `https://raw.githubusercontent.com/kideliofficial/ytlegacyfix/main/repo/`
- Package version: `0.1.0-8`

The Render blueprint is `render.yaml`. The current proxy was deployed from this repository on Render's free web service. It sleeps after 15 minutes without traffic and may take about a minute to wake. Its filesystem is temporary, so cached video files are not permanent.

## Publishing an update

Build the tweak with the currently deployed proxy hostname:

```sh
cd tweak
make clean package PROXY_SCHEME=https PROXY_HOST=ytlegacyfix-proxy-7pro.onrender.com PROXY_PORT=443
cd ../repo
./build_repo.sh
```

Before rebuilding, increase `Version` in `tweak/control` so Cydia sees the new package. Commit the generated `.deb`, `Packages`, `Packages.gz`, `Packages.bz2`, and `Release` under `repo/` to GitHub. GitHub Raw serves the updated feed at the same Cydia source URL above.

If the proxy code or `render.yaml` changes, open the `ytlegacyfix` Blueprint in Render and run **Manual sync**. The public-repository connection does not promise automatic deploys for every commit.

The cloud proxy disables request-body captures and writes logs to `/dev/null`; do not enable `YTLF_SAVE_DIR` or file logging on the public service. YouTube requests may contain account credentials.

The package feed's GitHub Raw TLS compatibility should be confirmed on the target iOS 7 device. YouTube may also behave differently when requests come from a datacenter IP.
