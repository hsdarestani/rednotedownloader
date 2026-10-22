# Shared VPS rollout: live preflight and staging result (2026-10-22)

## Verified target: 143.20.60.195:9011
Ubuntu 24.04; 1 vCPU, 1.9 GiB RAM, 20 GB root volume. After safe build-cache reclaim: 1.9 GB free (~90% occupied). Nginx owns TCP/80 and TCP/443.

## Real changes completed on target
- **BebinKarato:** repo `hsdarestani/bebinkarato` cloned into `/opt/bebinkarato/app`, app Docker image built, container `bebinkarato` running HEALTHY bound to **127.0.0.1:8106**. `GET /health` verified. Isolated staging database under Docker data volume. Telegram and Cloudflare AI intentionally disabled to prevent duplicate polling and unapproved user-data reset. Admin enabled with random password in server-only mode 0600 `.env`; not published to internet.
- **CamCam:** repo `hsdarestani/cacmcam` cloned into `/opt/camcam`. Isolated Compose stack `camcam-shared` with `db` PostgreSQL, `redis`, and `api` running. API health `GET http://127.0.0.1:8107/health` returned `{"ok":true,"service":"camcam"}`. New isolated test volumes; **not production data**. On 2026-10-22 (later run), also launched pet, MediaMTX, archive and petarchive from the **existing shared CamCam API image** with no duplicate builds. All 7 app components and a loopback Caddy gateway are running. Gateway binds 127.0.0.1:8117; local GET /, /pet, /camera, /health each returned HTTP 200. WebRTC transport port 8189 tcp/udp is published, but camera behavior over public Internet still needs real device/cutover tests. No DNS cutover. Credentials and production DB/recordings remain unmigrated.
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

## Updated evidence after CamCam expanded staging
- Initial four-image CamCam build failed on disk pressure; [failed build](https://github.com/hsdarestani/rednotedownloader/actions/runs/39853462623); no production state was touched.
- [Build-cache recovery successful](https://github.com/hsdarestani/rednotedownloader/actions/runs/39854062036); disk restored to ~1.8 GiB available and RedNote / BebinKarato / CamCam API health stayed OK.
- [Shared image deployment SUCCESS](https://github.com/hsdarestani/rednotedownloader/actions/runs/39854773425): CamCam API+DB+Redis+Pet+MediaMTX+Archive+PetArchive and local Caddy gateway (all started), approx 966928 KiB MemAvailable and 1793180 KiB disk after launch.
- [Local route smoke PASS](https://github.com/hsdarestani/rednotedownloader/actions/runs/39855130722): /, /pet, /camera returned HTML HTTP 200, /health returned JSON HTTP 200 via localhost:8117.
- Existing TLS certificate only for rishe.store, so no valid certificate yet for future app hostnames. DNS MUST remain pointed to old production until backed-up data and SSL are ready.
- Hamoon source audit confirmed all five old SSH secret values are present, but SSH keyscan/connection failed; [diagnostic run](https://github.com/hsdarestani/hamoon-private/actions/runs/39853691725). CamCam and BeOnMeet origin port 22 timed out.

## Release blockers not solved by low simultaneous load
- BebinKarato: current new instance has empty SQLite, Telegram token and Workers AI missing; intentionally no duplicate Telegram polling.
- CamCam: current new instance has fresh PostgreSQL, no original accounts/recordings and no external authenticated camera/media functional QA; external DNS remains unchanged.
- BeOnMeet: original Chrome profiles, recording data, OAuth credentials and database are not reachable; there is no functional BeOnMeet service on the new host. Original stack requires much larger hardware while active (12 slots, three Chromium profiles). Do not start it automatically and take down unrelated services.
- Hamoon: original MySQL/payments state, API credentials, full private runtime and bot token have not migrated; never start a new empty payment DB for a live service.

## DNS changes required only at release time
- `camcam.smarbiz.sbs`: update Cloudflare A origin to `143.20.60.195` **after production data and HTTPS certificate are ready**. Preserve proxy configuration if applicable; do not change the visible Cloudflare edge A result.
- `beonmeet.smarbiz.sbs`: update A origin only if meeting recorder genuinely transferred.
- `pay.hamooncloud.ir`: update A origin only if Hamoon payment callback and production DB genuinely transferred.
- `bebinkarato.smarbiz.sbs`: new subdomain is optional; create A to `143.20.60.195` only when original user data and bot token moved and TLS ready.

## Prepared reverse-proxy configuration (not enabled)
- [Preparation run SUCCESS](https://github.com/hsdarestani/rednotedownloader/actions/runs/39855589116). HTTP vhost templates for `camcam.smarbiz.sbs` and `bebinkarato.smarbiz.sbs` saved to `/opt/shared-vps-staging/proxy-templates/`.
- Nginx virtualhosts were not enabled or reloaded, to preserve current sites and prevent insecure public access before TLS certificates and data migration.
- CamCam local gateway `127.0.0.1:8117`, BebinKarato `127.0.0.1:8106`. Both health routes checked. Certificate only available for `rishe.store`, so HTTPS certificates for new hostnames remain outstanding.
