# setup.sh netfilter fix: requirements

First boot on the Oracle A1 VM (Ubuntu 24.04 aarch64, image 2026.11.05) failed in `deploy/oracle/setup.sh` at line 149: `systemctl restart netfilter-persistent` gives "Unit netfilter-persistent.service not found", although `systemctl is-enabled netfilter-persistent` returns 0 ("not a native service, redirecting to systemd-sysv-install ... enabled"). Packages present: `iptables-persistent 1.0.20`, `netfilter-persistent 1.0.20`. The script stopped there (`set -e`), so Docker, cloudflared and the rest were not installed. `/etc/iptables/rules.v4` was already rewritten with the marker, so a rerun skips the whole block and leaves the old REJECT rules live in the kernel.

- R1 Load the rewritten rules with the tool itself: `netfilter-persistent reload` when that command exists, else `iptables-restore < /etc/iptables/rules.v4`. No `systemctl restart netfilter-persistent`.
- R2 The reload SHALL run whenever the rules file carries the marker, not only on the run that rewrote it, so a rerun after a failed first boot repairs the live rules. It is idempotent.
- R3 After the reload, the script SHALL check that the live INPUT chain has no REJECT rule from the Oracle defaults (`iptables -S INPUT | grep -q REJECT` must be false) and fail with a clear message if it does.
- R4 The docker restart that follows stays, unchanged.
- R5 `bash -n deploy/oracle/setup.sh` passes. Change nothing else in the file.
