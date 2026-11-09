# Free HTTP host on Oracle Cloud

This setup serves the YouTube proxy and the Cydia feed from one public IPv4 address. The intended endpoints are:

- Proxy: `http://<public-ip>/`
- Cydia source: `http://<public-ip>/cydia/`

This is an HTTP-only legacy setup. Requests and package downloads are not encrypted or authenticated in transit. Use it only because the target iOS 7 client cannot connect to the current HTTPS host.

## Create the VM

Create an Oracle Cloud account and accept Oracle's terms yourself. In the home region, create an **Always Free** Ampere A1 VM (Ubuntu ARM64, 1 OCPU, 4 GB RAM) and keep it within the Always Free limits. Assign its public IPv4 address. Oracle may reclaim an Always Free VM it considers idle for seven days; it is not an uptime guarantee.

In the VM's public subnet security list, allow inbound TCP port 80 from `0.0.0.0/0` and SSH from your own IP. Do not open other inbound ports.

## Deploy the service

From the VM, install Docker and Git, clone this repository, then run these commands from the repository root. For a one-command install or refresh, run `sudo bash oci-http/bootstrap.sh <public-ip>` from a checkout on the VM; it installs Docker, builds the image, and starts the service on port 80:

```sh
sudo docker build -f oci-http/Dockerfile -t ytlegacyfix .
sudo docker run -d --name ytlegacyfix --restart unless-stopped \
  -p 80:8080 \
  -e YTLF_PUBLIC_BASE=http://<public-ip> \
  -e YTLF_LOG=/dev/null \
  -e YTLF_SAVE_DIR= \
  -e YTLF_UA='com.google.ios.youtube/21.26.4 (iPhone16,2; U; CPU iOS 18_3_2 like Mac OS X;)' \
  -e YTLF_DROP_HEADERS=X-Goog-Device-Auth \
  -e YTLF_PB_DROP_MS=1 \
  -e YTLF_PB_VERSION=21.26.4 \
  -e YTLF_PB_OSVER=18.3.2.22D82 \
  ytlegacyfix
```

Replace `<public-ip>` with the VM's actual public IPv4 address. Do not enable request-body capture or file logging on the public proxy.

## Build and publish a package for this VM

On a machine with Theos installed, build the tweak with the VM's public IP:

```sh
cd tweak
make clean package PROXY_SCHEME=http PROXY_HOST=<public-ip> PROXY_PORT=80
cd ../repo
./build_repo.sh
```

Commit the new package and generated repository index files to GitHub. On the VM, `git pull`, then rebuild and restart the container so its checked-out `repo/` is included. Alternatively, rerun `sudo bash oci-http/bootstrap.sh <public-ip>` from the VM checkout; it pulls the latest repository and rebuilds the image:

```sh
sudo docker stop ytlegacyfix
sudo docker rm ytlegacyfix
sudo docker build -f oci-http/Dockerfile -t ytlegacyfix .
```

Run the `docker run` command again. On the iPhone, add `http://<public-ip>/cydia/` to Cydia and refresh sources.
