# Shared VPS migration preflight — 2026-10-22

## Status: NO-GO for four-service production migration

Read-only inspection via [GitHub Actions inventory run](https://github.com/hsdarestani/rednotedownloader/actions/runs/39830021185) and [DNS/Nginx follow-up](https://github.com/hsdarestani/rednotedownloader/actions/runs/39830273681).
No services were migrated, restarted, or modified; no DNS was changed.

### Confirmed RedNote host

- IPv4: `143.20.60.195`, SSH port `9011` (RedNote repo production configuration, verified from host)
- OS: Ubuntu 24.04
- 1 vCPU, 1.9 GiB RAM, 2 GiB swap
- Root disk: 20 GiB total, 17 GiB used, 2.4 GiB available (88% occupied)
- Systemd RedNote downloader active
- Nginx already owns 80/443
- Docker: Rishestore, Orders Vesta, Orders Cutella, RedNote local Telegram API
- Docker unused images: 6.9 GB potentially reclaimable, **not** pruned (preserve recovery capacity)
- No CamCam/BeOnMeet/BebinKarato/Hamoon private installation on target

### Repository and architecture

| Application | Repository | Current migration constraint |
|---|---|---|
| Hamoon Cloud production bot/payment/dashboard | `hsdarestani/hamoon-private` (private) | Node, PM2, database, payments and provider integrations; database/state, secrets and polling must be migrated safely |
| CamCam | `hsdarestani/cacmcam` | WebRTC MediaMTX, PostgreSQL, Redis, archive, several containers, new port 8189 UDP/TCP; needs existing data and secrets |
| BeOnMeet | `hsdarestani/beonmeet` | Existing stack specifically tuned for 16 vCPU / 32 GB host and 12 local recording slots, Chromium profiles, PostgreSQL, Redis, transcription and meeting state |
| BebinKarato | `hsdarestani/bebinkarato` | Python app with local data directory, Telegram polling and Cloudflare token; existing domain includes former IP |

### DNS snapshot from host

- `camcam.smarbiz.sbs` resolved to Cloudflare proxy `188.114.96.3` (not a statement of real origin IP).
- `beonmeet.smarbiz.sbs` resolved to `5.78.54.83`.
- `pay.hamooncloud.ir` resolved to `91.107.241.4`.
- `hamooncloud.ir` resolved to Cloudflare proxy `188.114.97.3`.
- BebinKarato current Caddy hostname `5-161-144-101.sslip.io` resolves to `5.161.144.101`.

### Preconditions before migration

1. Upgrade or replace target VPS. Even the BeOnMeet stack alone expects much more than the current target; for all services on one host, provision enough CPU, RAM, disk and recording storage with margin. Plan capacity against measured production peak load.
2. Full backups of BeOnMeet and CamCam databases, recordings, Redis states where relevant, Chrome profiles and worker identities; Hamoon database and payment state; BebinKarato data directory and Telegram/Cloudflare config. Verify restore path.
3. Preserve RedNote, Rishestore, Orders Vesta and Orders Cutella. Do not touch host Nginx until hostnames and isolated upstreams are defined.
4. Use distinct Compose projects, networks, internal-only app ports, named persistent volumes; one front Nginx proxy for HTTPS 80/443; **remove/override** app-local Caddy published ports so they do not collide.
5. Keep CamCam `8189/udp` (and possibly TCP fallback) reachable, and test real WebRTC from mobile/mobile data.
6. Test app health, authentication, Google OAuth callbacks, payments, Telegram webhooks/polling, AI, push, cameras and recordings before DNS cutover.
7. Avoid concurrent Telegram polling on two hosts with the same token; coordinate cutover.
8. Migrate state and cutover each domain individually with rollback; change Cloudflare-proxied DNS **origin record**, not the public Cloudflare edge IP.

### DNS plan **only after services pass readiness**

| Name | DNS record | Target |
|---|---|---|
| `beonmeet.smarbiz.sbs` | A | `143.20.60.195` |
| `camcam.smarbiz.sbs` | Cloudflare A origin; proxy can remain enabled for HTTPS | `143.20.60.195` |
| `pay.hamooncloud.ir` | A, if Hamoon API/payment actually migrated | `143.20.60.195` |
| Proposed `bebinkarato.smarbiz.sbs` | A (new hostname for admin UI) | `143.20.60.195` |

Do **not** change `hamooncloud.ir` marketing website by default. Do **not** change records before target health checks and migration of state. If BeOnMeet stays on a larger dedicated server, keep its existing A record.

### Production changes made by this investigation

None. A read-only inventory workflow and this note exist solely on branch `ops/shared-vps-inventory-20261022`.
