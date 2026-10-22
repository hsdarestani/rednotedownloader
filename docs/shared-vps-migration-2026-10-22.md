# Shared VPS rollout: live preflight and staging result (2026-10-22)

## Verified target: 143.20.60.195:9011
Ubuntu 24.04; 1 vCPU, 1.9 GiB RAM, 20 GB root volume. After safe build-cache reclaim: 1.9 GB free (~90% occupied). Nginx owns TCP/80 and TCP/443.

## Real changes completed on target
- **BebinKarato:** repo `hsdarestani/bebinkarato` cloned into `/opt/bebinkarato/app`, app Docker image built, container `bebinkarato` running HEALTHY bound to **127.0.0.1:8106**. `GET /health` verified. Isolated staging database under Docker data volume. Telegram and Cloudflare AI intentionally disabled to prevent duplicate polling and unapproved user-data reset. Admin enabled with random password in server-only mode 0600 `.env`; not published to internet.
- **CamCam:** repo `hsdarestani/cacmcam` cloned into `/opt/camcam`. Isolated Compose stack `camcam-shared` with `db` PostgreSQL, `redis`, and `api` running. API health `GET http://127.0.0.1:8107/health` returned `{"ok":true,"service":"camcam"}`. New isolated test volumes; **not production data**. Remaining pet/streaming/archiving and public Caddy not started, credentials missing. No DNS cutover.
- **BeOnMeet:** repo `hsdarestani/beonmeet` source cloned under `/opt/beonmeet`. Recorder containers **not launched**; require existing Google sign-in Chrome profiles, state and adequate resource budget, not empty fresh instance.
- **HamoonCloud:** main production repo `hsdarestani/hamoon-private` (private); not copied to publicly accessible repository nor deployed yet; needs origin SSH keys, database, secrets, payment callback cutover.

## Existing production unchanged
RedNote systemd active, Rishestore, Orders Vesta, Orders Cutella containers still running, Nginx config verified. No existing prod data edited, DNS unchanged, no existing Telegram token reused.

## Evidence
- [Host inventory](https://github.com/hsdarestani/rednotedownloader/actions/runs/39830021185)
- [DNS and Nginx inspection](https://github.com/hsdarestani/rednotedownloader/actions/runs/39830273681)
- [BebinKarato staging success](https://github.com/hsdarestani/rednotedownloader/actions/runs/39845926071)
- [CamCam API, PostgreSQL and Redis staging success](https://github.com/hsdarestani/rednotedownloader/actions/runs/39847088624)
- [Build-cache-only clean and postchecks](https://github.com/hsdarestani/rednotedownloader/actions/runs/39847607111)
- Origin audits: CamCam and BeOnMeet SSH port 22 timed out from GitHub runners. Hamoon origin diagnostic could not establish required credential set. BebinKarato origin workflow did not run; avoid assuming its data is absent.

## Next necessary steps for real migration
1. Obtain controlled access to original application servers/data and runtime secrets, with no tokens ever committed. Back up application databases, BeOnMeet Chrome profiles, CamCam recordings and user files; verify restore.
2. Verify capacity before starting recording/streaming services: even low *concurrent load* does not remove BeOnMeet/Chrome runtime overhead. Keep each Compose project separate, guard disk >1 GiB during migration.
3. Migrate SQLite/Postgres/Redis state as applicable, run migrations/smoke checks.
4. Keep Nginx at 80/443, route apps to localhost ports. Do not publish project-specific Caddy to those ports.
5. Switch Telegram polling exactly once; never have two copies active on same bot token.
6. After each app passes full functional smoke checks and rollback is available, change DNS A **origin** records to `143.20.60.195`: `camcam.smarbiz.sbs`, `beonmeet.smarbiz.sbs`, `pay.hamooncloud.ir` (only for Hamoon API/payment if transferred). Optional new `bebinkarato.smarbiz.sbs`. DNS records for Cloudflare-proxied sites remain proxied as appropriate. Do not point domains at staging yet.
