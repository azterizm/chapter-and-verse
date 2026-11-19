# Network-aware limits: requirements

Decision (20 Nov 2026, privacy): no browser or device fingerprinting. Abuse limits use network data only. Cloudflare gives the Worker `request.cf.asn` and `request.cf.asOrganization` for each request. VPN and proxy exits mostly sit on hosting and datacenter networks; rotating IPs inside one provider must not reset the limits.

- R1 The Worker SHALL classify each request's network as `hosting` or `other`. `hosting` = the ASN is in a bundled list of hosting, cloud, datacenter and VPN providers (a static file `deploy/worker/src/hostingAsns.ts` with at least the major clouds and VPN operators, each entry with ASN and name and a comment naming the public source used), or `asOrganization` matches a short keyword list (hosting, cloud, datacenter, data center, server, vps, vpn, proxy). Document both lists and their source in the file header.
- R2 Limits for `POST /api/ask`, in addition to the existing per-IP limit:
  - per network (keyed by ASN) for `hosting`: `HOSTING_ASN_PER_MINUTE` (default 6) using the Rate Limiting binding, and per IP for `hosting`: `HOSTING_IP_PER_MINUTE` (default 2);
  - per network for `other`: `OTHER_ASN_PER_MINUTE` (default 120), so one large home ISP is not blocked as a whole.
  Over a limit: HTTP 429 with `Retry-After`, before forwarding.
- R3 The Worker SHALL forward the class and ASN to the origin in headers it sets itself (`X-CV-Net-Class`, `X-CV-ASN`), overwriting any client copy, only on origin requests that carry the Access headers. Nothing about the person is forwarded.
- R4 Nothing is stored or logged per person. Logs may count requests per class.
- R5 Config: new vars in `wrangler.toml` with the defaults above; new Rate Limiting bindings declared there.
- R6 Tests (vitest): classification by ASN list and by keyword; limits per class; header overwrite; no header when Access headers are missing. `npx tsc --noEmit`, `npx vitest run`, `npx wrangler deploy --dry-run` pass.
