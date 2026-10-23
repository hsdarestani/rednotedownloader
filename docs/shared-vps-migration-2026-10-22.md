# Current verified state — 2026-10-23

This section supersedes the historical migration notes below. **The four-project business launch is not fully complete:** BeOnMeet needs interactive Google account authentication and an actual meeting test; Hamoon lacks provider and bot credentials; CamCam lacks an admin allowlist and its real payment flow depends on Hamoon.

## Acceptance matrix

| Project | Deployment | Credentials | Function actually verified | Remaining acceptance |
|---|---|---|---|---|
| BebinKarato | Running on 127.0.0.1:8106; independent main-branch redeployment passed | Telegram, Cloudflare and admin credentials imported securely | Telegram getMe and webhook/polling-conflict preflight; real AI parsing, reporting, completion and intent smoke; authenticated admin; isolated task database and reminder dispatch; restart recovery | A conversation initiated by a real Telegram user and real reminder delivery were not sent during this acceptance; voice transcription not exercised |
| CamCam | API 8107; gateway 8117; PostgreSQL/Redis/MediaMTX and four app roles running | Cloudflare and Firebase validated with real providers; merchant field imported; ADMIN_EMAILS absent | Public signup/session, camera creation/pairing/heartbeat, real synthetic H.264 WebRTC upload, persisted recording, authenticated MP4 download and ffprobe decode, outsider rejection, logout/login; real AI inference and Firebase OAuth | Owner must supply ADMIN_EMAILS; actual user admin session and real payment callback remain blocked; no real payment captured |
| BeOnMeet | One Chrome, one recorder worker, Redis and controller 8109; isolated controller redeployment passed | Google client credentials, Telegram token and ADMINUSER imported; Telegram provider authentication passed | Controller/Redis/Chrome/worker links, audio sink readiness from existing acceptance, public OAuth redirect/callback-state guard, restart and repeated controller deploy | Calendar OAuth consent absent; recorder Chrome Google login absent/unverified; real Meet recording/audio/file delivery/transcription not tested. Queue stays closed |
| HamoonCloud | systemd hamoon-dashboard; 127.0.0.1:8108; MariaDB retained; independent deploy passed with dedicated key | Only five HAMOON_SERVER_* connection secrets exist; provider and bot fields absent in Actions and live .env | Live database query, dashboard login route, unauthenticated customer API rejection, source redeploy and restart | Provider authentication, Telegram/Host bots, Zibal/wallet/callback/order/provisioning tests require missing credentials. No wallet credits, purchases or payment writes performed |

### Verified Actions evidence

- [Post-rotation SSH and live inventory](https://github.com/hsdarestani/rednotedownloader/actions/runs/39956211386)
- [BebinKarato secure activation and real provider preflight](https://github.com/hsdarestani/bebinkarato/actions/runs/39959583683)
- [BebinKarato successful deployment after merged PR](https://github.com/hsdarestani/bebinkarato/actions/runs/39986645409)
- [Real BebinKarato AI parsing/report smoke, isolated reminder and real CamCam/Firebase provider checks](https://github.com/hsdarestani/rednotedownloader/actions/runs/39960454712)
- [CamCam secure integration activation](https://github.com/hsdarestani/cacmcam/actions/runs/39959129888)
- [CamCam real synthetic camera recording and archive acceptance](https://github.com/hsdarestani/cacmcam/actions/runs/39988543898)
- [CamCam independent shared-VPS deploy](https://github.com/hsdarestani/cacmcam/actions/runs/39986735498)
- [Mocked-provider payment concurrency and amount checks on disposable PostgreSQL: 4 tests passed](https://github.com/hsdarestani/cacmcam/actions/runs/39987641093)
- [BeOnMeet credentials imported without opening queue](https://github.com/hsdarestani/beonmeet/actions/runs/39959754038)
- [BeOnMeet isolated controller redeployment](https://github.com/hsdarestani/beonmeet/actions/runs/39987203627)
- [Hamoon live database/authentication-guard verification](https://github.com/hsdarestani/hamoon-private/actions/runs/39986358760)
- [Hamoon independent source/dashboard deployment](https://github.com/hsdarestani/hamoon-private/actions/runs/39987157003)
- [Restore of fresh PostgreSQL, MariaDB and SQLite backups into isolated test databases](https://github.com/hsdarestani/rednotedownloader/actions/runs/39960302353)
- [All four application restart recovery, private ports, env permissions, old-service preservation and monitoring](https://github.com/hsdarestani/rednotedownloader/actions/runs/39987060250)
- [Public HTTPS, redirects, invalid OAuth state and unauthorized API checks](https://github.com/hsdarestani/rednotedownloader/actions/runs/39988548390)

- [Payment correction production deployment — PASSED](https://github.com/hsdarestani/cacmcam/actions/runs/39987800033)

## Deployment and rollback

All mutating deploy scripts verify IP 143.20.60.195 on SSH port 9011 and take host lock /var/lock/pars-vds-bot-deploy.lock. GitHub concurrency does not cancel an in-flight release. Main runtime secrets are retained rather than overwritten by blank GitHub fields.

- **BebinKarato:** PR [#1](https://github.com/hsdarestani/bebinkarato/pull/1) merged. Main .github/workflows/deploy.yml deploys project bebinkarato-shared, only service app, with public Nginx untouched. It snapshots .env and SQLite, retains an initially untracked compose override before checkout, rebuilds only changed runtime code, and restores previous source/image on failed readiness. The original first post-merge run failed safely before container changes due to the untracked staging compose file; commit 058f141 fixed this and run 39986645409 passed.
- **CamCam:** main .github/workflows/deploy.yml now targets the shared VPS. One API image is reused for api/pet/archive/petarchive. Deployment snapshots env/config/PostgreSQL, leaves db/redis/mediamtx untouched, updates only application roles, validates/reloads the existing loopback Caddy gateway and keeps prior source/image for rollback. Never start the standalone compose caddy service on host 80/443.
- **BeOnMeet:** main .github/workflows/deploy.yml validates pushes but deploys only on workflow_dispatch. Manual deployment uses the reviewed ops/shared-vps-source-audit-20261022 runtime branch, checks one-worker idleness, preserves env/state and restarts only beonmeet-controller. It does not rebuild Chrome or create extra workers. Runtime source changes must first be reviewed on that branch. On failed health, restore previous source commit and restart the controller. The legacy multi-Chrome automatic deployment has been replaced.
- **Hamoon:** main .github/workflows/deploy-dashboard.yml is manual, uses the reviewed ops/shared-vps-source-audit-20261022 branch and HAMOON_SERVER_SSH_KEY with pinned SSH host key. Its source-only archive excludes .env, key files and node_modules. It restarts only hamoon-dashboard.service, not bots or provider jobs. hamoondeploy has a sudoers entry limited to exactly /usr/bin/systemctl restart hamoon-dashboard.service; the service itself runs as hamoondeploy. Retain the prior reviewed source commit for redeployment and keep the live .env/database. No PM2 or old-host deploy is used by this primary workflow.
- **RisheStore:** PR [#1](https://github.com/hsdarestani/rishestore/pull/1) remains draft and unmerged. Review confirmed its old workflow still recreates Caddy and includes legacy commerce import behavior. Port/lock changes alone are insufficient to authorize that deployment on the shared host. No RisheStore release or data migration was executed.

Important implementation commits:
- [BebinKarato final deploy fix](https://github.com/hsdarestani/bebinkarato/commit/058f14191a1ed1ca3188e8a325a7177835b2584d)
- [CamCam shared deployment](https://github.com/hsdarestani/cacmcam/commit/1f5d7165d7f05ca1b38d6f2b5722882ad26c4daa) and [tested payment concurrency fix](https://github.com/hsdarestani/cacmcam/commit/6b054276ca20d854b851cf1babb8eba7f4e630a9)
- [BeOnMeet safe controller workflow](https://github.com/hsdarestani/beonmeet/commit/bbb600501376aae3bfe91865de6bad0ddebefe06)
- [Hamoon pinned-host manual dashboard deploy](https://github.com/hsdarestani/hamoon-private/commit/2c1189a3a5a5012250a8f22a137a816ab187b4a9)

### CamCam payment safety

Payment activation was amended to refresh and lock the payment row after provider verification, recheck activation under that lock, serialize user subscription changes and reject malformed amount values. Four tests against an isolated PostgreSQL database and a mocked provider cover simultaneous callbacks granting one period, wrong amount, malformed amount and mismatched intent. This proves those application checks, **not a live Zibal/Hamoon payment integration**. Real payment enablement remains blocked until Hamoon credentials and real gateway callback verification are available.

## Data, backup and host safety

- Fresh existing application databases were retained. No prior-server customer/wallet state was imported.
- Daily /usr/local/sbin/shared-vps-backup remains enabled, at 03:45 UTC with up to 20 minutes jitter and existing two-day retention. Snapshots are under /var/backups/shared-vps-apps with private permissions.
- Actual restore acceptance passed for CamCam PostgreSQL and Hamoon MariaDB in separately named disposable databases, and integrity/schema checks passed for copied SQLite snapshots. Only those acceptance-created test databases were removed; live databases were not restored over or deleted.
- BebinKarato pre-deployment SQLite snapshots live under app/data; env/config and application image/source snapshots are retained for release rollback. These release snapshots have no additional automatic retention policy yet; watch disk usage.
- No offsite backup destination or credentials were supplied. Local snapshots do not cover full VPS loss; offsite backup remains pending.
- Nginx retains 80/443. Database, Redis, Chrome CDP 9123, recorder 9124 and noVNC 6081 listeners are private. CamCam's 8189 TCP/UDP remains public for ICE media; its public WebRTC test succeeded through the existing Cloudflare setup.
- All four .env files passed private-mode checks. Certbot renewal dry-run and safe nginx reload hook had already passed in run 39932095761.
- Application restarts were rehearsed sequentially; existing RedNote, RisheStore, OrdersVesta and OrdersCutella containers/services remained active. No whole-host reboot was performed; enabled-on-boot settings were checked.
- shared-vps-resource-check.timer checks every five minutes and emits a journald warning/failure if root disk reaches 90% or MemAvailable drops below 180000 KiB. This is host monitoring, without an external alert destination.
- Last checked reserve after restart: about 3.1 GiB free disk, 730 MiB MemAvailable, 707 MiB swap used. Builds and further recordings change these values.
- Synthetic CamCam QA accounts/devices and small synthetic clips were retained as test records; no existing user/device was removed.

## Exact external blockers

**CamCam admin:** set ADMIN_EMAILS in hsdarestani/cacmcam to the intended administrator's email. No arbitrary user was granted admin access.

**Hamoon:** Actions secret UI inspection confirms only HAMOON_SERVER_HOST, HAMOON_SERVER_PATH, HAMOON_SERVER_PORT, HAMOON_SERVER_SSH_KEY and HAMOON_SERVER_USER exist; no environment secrets are configured. Required integration names include TELEGRAM_BOT_TOKEN, HOST_BOT_TOKEN, HETZNER_API_TOKEN, OS_AUTH_URL, OS_USERNAME, OS_PASSWORD, OS_PROJECT_ID (and the selected OpenStack domain/network settings), AFRACLOUD_API_KEY, AFRACLOUD_SECRET_KEY and the configured Zibal merchant credential. Set them through GitHub Secrets or the private server channel, never chat. Do not replace DB credentials, admin session secrets or the fresh database. Provider authentication and controlled payment/callback/wallet acceptance must precede enabling real financial/provisioning actions.

**BeOnMeet:** calendar OAuth token is absent at /opt/beonmeet/data/google-token.json. Complete calendar consent at https://beonmeet.smarbiz.sbs/auth/google using the intended account (the live browser reached Google's BeOnMEET account chooser without a redirect/client error), and separately sign the recorder's account into its existing Chrome profile. A private local SSH tunnel can reach noVNC without exposing it:

```sh
ssh -p 9011 -L 6081:127.0.0.1:6081 root@143.20.60.195
```

Then use the existing noVNC UI at http://127.0.0.1:6081/vnc.html in the tunnel owner's local browser. Never expose CDP/noVNC publicly. After authentication, run one real test meeting, confirm audio, saved/downloadable file, intended-recipient delivery, restart recovery and available transcription provider. Keep BEONMEET_STAGING_NO_POLL=1, FREE_MEETING_SLOTS=0, PREMIUM_MEETING_SLOTS=0 and AUTOSCALE_ENABLED=false until that acceptance passes; then permit at most one local meeting. No large Whisper model was downloaded or tested on this small VPS.

---

# Historical migration record (superseded where it conflicts with the verified state above)

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

## 2026-10-23 public HTTP(S) release (supersedes earlier DNS/TLS blockers)
**Public domain/DNS/TLS deployment is COMPLETED and independently checked.** Earlier notes saying that public DNS was not cut over, TLS certificates were missing or HTTP returned 503 are historical and no longer describe the live state.

- [Public ACME routing from external runner confirmed](https://github.com/hsdarestani/rednotedownloader/actions/runs/39931296252): an exact token file from the new VPS was returned with status 200 by all four domains. CamCam remains Cloudflare-proxied.
- [All four Let's Encrypt certificates issued](https://github.com/hsdarestani/rednotedownloader/actions/runs/39931491245), expire January 21, 2027.
- [All four new Nginx HTTPS reverse proxies activated](https://github.com/hsdarestani/rednotedownloader/actions/runs/39931797544); existing rishe.store and order site configs preserved. Public port 80 redirects to HTTPS 301 while the dedicated HTTP-01 webroot path remains available for renewal.
- [Independent public HTTPS QA passed](https://github.com/hsdarestani/rednotedownloader/actions/runs/39931904510): all four `https://<domain>/health` return HTTP 200, valid JSON and trusted HTTPS; four HTTP-to-HTTPS redirects returned 301; CamCam / returned 200 HTML, BeOnMeet / returned 200 HTML, BebinKarato / returned 200 JSON (API app), Hamoon payment host / returned 404 JSON **by application design**, while its /health is 200. Use Hamoon /dashboard/login for the dashboard; do not treat root 404 as gateway failure.
- [Auto-renew rehearsal passed for all five certificates](https://github.com/hsdarestani/rednotedownloader/actions/runs/39932095761): four new application certs plus existing rishe.store. A Certbot deploy hook validates and reloads Nginx on actual renewals.
- [Read-only external integration readiness](https://github.com/hsdarestani/rednotedownloader/actions/runs/39932178204): all listed Telegram, Cloudflare AI, Firebase, Google OAuth, Hetzner, OpenStack and Zibal credential **fields remain blank** on the new VPS. No bot polling, real Meet Google authentication, payment capture, or provider provisioning can be called live yet. Do not assume public HTTP health implies working business features.

### HTTPS/public service routing
| Host | Public HTTPS | Backend |
|---|---|---|
| camcam.smarbiz.sbs | Healthy, main HTML | 127.0.0.1:8117 |
| beonmeet.smarbiz.sbs | Healthy, main HTML | 127.0.0.1:8109 |
| bebinkarato.smarbiz.sbs | Healthy API | 127.0.0.1:8106 |
| pay.hamooncloud.ir | Healthy API and payment callback routing, application root 404 by design | 127.0.0.1:8108 |

The current VPS has ~3.2 GB free after certificate issuance, subject to log growth and actual recordings. Existing RedNote, RisheStore and two order bots were validated after public HTTPS release.

**Outstanding:** set valid external secrets on the VPS using a secure channel, authenticate BeOnMeet recorder's Google account, switch on Telegram polling only with valid tokens, and test one real video recording and one provider/payment end-to-end in a non-production test scenario. Separate offsite backups are still not configured. 


## 2026-10-23 credential-rotation follow-up (pending external authorization)

The VPS root password was rotated and the user reports updating GitHub Actions secrets. **No post-rotation SSH Actions run has been confirmed in this conversation**, and no application-provider tokens have been imported to the new server. Do not mark Telegram polling, actual Google Meet recording, AI requests or Zibal payments as working without integration tests.

Two **draft**, unmerged GitHub PRs were prepared safely:
- [RisheStore #1](https://github.com/hsdarestani/rishestore/pull/1): correct SSH port 9011, check actual target IP and take global deployment lock. Remains draft because the current RisheStore deploy includes Caddy and legacy migration behavior that must not disrupt existing public Nginx/data.
- [BebinKarato #1](https://github.com/hsdarestani/bebinkarato/pull/1): loopback-only 8106 compose override, make the standalone Caddy opt-in, point staged deployment at the new SSH port and dedicated `SHARED_VPS_PASS`, lock deployments, switch the smoke test to the current public domain. Remains draft; merging the default branch would automatically trigger the deployment, so release only with active SSH and credential checks.

Current connector's GitHub actions do not offer Secret-value reading/writing or direct workflow dispatch. Attempts in this conversation to start new SSH checks, re-run sensitive jobs, or modify existing secret-import scripts were rejected by safety controls. **No workaround was used**, and none of those rejected operations was executed. BeOnMeet and CamCam's old main workflows remain unsafe to repoint directly to the shared 1-CPU VPS; Hamoon private source staging via separate unprivileged SSH key was previously verified.

Next independently verifiable gates:
1. Confirm GitHub Actions authentication over new root SSH credentials on port 9011 using a vetted, non-destructive workflow.
2. Transfer only the required application's GitHub Secrets using an authorized, masked, least-privilege deployment process; never print, check in, upload as artifacts or relay credentials through chat.
3. Check each app's health and external provider authentication; enable only one Telegram polling consumer per token.
4. Test one actual BeOnMeet meeting through Google sign-in and recorded-file upload/delivery; only then enable polling and concurrency above zero.
5. Keep billing, wallet credits and infrastructure-provisioning writes inactive until provider verification, gateway callbacks and reconciliation can be exercised with a controlled test.
