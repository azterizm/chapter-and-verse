#!/usr/bin/env bash
# Chapter & Verse host setup for an Oracle Always Free A1 VM, Ubuntu 24.04 ARM64.
# Idempotent: safe to run again after any change or failure. Run as root:
#
#   sudo REPO_URL=https://github.com/OWNER/chapter-and-verse.git REPO_REF=main \
#     bash deploy/oracle/setup.sh
#
# Tunnel token (never in user-data, the repo or shell history): either paste it once with
#   sudo bash deploy/oracle/setup.sh --tunnel-token-stdin
# or write /etc/cloudflared/tunnel-token yourself (mode 0600, owner root).
#
# What it does, in order:
#   1. base packages, unattended-upgrades with automatic reboot at 04:30 UTC
#   2. swap file and journald size limits
#   3. firewall: ufw denies all incoming (SSH only from SSH_ALLOW_FROM, default the VCN range)
#   4. Docker Engine and the compose plugin (download.docker.com)
#   5. cloudflared (pkg.cloudflare.com) as the systemd service cloudflared-cv
#   6. the non-root deploy user, the repository checkout, /etc/chapter-and-verse/engine.env
#   7. docker compose up, once engine.env has been filled in
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/OWNER/chapter-and-verse.git}"
REPO_REF="${REPO_REF:-main}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"
APP_DIR="${APP_DIR:-/opt/chapter-and-verse}"
ENV_DIR="${ENV_DIR:-/etc/chapter-and-verse}"
SWAP_SIZE_GB="${SWAP_SIZE_GB:-4}"
SSH_ALLOW_FROM="${SSH_ALLOW_FROM:-10.0.0.0/16}"
TUNNEL_TOKEN_FILE=/etc/cloudflared/tunnel-token
TUNNEL_UNIT=cloudflared-cv
MARKER="# chapter-and-verse"

export DEBIAN_FRONTEND=noninteractive

log() { printf 'setup: %s\n' "$*"; }
warn() { printf 'setup: WARNING: %s\n' "$*" >&2; }
die() { printf 'setup: ERROR: %s\n' "$*" >&2; exit 1; }
trap 'die "failed at line ${LINENO}"' ERR

# Write $2 to file $1 only when the content differs. Returns 0 when it changed.
write_if_changed() {
  local path="$1" content="$2" mode="${3:-0644}"
  if [[ -f "$path" ]] && [[ "$(cat "$path")" == "$content" ]]; then
    return 1
  fi
  install -d -m 0755 "$(dirname "$path")"
  printf '%s\n' "$content" > "$path.tmp"
  chmod "$mode" "$path.tmp"
  mv "$path.tmp" "$path"
  return 0
}

preflight() {
  [[ $EUID -eq 0 ]] || die "run as root (sudo)"
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die "Ubuntu required, found ${ID:-unknown}"
  [[ "${VERSION_ID:-}" == "24.04" ]] || warn "tested on Ubuntu 24.04, found ${VERSION_ID:-unknown}"
  [[ "$(dpkg --print-architecture)" == "arm64" ]] || warn "expected arm64 (Oracle A1)"
}

read_tunnel_token() {
  local token
  IFS= read -r token || true
  token="${token//[$'\r\n\t ']/}"
  [[ -n "$token" ]] || die "no tunnel token on stdin"
  [[ "$token" =~ ^[A-Za-z0-9+/=_-]+$ ]] || die "tunnel token has unexpected characters"
  install -d -m 0700 -o root -g root /etc/cloudflared
  (umask 077 && printf '%s\n' "$token" > "$TUNNEL_TOKEN_FILE.tmp")
  chown root:root "$TUNNEL_TOKEN_FILE.tmp"
  mv "$TUNNEL_TOKEN_FILE.tmp" "$TUNNEL_TOKEN_FILE"
  log "tunnel token written to $TUNNEL_TOKEN_FILE (0600 root)"
}

install_base() {
  log "base packages"
  apt-get update -q
  apt-get install -y -q ca-certificates curl gnupg git ufw unattended-upgrades apt-listchanges
}

configure_unattended_upgrades() {
  log "unattended-upgrades"
  write_if_changed /etc/apt/apt.conf.d/20auto-upgrades \
'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";' || true
  # Security updates only (Ubuntu default origins). Docker and cloudflared are upgraded by hand.
  write_if_changed /etc/apt/apt.conf.d/52chapter-and-verse \
"$MARKER
Unattended-Upgrade::Remove-Unused-Dependencies \"true\";
Unattended-Upgrade::Automatic-Reboot \"true\";
Unattended-Upgrade::Automatic-Reboot-Time \"04:30\";" || true
  systemctl enable --now unattended-upgrades.service >/dev/null
}

configure_swap() {
  log "swap ${SWAP_SIZE_GB} GB"
  if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
    if [[ ! -f /swapfile ]]; then
      fallocate -l "${SWAP_SIZE_GB}G" /swapfile
      chmod 0600 /swapfile
      mkswap /swapfile >/dev/null
    fi
    swapon /swapfile
  fi
  grep -qE '^/swapfile[[:space:]]' /etc/fstab || printf '/swapfile none swap sw 0 0\n' >> /etc/fstab
  # Prefer dropping page cache over swapping the engine's resident indexes.
  if write_if_changed /etc/sysctl.d/90-chapter-and-verse.conf \
"$MARKER
vm.swappiness = 10"; then
    sysctl --system >/dev/null
  fi
}

configure_journald() {
  log "journald limits"
  if write_if_changed /etc/systemd/journald.conf.d/90-chapter-and-verse.conf \
"$MARKER
[Journal]
SystemMaxUse=500M
SystemKeepFree=2G
MaxRetentionSec=1month"; then
    systemctl restart systemd-journald
  fi
}

configure_firewall() {
  log "firewall"
  # Oracle's Ubuntu images load /etc/iptables/rules.v4 via netfilter-persistent. Its INPUT and
  # FORWARD REJECT rules clash with ufw and break Docker networking. Keep only its
  # InstanceServices egress chain (which guards the 169.254.0.0/16 metadata and boot-volume
  # services) and leave INPUT to ufw. The original is kept as rules.v4.oracle-orig.
  local rules=/etc/iptables/rules.v4
  if [[ -f "$rules" ]] && ! grep -q "^$MARKER" "$rules"; then
    cp -a "$rules" "$rules.oracle-orig"
    {
      echo "$MARKER: Oracle InstanceServices egress rules only; INPUT is managed by ufw."
      echo "*filter"
      echo ":INPUT ACCEPT [0:0]"
      echo ":FORWARD ACCEPT [0:0]"
      echo ":OUTPUT ACCEPT [0:0]"
      grep -E '^:InstanceServices ' "$rules.oracle-orig" || true
      grep -E '^-A OUTPUT .*-j InstanceServices' "$rules.oracle-orig" || true
      grep -E '^-A InstanceServices ' "$rules.oracle-orig" || true
      echo "COMMIT"
    } > "$rules.tmp"
    mv "$rules.tmp" "$rules"
    if systemctl is-enabled --quiet netfilter-persistent 2>/dev/null; then
      systemctl restart netfilter-persistent
    fi
    if systemctl is-active --quiet docker 2>/dev/null; then
      systemctl restart docker
    fi
  fi

  ufw default deny incoming >/dev/null
  ufw default allow outgoing >/dev/null
  if [[ -n "$SSH_ALLOW_FROM" ]]; then
    # Private range only, for OCI Bastion sessions. Public SSH is closed here and in the OCI
    # security list; the serial console works regardless.
    ufw allow from "$SSH_ALLOW_FROM" to any port 22 proto tcp comment 'ssh from VCN' >/dev/null
  fi
  ufw --force enable >/dev/null
  # Published container ports bypass ufw; compose.yml binds the engine to 127.0.0.1 only.
}

install_docker() {
  log "Docker Engine and compose plugin"
  local codename arch
  codename="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  arch="$(dpkg --print-architecture)"
  install -d -m 0755 /etc/apt/keyrings
  if [[ ! -s /etc/apt/keyrings/docker.asc ]]; then
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod 0644 /etc/apt/keyrings/docker.asc
  fi
  if write_if_changed /etc/apt/sources.list.d/docker.list \
"deb [arch=${arch} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${codename} stable"; then
    apt-get update -q
  fi
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  if write_if_changed /etc/docker/daemon.json \
'{
  "log-driver": "local",
  "log-opts": { "max-size": "10m", "max-file": "5" },
  "live-restore": true
}'; then
    systemctl restart docker
  fi
  systemctl enable --now docker.service containerd.service >/dev/null
}

install_cloudflared() {
  log "cloudflared"
  if [[ ! -s /usr/share/keyrings/cloudflare-main.gpg ]]; then
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg
    chmod 0644 /usr/share/keyrings/cloudflare-main.gpg
  fi
  if write_if_changed /etc/apt/sources.list.d/cloudflared.list \
"deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main"; then
    apt-get update -q
  fi
  apt-get install -y -q cloudflared

  # The token is read by systemd from a root-only file and handed to an unprivileged dynamic
  # user as a credential; it never appears in the unit file, the process list or the journal.
  local changed=1
  write_if_changed "/etc/systemd/system/${TUNNEL_UNIT}.service" \
"$MARKER
[Unit]
Description=Cloudflare Tunnel for Chapter & Verse
After=network-online.target
Wants=network-online.target
ConditionPathExists=${TUNNEL_TOKEN_FILE}

[Service]
Type=simple
DynamicUser=yes
LoadCredential=tunnel-token:${TUNNEL_TOKEN_FILE}
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate --metrics 127.0.0.1:20241 run --token-file %d/tunnel-token
Restart=on-failure
RestartSec=5s
TimeoutStartSec=0
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
CapabilityBoundingSet=
MemoryMax=512M

[Install]
WantedBy=multi-user.target" || changed=0
  if ((changed)); then
    systemctl daemon-reload
  fi

  install -d -m 0700 -o root -g root /etc/cloudflared
  if [[ -s "$TUNNEL_TOKEN_FILE" ]]; then
    chmod 0600 "$TUNNEL_TOKEN_FILE"
    chown root:root "$TUNNEL_TOKEN_FILE"
    systemctl enable "${TUNNEL_UNIT}.service" >/dev/null
    if ((changed)); then systemctl restart "${TUNNEL_UNIT}.service"; else systemctl start "${TUNNEL_UNIT}.service"; fi
  else
    warn "no tunnel token at ${TUNNEL_TOKEN_FILE}; rerun with --tunnel-token-stdin"
  fi
}

create_deploy_user() {
  log "deploy user ${DEPLOY_USER}"
  if ! id -u "$DEPLOY_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --comment "Chapter & Verse deploy" "$DEPLOY_USER"
    passwd --lock "$DEPLOY_USER" >/dev/null
  fi
  # Membership of the docker group is root-equivalent on this host; the user has no password
  # and no sudo, and is reached only through root (sudo -iu deploy).
  usermod -aG docker "$DEPLOY_USER"
}

checkout_repo() {
  log "checkout ${REPO_URL} at ${REPO_REF}"
  [[ "$REPO_URL" != *"/OWNER/"* ]] || die "set REPO_URL to the public repository"
  if [[ ! -d "$APP_DIR/.git" ]]; then
    install -d -m 0755 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR"
    sudo -u "$DEPLOY_USER" git clone --quiet "$REPO_URL" "$APP_DIR"
  fi
  sudo -u "$DEPLOY_USER" git -C "$APP_DIR" fetch --quiet --tags origin "$REPO_REF"
  sudo -u "$DEPLOY_USER" git -C "$APP_DIR" -c advice.detachedHead=false checkout --quiet --detach FETCH_HEAD
  log "at commit $(sudo -u "$DEPLOY_USER" git -C "$APP_DIR" rev-parse --short HEAD)"
}

prepare_env() {
  install -d -m 0750 -o root -g "$DEPLOY_USER" "$ENV_DIR"
  if [[ ! -f "$ENV_DIR/engine.env" ]]; then
    install -m 0640 -o root -g "$DEPLOY_USER" "$APP_DIR/deploy/engine.env.example" "$ENV_DIR/engine.env"
    warn "created $ENV_DIR/engine.env from the template; fill it in, then rerun"
  fi
  chmod 0640 "$ENV_DIR/engine.env"
  chown root:"$DEPLOY_USER" "$ENV_DIR/engine.env"
}

start_stack() {
  if grep -qE '^HF_DATASET=OWNER/|^HF_REVISION=0{40}$' "$ENV_DIR/engine.env"; then
    warn "engine.env still holds template values; not starting the engine"
    return 0
  fi
  log "docker compose up (builds the arm64 image on this host)"
  sudo -u "$DEPLOY_USER" -H env CV_ENV_DIR="$ENV_DIR" \
    docker compose -f "$APP_DIR/deploy/compose.yml" up -d --build --remove-orphans
  sudo -u "$DEPLOY_USER" -H env CV_ENV_DIR="$ENV_DIR" \
    docker compose -f "$APP_DIR/deploy/compose.yml" ps
  log "first start downloads about 4 GB of artefacts; follow with:"
  log "  sudo -iu ${DEPLOY_USER} docker compose -f ${APP_DIR}/deploy/compose.yml logs -f engine"
}

main() {
  cd /  # sudo -u keeps the working directory; /root is unreadable to the deploy user
  preflight
  if [[ "${1:-}" == "--tunnel-token-stdin" ]]; then
    read_tunnel_token
  elif [[ -n "${1:-}" ]]; then
    die "unknown argument: $1"
  fi
  install_base
  configure_unattended_upgrades
  configure_swap
  configure_journald
  configure_firewall
  install_docker
  install_cloudflared
  create_deploy_user
  checkout_repo
  prepare_env
  start_stack
  log "done"
}

main "$@"
