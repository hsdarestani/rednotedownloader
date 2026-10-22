# Fresh installation: four apps on the RedNote shared VPS

**Checked on:** 2026-10-22 22:58 UTC
**Shared VPS:** `143.20.60.195`, SSH `9011`, Ubuntu 24.04
**Hardware:** 1 vCPU, ~1.9 GiB RAM + 2 GiB swap, 20 GB root SSD; 3.4 GB free after four-service acceptance.
**Ground truth:** [successful final acceptance run](https://github.com/hsdarestani/rednotedownloader/actions/runs/39868617608).

## Production-like services installed from zero

| App | Directory | Internal listener | Installed/running | Not yet operational |
|---|---|---|---|---|
| RedNote Downloader | existing deployment | Telegram service | Existing systemd active, unchanged | Existing setup preserved |
| BebinKarato | `/opt/bebinkarato/app` | 127.0.0.1:8106 | Python app, fresh SQLite, admin password locally generated, healthy Docker container | Telegram bot token, Cloudflare AI credentials and public TLS |
| CamCam | `/opt/camcam` | 127.0.0.1:8117 (gateway), 8107 (API), TCP/UDP 8189 (MediaMTX) | API, PostgreSQL, Redis, pet, MediaMTX, archive, petarchive, local Caddy gateway all running; /, /pet, /camera all HTTP 200 | External AI/Firebase/payment secrets, HTTPS/DNS, real device/streaming tests |
| HamoonCloud | `/home/hamoondeploy/hamoon-source` | 127.0.0.1:8108 | Private repository source, Node dependencies, new MariaDB schema, localhost-only dashboard/API with fresh admin credentials, systemd enabled | Hetzner/OpenStack/payment/provider and Telegram tokens, public HTTPS, real billing tests |
| BeOnMeet | `/opt/beonmeet` | 127.0.0.1:8109 (controller), 9124 (worker), 9123 (Chrome CDP), 6081 (Chrome noVNC), 9125 (Redis) | Fresh SQLite/controller, one Chromium instance, one optimized TypeScript recording worker with initialized PulseAudio sink, Redis, private callback bridge on Docker gateway port 8119. All service/network health probes passed. | Google OAuth creds and account consent, Telegram token, recorder Google login, real Meet recording/delivery test, public HTTPS/DNS |

**Important:** BeOnMeet controller is in `BEONMEET_STAGING_NO_POLL=1` with `FREE_MEETING_SLOTS=0` until all required Google/Telegram integrations are configured. The **worker** allows one meeting at a time, but no user requests will be processed yet. This is deliberate, not a claim that production recording was tested.

All four services were created with **fresh databases** at user direction; previous production state was not available and was not migrated. No production wallet balance, account, previous recording, or OAuth consent was recreated.

## Evidence and controls

- [BeOnMeet Chromium build success](https://github.com/hsdarestani/rednotedownloader/actions/runs/39863370245)
- [BeOnMeet Chrome/CDP/noVNC localhost startup](https://github.com/hsdarestani/rednotedownloader/actions/runs/39865977263)
- [Lean TypeScript worker image success](https://github.com/hsdarestani/rednotedownloader/actions/runs/39866354861)
- [BeOnMeet audio-fixed image success](https://github.com/hsdarestani/rednotedownloader/actions/runs/39868044863)
- [BeOnMeet Chrome / worker / PulseAudio / private controller end-to-end wiring success](https://github.com/hsdarestani/rednotedownloader/actions/runs/39868311054)
- [All four apps' internal acceptance and legacy system health pass](https://github.com/hsdarestani/rednotedownloader/actions/runs/39868617608)
- [Hamoon fresh database and dashboard success](https://github.com/hsdarestani/rednotedownloader/actions/runs/39861388948)
- [BebinKarato staging success](https://github.com/hsdarestani/rednotedownloader/actions/runs/39845926071)
- [CamCam full healthy local stack](https://github.com/hsdarestani/rednotedownloader/actions/runs/39854773425)
- [Disk space reclaimed without touching active containers](https://github.com/hsdarestani/rednotedownloader/actions/runs/39862865989)
- [Daily local backups enabled and test snapshots generated](https://github.com/hsdarestani/rednotedownloader/actions/runs/39866934960). Local backups at `/var/backups/shared-vps-apps/` with `shared-vps-backup.timer` at 03:45 UTC (random delay up to 20 minutes), 2-day retention; includes Hamoon MariaDB, CamCam PostgreSQL, BebinKarato and BeOnMeet SQLite if present. No offsite backup has been configured.
- [HTTP reverse-proxy templates prepared](https://github.com/hsdarestani/rednotedownloader/actions/runs/39862586028).

## Existing production preserved

Nginx remains the only service on host ports 80 and 443 and still serves `rishe.store`, `ordersvesta.smarbiz.sbs`, and `orderscutella.smarbiz.sbs`. RedNote systemd and all existing store/bot containers passed read-only final checks. CamCam's Caddy binds only to local port 8117, not host 80/443. BeOnMeet's Chrome noVNC and CDP bind **only to localhost**, not the public internet.

## Outstanding domain/DNS and external credential tasks

**No DNS records have been changed, and no public HTTPS sites for these four apps have been enabled.** Four Nginx frontend templates exist at `/opt/shared-vps-staging/proxy-templates/` but are intentionally not in `sites-enabled`. Only an existing Let's Encrypt certificate for `rishe.store` was discovered.

When ready to release, point A records to `143.20.60.195`:

| DNS name | Type | IPv4 | Notes |
|---|---|---|---|
| camcam.smarbiz.sbs | A | 143.20.60.195 | Cloudflare may be proxied only after host/cert setup as appropriate |
| beonmeet.smarbiz.sbs | A | 143.20.60.195 | Google OAuth redirect needs the exact publicly accessible HTTPS hostname |
| pay.hamooncloud.ir | A | 143.20.60.195 | Do not accept real payments before payment gateway credentials / callbacks are tested |
| bebinkarato.smarbiz.sbs | A | 143.20.60.195 | Suggested new site domain; Telegram polling needs a valid token |

Then issue four hostname certificates with ACME/Certbot, enable Nginx proxy vhosts on ports 443 with valid certificates, and verify public routes before enabling external bot/payment workflows. Do not enable unauthenticated noVNC or Chrome CDP on the public internet.

### External account secrets to set directly on VPS (never commit)
- BebinKarato: `TELEGRAM_BOT_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.
- CamCam: `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_AI_TOKEN`, `FIREBASE_SERVICE_ACCOUNT_B64` as needed, payment merchant key if payments enabled.
- BeOnMeet: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`; authorize OAuth redirect `https://beonmeet.smarbiz.sbs/auth/google/callback`, connect recorder Google account in private noVNC UI, and do an actual Google Meet recording/replay/delivery QA test.
- Hamoon: Telegram, Hetzner/OpenStack/Afracloud provider secrets and gateway/payment credentials; test wallet top-up and gateway reconciliation before launch. **Never claim real users or wallets were migrated.**

### Operational watchouts
- The host has **1 vCPU and 2 GB RAM**. Services and single BeOnMeet browser/worker pass idle health; this does not prove safe concurrent recording load. Memory limits and one-worker policy are deliberate.
- Local backups don't protect against full VPS loss. Configure offsite backups when storage is available.
- Temporary secret-bridge experiments were disabled and their VPS private encryption key deleted. No project secret values were printed to this report.
- Ops changes are on GitHub branch `ops/shared-vps-inventory-20261022`; source-repo experiments are on separate `ops/shared-vps-source-audit-20261022` branches. Existing main-branch deployment workflows have **not** been retargeted; do not run old GitHub Actions deploy against the defunct origins.


## HTTP ACME preflight installed after acceptance
- [Safe four-domain Nginx preflight SUCCESS](https://github.com/hsdarestani/rednotedownloader/actions/runs/39869075648).
- Nginx now has exact port-80 vhosts for each of the four proposed public domains serving **only ACME HTTP-01 challenges** from `/var/www/shared-vps-acme/.well-known/acme-challenge/`. All other plain HTTP requests return 503 and do not expose app/admin/payment traffic.
- After Nginx reload, all four hostnames were verified to return 503 on the target with their Host headers; existing RedNote, CamCam, BebinKarato, Hamoon and BeOnMeet internal probes still passed.
- **Important next external action:** Create/update the four DNS A records to `143.20.60.195`, preferably set Cloudflare DNS-only during first HTTP-01 certificate issuance. The user has not yet pointed these domains and there is no Cloudflare DNS management connection in this session. Once A records resolve, use Certbot with HTTP-01 webroot to issue certificates for each hostname; then create SSL 443 proxy vhosts and apply `nginx -t` plus controlled reload.
- Do not interpret HTTP-503 placeholder responses as live public availability. The apps are healthy on private loopback, not publicly released.
