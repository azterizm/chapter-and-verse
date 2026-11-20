# Artefacts in RAM: requirements

Oracle reclaims an Always Free A1 instance when, over 7 days, CPU, network and memory (95th percentile) all stay under 20%. The A1 has 24 GB, so the engine must keep at least 4.8 GB of non-reclaimable memory in use. The resident text store (`STORE_RESIDENT=1`, commit b4fc295) uses about 3.97 GB. The router `index/` (0.38 GB) and `concepts/` (0.57 GB) are memory-mapped from disk, and page cache does not count as used memory.

- R1 `deploy/compose.yml` SHALL give the engine a tmpfs mount (for example `/ram`, size 3g, mode 0700, owned by the container user) for artefacts.
- R2 At start, `deploy/entrypoint.sh` SHALL copy the verified artefacts (after `fetch_artefacts.py` has verified them) from the volume into the tmpfs, verify each copied file's SHA-256 against its manifest again, and point `STORE_DIR`, `ROUTER_INDEX` and `CONCEPTS_INDEX` at the tmpfs copies. If the copy or the check fails, it SHALL fall back to the volume paths and log one warning (never start with unverified data).
- R3 `STORE_RESIDENT=1` and `STORE_RESIDENT_MAX_BYTES=8589934592` SHALL be set for the engine in `compose.yml`'s environment (not in engine.env, which another worker edits).
- R4 The engine's memory limit in `compose.yml` SHALL leave room: at least 10g.
- R5 `deploy/README.md` SHALL get a short "Idle reclamation" note: the expected resident memory (about 5.8 GB, about 24% of 24 GB), how to read it on the VM (`free -m`, used column; and the OCI Monitoring MemoryUtilization metric), and that it should be checked after the first week.
- R6 Checks: `sh -n deploy/entrypoint.sh` (or `bash -n` if it is bash), compose file validated with `uv run python -c` loading YAML (PyYAML may not be installed; if not, check by reading). No docker on this Mac; do not try to run containers.
