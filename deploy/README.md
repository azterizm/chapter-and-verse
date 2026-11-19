# Deploy (Slice 1)

Runbook for the first public deployment: one engine on Oracle A1 behind Cloudflare Tunnel, the API Worker, the Pages site, Heroku Postgres and the Modal judge. Steps run in the order below. Each step is marked "By hand" (console or dashboard work) or "Command" (run in a shell).

The Hugging Face Space standby, Worker failover and Terraform come in Slice 4.

## Files

| Path | Purpose |
|---|---|
| `Dockerfile` | Engine image, `linux/arm64` and `linux/amd64`. Build context is the repo root |
| `Dockerfile.dockerignore` | Allow-list for that context. BuildKit reads it in preference to a root `.dockerignore` |
| `entrypoint.sh` | Runs `fetch_artefacts.py`, then `uvicorn chapterverse.app:app` with one worker |
| `fetch_artefacts.py` | Downloads the artefacts into `/data`, checks every SHA-256, resumes, fails closed. Standard library only |
| `compose.yml` | Engine on the A1: read-only root filesystem, 20 GB memory limit, artefacts volume, port bound to 127.0.0.1 |
| `engine.env.example`, `cloudflared.env.example` | Templates for the env files kept outside the repo |
| `worker/` | Cloudflare Worker on `/api/*`, in TypeScript, with vitest tests |
| `pages/_headers` | CSP and security headers for the Pages site |
| `oracle/cloud-init.yaml`, `oracle/setup.sh` | First boot and idempotent host setup for Ubuntu 24.04 ARM64 |
| `modal/serve.py`, `modal/ledger.py` | SGLang judge server and the spend ledger with a hard cap |
| `schema.sql` | Postgres 16 schema: quota ledger, receipts, feedback, sessions |

## Where every secret lives

No secret is in this repository, in cloud-init user-data or in the image.

| Secret | Lives in | Read by |
|---|---|---|
| Cloudflare Tunnel token | `/etc/cloudflared/tunnel-token` on the VM, mode 0600 root | systemd `cloudflared-cv`, passed in as a credential |
| `DATABASE_URL` | `/etc/chapter-and-verse/engine.env`, mode 0640 root:deploy | engine container |
| `GROQ_API_KEY`, `CLOUDFLARE_AI_WORKER_TOKEN`, `MODAL_API_KEY` | same `engine.env` | engine container |
| Turnstile secret key | Worker secret `TURNSTILE_SECRET` | Worker |
| Session signing key | Worker secret `SESSION_SIGNING_KEY` | Worker |
| Access service token (Client ID and Client Secret) | Worker secrets `ACCESS_CLIENT_ID`, `ACCESS_CLIENT_SECRET`. Shown once by Cloudflare when created | Worker, sent on every origin request |
| SGLang API key | Modal Secret `chapter-and-verse-judge-key`, key `JUDGE_API_KEY` (same value as `MODAL_API_KEY`) | Modal container |
| Modal ledger | `~/.local/state/chapter-and-verse/modal_ledger.json` on the operator's machine | `modal/ledger.py` |

The Turnstile site key is public and goes in the web build. The tunnel token, `DATABASE_URL` and the API keys are never pasted into a terminal command line; use the stdin forms below.

## 1. Cloudflare (by hand)

1. Buy the domain at Cloudflare Registrar (plan section 2: `chapterandverse.uk`, else `.app`). The examples below use `chapterandverse.uk`. If the domain differs, change it in `worker/wrangler.toml` (`ALLOWED_ORIGINS`, `TURNSTILE_HOSTNAMES`, `ORIGIN_URL`, `routes`).
2. Turnstile: add a widget for the domain, mode Managed. Keep the site key for the web build and the secret key for step 6.
3. Zero Trust, Networks, Tunnels: create a tunnel of type Cloudflared. Copy its token. Add a public hostname `origin.chapterandverse.uk` with service `http://127.0.0.1:8000`.
4. Protect the origin hostname with Cloudflare Access, so only the Worker can reach it. Without this, anyone who finds the hostname can bypass Turnstile and the rate limits. Required: the Worker returns 503 and forwards nothing until both Access secrets are set.
   1. Zero Trust, Access, Service Auth, Service Tokens: create a token named `chapter-and-verse-worker`, duration 1 year (or non-expiring). Copy the Client ID and Client Secret; the secret is shown only once. Keep them for step 5.
   2. Zero Trust, Access, Applications: add a Self-hosted application for the domain `origin.chapterandverse.uk`. Add one policy with action Service Auth, rule Include, Service Token, `chapter-and-verse-worker`. Add no other policy, so no person or other token can pass.
   3. Check: `curl -s -o /dev/null -w '%{http_code}\n' https://origin.chapterandverse.uk/api/health` returns 403 (or a redirect to the Access login), not 200.
   4. Before the token expires, create a new one, add it to the policy, update the two Worker secrets, then delete the old token.
5. Workers and Pages: create the Pages project `chapter-and-verse` (direct upload).

## 2. Heroku Postgres

1. By hand: create an app in the EU region and add Heroku Postgres Essential-0.
2. Command: apply the schema. It is idempotent, so running it again is safe.

```
heroku pg:psql -a APP < deploy/schema.sql
```

Retention runs in the engine, not the database, because Essential-0 has no `pg_cron`. The purge statements are at the end of `schema.sql`.

| Table | Holds | Kept |
|---|---|---|
| `provider_quota` | Requests, tokens, neurons, GPU seconds, 429s per provider and window | Minute windows 2 days, day windows 90 days, month windows 13 months |
| `receipts` | Answer Fact Object (`fact`), keyed by `answer_id`. A CHECK refuses a `query` field | 180 days |
| `feedback` | Verdict `up` or `down` and an optional note of at most 1000 characters | Note 90 days, row 180 days |
| `sessions` | Memory tiers in `state` | Deleted at `expires_at` |

## 3. Modal judge

1. By hand: in the Modal dashboard, Usage and Billing, set the workspace budget to $30. This is the hard cap for endpoint traffic. Confirm the workspace is on Starter.
2. Command: create the Modal Secret. `modal secret create` reads `KEY=VALUE` pairs from its arguments, so generate the key in a subshell rather than typing it:

```
KEY=$(openssl rand -hex 32)
uv run --with modal==1.2.6 modal secret create chapter-and-verse-judge-key JUDGE_API_KEY="$KEY"
echo "$KEY"   # copy into engine.env as MODAL_API_KEY, then clear the terminal
```

3. Command: fill the weights volume. This runs once on CPU, about 17 GB from Hugging Face at the pinned revision, and is costed in the ledger before it starts.

```
uv run --with modal==1.2.6 modal run deploy/modal/serve.py::fill_weights
```

4. Command: deploy the endpoint. The URL it prints for `serve` is `MODAL_URL`.

```
uv run --with modal==1.2.6 modal deploy deploy/modal/serve.py
```

5. Command: fetch the serialised logit mask once, for the engine's `SGLangBackend(..., processor=...)`. This runs on CPU in the server image, which has torch and sglang, so the engine host needs neither.

```
uv run --with modal==1.2.6 modal run deploy/modal/serve.py::get_mask_processor --out mask_processor.txt
```

Settings in `serve.py`:

- One L40S, `max_containers=1`, `scaledown_window=120`, `startup_timeout=1800`.
- SGLang 0.5.21, model `Vishva007/Qwen3.8-27B-W4A16-AutoRound-GPTQ` at revision `1b9d56b428e9f841d7ad566f87660b5f47553c97`.
- The flags are the stop 78 measured flags (`--mem-fraction-static 0.85 --reasoning-parser qwen3 --context-length 65536`), plus `--enable-custom-logit-processor`. That flag takes effect only for a request that carries `custom_logit_processor` (the generator's token ban fallback under `steering="inject"`). Judge requests carry none, so the measured judge path is unchanged.
- The server image adds `legal-rag-verifier==0.5.0`, a pure Python package with no dependencies. The serialised mask refers to its class by module path, so the server needs the package to load it.
- Weights are read offline (`HF_HUB_OFFLINE=1`). The server checks for the snapshot before it starts SGLang, so a missing volume fails in seconds, not after a GPU cold start.

Names can be changed with `CV_MODAL_APP`, `CV_WEIGHTS_VOLUME` and `CV_MODAL_SECRET`, set in the shell that runs `modal`. They are copied into the image environment so the container sees the same values.

Cost: a cold start is about 406 s of L40S, about $0.25 with overhead, plus 120 s idle before scale-down. Operator runs go through the ledger:

```
python deploy/modal/ledger.py status
python deploy/modal/ledger.py check L40S 1800      # exit 1 when the month would pass the cap
python deploy/modal/ledger.py record "smoke" L40S 900 "20 questions"
```

## 4. Oracle VM

1. By hand: sign up for Oracle Cloud Free Tier. The home region cannot be changed later. Choose by A1 capacity near the UK: London, else Frankfurt or Amsterdam. Never upgrade the account to Pay As You Go.
2. By hand: create the instance.

| Field | Value |
|---|---|
| Image | Canonical Ubuntu 24.04 (aarch64) |
| Shape | VM.Standard.A1.Flex, 4 OCPU, 24 GB |
| Boot volume | 100 GB or more (Always Free allows 200 GB in total) |
| Initialization script | `deploy/oracle/cloud-init.yaml`, with `OWNER` replaced by the GitHub owner |

3. By hand: OCI security list for the subnet.

| Direction | Rule |
|---|---|
| Ingress | Delete the default `0.0.0.0/0` TCP 22 rule. Keep only ICMP type 3 code 4 (path MTU) and ICMP type 3 from the VCN CIDR |
| Egress | `0.0.0.0/0`, all protocols (the tunnel, Docker pulls, Hugging Face, providers) |

SSH stays closed to the internet. The only ways onto the VM are an OCI Bastion session and the OCI serial console. The tunnel is outbound only, so no inbound port is open. ufw denies all incoming traffic too, except TCP 22 from `SSH_ALLOW_FROM` (default `10.0.0.0/16`, the VCN) for OCI Bastion sessions. The serial console also works. If the VCN uses another CIDR, rerun `setup.sh` with `SSH_ALLOW_FROM` set to it.

4. Command, on the VM through Bastion or the serial console: add the tunnel token. Paste it at the empty prompt, then press Enter.

```
sudo bash /opt/chapter-and-verse/deploy/oracle/setup.sh --tunnel-token-stdin
```

5. Command: fill in the engine env file, then rerun setup, which builds the arm64 image on the VM and starts the engine.

```
sudo nano /etc/chapter-and-verse/engine.env
sudo REPO_URL=https://github.com/OWNER/chapter-and-verse.git bash /opt/chapter-and-verse/deploy/oracle/setup.sh
sudo -iu deploy docker compose -f /opt/chapter-and-verse/deploy/compose.yml logs -f engine
```

The first start downloads about 4 GB of artefacts into the `chapter-and-verse-artefacts` volume. Later starts verify the files (a few seconds) and skip the download. A checksum mismatch stops the container before uvicorn starts.

What `setup.sh` does, in order:

1. Base packages, then unattended-upgrades for security updates, with an automatic reboot at 04:30 UTC when one is needed.
2. A 4 GB swap file with `vm.swappiness = 10`, and journald capped at 500 MB and 1 month.
3. The firewall. Oracle's Ubuntu image ships iptables INPUT and FORWARD REJECT rules that clash with ufw and Docker. The script keeps only Oracle's `InstanceServices` egress chain (metadata and boot-volume services) and keeps the original as `/etc/iptables/rules.v4.oracle-orig`.
4. Docker Engine and the compose plugin from download.docker.com, with log rotation and `live-restore` in `daemon.json`.
5. `cloudflared` from pkg.cloudflare.com, run as `cloudflared-cv.service`. The service runs as a dynamic unprivileged user, and the token reaches it through `LoadCredential`, never through the command line.
6. The `deploy` user (no password, no sudo, member of `docker`), the checkout in `/opt/chapter-and-verse`, and `/etc/chapter-and-verse/engine.env` created from the template.
7. `docker compose up -d --build`, once `engine.env` no longer holds template values.

Membership of the `docker` group is root-equivalent on the host. The `deploy` user can only be reached through root (`sudo -iu deploy`).

### Idle reclamation

Oracle reclaims an Always Free A1 instance as idle when, over 7 days, all three of these hold: 95th percentile CPU under 20%, network under 20%, memory under 20%. The instance stays safe if any one of them is above 20%. Memory is the one this deployment keeps up: the router index, the concepts index and the text store (about 4 GB) are held resident by the engine, and Slice 3 adds the dense vectors. 4 GB of 24 GB is about 17%, which is under the line. After the first week, read the instance's memory utilisation metric in the OCI console. If it is under 25%, raise the resident set before the 7-day window can close.

## 5. Worker (Command)

From `deploy/worker/`:

```
npm ci
npx wrangler login
npx wrangler secret put TURNSTILE_SECRET
openssl rand -base64 48 | npx wrangler secret put SESSION_SIGNING_KEY
npx wrangler secret put ACCESS_CLIENT_ID        # Client ID from step 1.4
npx wrangler secret put ACCESS_CLIENT_SECRET    # Client Secret from step 1.4
npx wrangler deploy
```

Uncomment `routes` in `wrangler.toml` first. `workers_dev = false`, so the Worker has no `*.workers.dev` URL.

What the Worker does on each `/api/*` request, in order:

| Check | Rule |
|---|---|
| CORS | Allows only `ALLOWED_ORIGINS`. A request with any other `Origin` gets 403. OPTIONS is answered at the edge |
| Methods | GET, HEAD, POST, OPTIONS. Anything else gets 405 |
| Rate limit | Workers Rate Limiting binding, per IP (IPv6 by /64), counted per Cloudflare location: 60 requests per 60 s for all requests (`API_LIMITER`), 10 per 60 s for POST (`ASK_LIMITER`). 429 with `Retry-After: 60` |
| Rate limit fallback | If a binding is missing or its call fails, an in-isolate token bucket with the same numbers |
| Body | POST must be `application/json` and at most 12,288 bytes (`MAX_BODY_BYTES`), which leaves room for the JSON envelope and escaping. Otherwise 415 or 413. The engine enforces `MAX_QUERY_BYTES=4096` on the query itself |
| Session | A POST needs a valid `X-Session-Token`. On `POST /api/ask` without one, the `X-Turnstile-Token` header is checked with siteverify (hostname and action `ask` must match), and a new token comes back in `X-Session-Token`. Missing check: 401 `turnstile_required`. Failed check: 403 |
| Access | Every origin request (proxied and cron) carries `CF-Access-Client-Id` and `CF-Access-Client-Secret` from the Worker secrets. Client-sent copies are replaced. `CF-Access-*` headers from the origin are dropped from responses, and the values are never logged. Either secret unset: 503 |
| Forward | To `ORIGIN_URL` with a 4 s timeout for the response headers. After that the body streams through unbuffered (SSE gets `Cache-Control: no-cache, no-transform`). Timeout: 504. Unreachable: 502 |
| Headers | Origin `Set-Cookie`, `Server` and CORS headers are dropped. CSP `default-src 'none'`, `nosniff`, `no-referrer`, HSTS and `X-Frame-Options: DENY` are added |
| Cron | `17 */3 * * *`: `GET /api/health` on every origin, logged to Workers Logs |

Whether the Rate Limiting binding is included on the Workers Free plan is not stated in Cloudflare's docs, and a community answer says it is. The Worker works either way because of the fallback.

Contract for the web page:

- The first question sends `X-Turnstile-Token: <widget token>` and stores the `X-Session-Token` response header in memory.
- Every later POST sends `X-Session-Token`. On a 401, the page runs the widget again.
- The Worker forwards the session id to the engine as `X-CV-Session-Id`. That id is `sessions.session_id`.
- Sessions last 2 hours (`SESSION_TTL_SECONDS`).
- The request body is limited to 12,288 bytes at the edge. The engine limits the query itself to 4096 bytes (`MAX_QUERY_BYTES`) and refuses a longer one.

Slice 4 adds the Hugging Face standby in `worker/src/origins.ts` only. `proxy.ts` already tries origins in order on timeout, connection failure or 502, 503, 504 and 530.

Checks, from `deploy/worker/`:

```
npx tsc --noEmit
npx vitest run
npx wrangler deploy --dry-run --outdir dist
```

## 6. Pages (Command)

Copy `deploy/pages/_headers` into the build output, then deploy:

```
cp deploy/pages/_headers web/dist/_headers
npx wrangler pages deploy web/dist --project-name chapter-and-verse
```

The CSP allows scripts from `'self'` and `https://challenges.cloudflare.com` only, with no inline or eval scripts. Frames are allowed from Turnstile only, and `connect-src 'self'` covers the API on the same origin. Inline styles are allowed, because the Turnstile widget and React set style attributes. If the API is ever served from another host, add that origin to `connect-src`.

## 7. Smoke checks (Command)

```
curl -s https://chapterandverse.uk/api/health
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Content-Type: application/json' \
  -d '{"query":"test"}' https://chapterandverse.uk/api/ask        # 401 without Turnstile
sudo -iu deploy docker compose -f /opt/chapter-and-verse/deploy/compose.yml ps
systemctl status cloudflared-cv
```

## Engine image

```
docker buildx build -f deploy/Dockerfile --platform linux/arm64,linux/amd64 -t IMAGE:TAG .
```

- Base `python:3.12-slim-bookworm`, with uv 0.13.6 from `ghcr.io/astral-sh/uv`.
- `uv sync --locked --no-dev`, non-editable, so the runtime stage holds only `/opt/venv`.
- Runs as uid 1000. `PORT` defaults to 8000; the Hugging Face Space sets 7860.
- `HEALTHCHECK` on `/api/health` with a 900 s start period, which covers the first download.
- No artefacts are in the image.

Size, estimated from the wheel sizes in `uv.lock` because Docker was not available where these files were written: 30 runtime packages, 20.3 MB of arm64 wheels, about 60 to 70 MB once installed with bytecode. With the base image the total is about 200 MB uncompressed, about 70 MB compressed. Measure with `docker image ls` after the first build.

Artefact environment variables (`fetch_artefacts.py`):

| Variable | Meaning |
|---|---|
| `HF_DATASET` | `owner/name` of the public dataset. Unset means verify a mounted `/data` only |
| `HF_REVISION` | 40-hex dataset commit. A branch name is refused unless `ARTEFACTS_ALLOW_BRANCH=1` |
| `MANIFEST_SHA256` | Pin of `corpus-manifest.json` itself. Recommended |
| `MANIFEST_NAME` | Default `corpus-manifest.json` |
| `EXTRA_MANIFESTS` | Default `index/index-manifest.json,concepts/concepts-manifest.json`. Empty for none |
| `ARTEFACTS_WORKERS` | Parallel downloads, default 4 |

Dataset layout, which is also the layout of `/data`:

| Path | Contents | Engine variable |
|---|---|---|
| `corpus-manifest.json` and the files it lists | Text store, as `build/publish_dataset.py` uploads it (files at the root, `files` entries with `size` and `sha256`) | `STORE_DIR=/data` |
| `index/index-manifest.json` and its files | Router index | `LEGAL_DATA_ROOT=/data` |
| `concepts/concepts-manifest.json` and its files | Concepts index | `LEGAL_DATA_ROOT=/data` |

`MANIFEST_SHA256` pins the store manifest. The router manifests are fetched at the pinned dataset commit, their hashes are recorded in `/data/.artefacts-source.json`, and every file they list is checked. `build/publish_dataset.py` uploads the store only, so `index/` and `concepts/` must be uploaded to the same dataset commit by another step.

## Rollback

| Part | How |
|---|---|
| Engine | Each build is tagged by commit. On the VM: `sudo REPO_REF=<previous commit or tag> REPO_URL=... bash /opt/chapter-and-verse/deploy/oracle/setup.sh`. That checks out the old commit and rebuilds. The image cache makes this quick when that commit was built before |
| Engine, without rebuild | `docker tag` each image before replacing it (for example `chapter-and-verse-engine:<sha>`), then `ENGINE_IMAGE=chapter-and-verse-engine:<sha> docker compose -f deploy/compose.yml up -d` as `deploy` |
| Artefacts | Set `HF_REVISION` (and `MANIFEST_SHA256`) in `engine.env` back to the previous dataset commit and restart the engine. Files are fetched again where they differ |
| Worker | `npx wrangler rollback` (choose the version), or `npx wrangler deployments list` then `npx wrangler rollback <version-id>` |
| Pages | Dashboard, Workers and Pages, the project, Deployments: "Rollback to this deployment" |
| Modal | `modal app rollback chapter-and-verse-judge`, or `modal app stop chapter-and-verse-judge` to take Modal out (the engine drops it from the rotation when it fails) |
| Schema | Additive only, with `IF NOT EXISTS`. Nothing is dropped by `schema.sql` |
| Tunnel | `sudo systemctl stop cloudflared-cv` takes the origin offline at once. The Worker then returns 502 |
