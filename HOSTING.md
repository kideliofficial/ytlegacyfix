# Hosting

This project deploys two independent services from one public Git repository:

- `ytlegacyfix-proxy` runs the playback/API proxy from `server/`.
- `ytlegacyfix-cydia` serves the static APT repository from `repo/`.

The Render blueprint is `render.yaml`. Connect this Git repository in Render and create a Blueprint from that file. Render assigns each service its own `onrender.com` URL. The proxy uses its assigned URL for generated video stream links.

After Render creates the proxy service, build the tweak with the exact hostname shown in its dashboard:

```sh
cd tweak
make clean package PROXY_SCHEME=https PROXY_HOST=<proxy-hostname> PROXY_PORT=443
cd ../repo
./build_repo.sh
```

Commit the generated `.deb`, `Packages`, `Packages.gz`, `Packages.bz2`, and `Release` under `repo/`; Render then republishes the Cydia source. Add the static site's HTTPS URL to Cydia Sources.

The cloud proxy disables request-body captures and writes logs to `/dev/null`; do not enable `YTLF_SAVE_DIR` or file logging on the public service. YouTube requests may contain account credentials.

Render's free web service sleeps after 15 minutes without traffic and may take about a minute to wake. It also has an ephemeral filesystem, so cached video files are temporary. The static Cydia site does not need the Mac to stay on. A paid, always-on proxy plan avoids the free service's idle sleep; no paid plan is selected by this project.

Test both HTTPS URLs from the iOS 7 device before relying on them. Cydia's old TLS stack may not accept every modern certificate chain, and YouTube may behave differently when requests come from a datacenter IP.
