#!/usr/bin/env bash
# Create a KVM/libvirt test VM (shows up in Virtual Machine Manager) running
# the dev test Home Assistant with the simulated house.
#
# Debian 13 cloud image + cloud-init: installs Docker, clones this repo,
# starts dev/docker-compose.yaml and (unless --no-setup) does onboarding and
# creates the heating system with all simulated rooms.
#
#   ./create-vm.sh                       # VM "cmt-test", branch of this checkout
#   ./create-vm.sh --branch main --name cmt-main
#   ./create-vm.sh --delete              # remove the VM and its disk
#
# Needs: virt-install, virsh, qemu-img, curl (Fedora: sudo dnf install virt-install qemu-img)
set -euo pipefail

NAME="cmt-test"
BRANCH=""
REPO="https://github.com/BarBaar44/thermostat"
MEMORY=2048
VCPUS=2
DISK_GB=16
USERNAME="test"
PASSWORD="testtest"
SSH_KEY=""
SETUP=1
DELETE=0
CONNECT="qemu:///system"
IMAGES="${CMT_IMAGES:-/var/lib/libvirt/images}"  # override with CMT_IMAGES
BASE_URL="https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2"
BASE_IMG="$IMAGES/debian-13-genericcloud-amd64.qcow2"

usage() {
  sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
  cat <<EOF

Options:
  --name NAME        VM name (default $NAME)
  --branch BRANCH    repo branch to run (default: branch of this checkout, else main)
  --memory MB        default $MEMORY
  --vcpus N          default $VCPUS
  --disk GB          default $DISK_GB
  --username NAME    HA owner and VM login user (default $USERNAME)
  --password PASS    HA owner and VM console password (default $PASSWORD)
  --ssh-key FILE     public key for the VM user (default ~/.ssh/id_ed25519.pub or id_rsa.pub)
  --no-setup         only start HA, leave onboarding and the heating system to you
  --delete           delete VM NAME and its disk
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --memory) MEMORY="$2"; shift 2 ;;
    --vcpus) VCPUS="$2"; shift 2 ;;
    --disk) DISK_GB="$2"; shift 2 ;;
    --username) USERNAME="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --ssh-key) SSH_KEY="$2"; shift 2 ;;
    --no-setup) SETUP=0; shift ;;
    --delete) DELETE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

DISK="$IMAGES/$NAME.qcow2"
virsh_() { virsh --connect "$CONNECT" "$@"; }

if [[ $DELETE -eq 1 ]]; then
  if virsh_ dominfo "$NAME" >/dev/null 2>&1; then
    virsh_ destroy "$NAME" >/dev/null 2>&1 || true
    virsh_ undefine "$NAME" --nvram >/dev/null 2>&1 || virsh_ undefine "$NAME" >/dev/null
    echo "Removed VM $NAME"
  fi
  if sudo test -f "$DISK"; then sudo rm -f "$DISK"; echo "Removed $DISK"; fi
  exit 0
fi

for cmd in virt-install virsh qemu-img curl; do
  command -v "$cmd" >/dev/null || { echo "Missing $cmd (Fedora: sudo dnf install virt-install qemu-img curl)" >&2; exit 1; }
done
if [[ ! "$USERNAME" =~ ^[a-z][a-z0-9_-]*$ ]]; then
  echo "--username must be a lowercase login name" >&2; exit 2
fi
if [[ ${#PASSWORD} -lt 6 ]]; then
  echo "--password needs at least 6 characters" >&2; exit 2
fi
if virsh_ dominfo "$NAME" >/dev/null 2>&1; then
  echo "VM $NAME already exists. Remove it first: $0 --name $NAME --delete" >&2; exit 1
fi

if [[ -z "$BRANCH" ]]; then
  BRANCH=$(git -C "$(dirname "$0")" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
  [[ -z "$BRANCH" || "$BRANCH" == "HEAD" ]] && BRANCH="main"
fi
if [[ -z "$SSH_KEY" ]]; then
  for k in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_rsa.pub"; do
    [[ -f "$k" ]] && { SSH_KEY="$k"; break; }
  done
fi
KEY_LINE=""
[[ -n "$SSH_KEY" ]] && KEY_LINE="    ssh_authorized_keys: [\"$(cat "$SSH_KEY")\"]"

if ! sudo test -f "$BASE_IMG"; then
  echo "Downloading Debian 13 cloud image (once, ~400 MB)..."
  sudo curl -fL --progress-bar -o "$BASE_IMG.part" "$BASE_URL"
  sudo mv "$BASE_IMG.part" "$BASE_IMG"
fi
echo "Creating ${DISK_GB} GB disk $DISK (backed by the base image)..."
sudo qemu-img create -q -f qcow2 -F qcow2 -b "$BASE_IMG" "$DISK" "${DISK_GB}G"

SETUP_CMD="echo 'Skipping heating setup (--no-setup)'"
if [[ $SETUP -eq 1 ]]; then
  SETUP_CMD="python3 /opt/thermostat/dev/setup_heating.py --onboard --wait 1200 --experimental --username '$USERNAME' --password '$PASSWORD'"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cat > "$TMP/user-data" <<EOF
#cloud-config
hostname: $NAME
users:
  - name: $USERNAME
    shell: /bin/bash
    groups: [sudo]
    sudo: "ALL=(ALL) NOPASSWD:ALL"
    lock_passwd: false
$KEY_LINE
chpasswd:
  expire: false
  users:
    - {name: $USERNAME, password: "$PASSWORD", type: text}
ssh_pwauth: true
package_update: true
packages: [git, curl, ca-certificates, python3, qemu-guest-agent]
write_files:
  - path: /usr/local/bin/cmt-update
    permissions: "0755"
    content: |
      #!/bin/sh
      # Pull the latest code of the branch and restart the test HA
      set -e
      cd /opt/thermostat && git pull --ff-only
      cd dev && docker compose up -d && docker compose restart
runcmd:
  - systemctl enable --now qemu-guest-agent
  - curl -fsSL https://get.docker.com | sh
  - usermod -aG docker $USERNAME
  - git clone --branch "$BRANCH" "$REPO" /opt/thermostat
  - cd /opt/thermostat/dev && docker compose up -d
  - $SETUP_CMD
  - touch /var/lib/cmt-ready
final_message: "Cloudless Multiroom Thermostat test VM ready after \$UPTIME s"
EOF
printf 'instance-id: %s\nlocal-hostname: %s\n' "$NAME-$(date +%s)" "$NAME" > "$TMP/meta-data"

OSINFO="linux2022"
if command -v osinfo-query >/dev/null && osinfo-query os short-id=debian13 2>/dev/null | grep -q debian13; then
  OSINFO="debian13"
fi

echo "Creating VM $NAME (branch $BRANCH)..."
virt-install --connect "$CONNECT" \
  --name "$NAME" \
  --memory "$MEMORY" --vcpus "$VCPUS" \
  --osinfo "$OSINFO" \
  --disk "path=$DISK,format=qcow2,bus=virtio" \
  --network network=default,model=virtio \
  --channel unix,target.type=virtio,target.name=org.qemu.guest_agent.0 \
  --cloud-init "user-data=$TMP/user-data,meta-data=$TMP/meta-data" \
  --import --noautoconsole

echo -n "Waiting for an IP address"
IP=""
for _ in $(seq 1 60); do
  IP=$(virsh_ domifaddr "$NAME" 2>/dev/null | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}')
  [[ -n "$IP" ]] && break
  echo -n "."; sleep 5
done
echo

cat <<EOF

VM $NAME is booting (branch $BRANCH). First boot installs Docker and Home
Assistant, which takes about 5 to 10 minutes.

  Home Assistant:  http://${IP:-<vm-ip>}:8124   login $USERNAME / $PASSWORD$( [[ $SETUP -eq 0 ]] && echo " (do onboarding yourself)")
  SSH / console:   ssh $USERNAME@${IP:-<vm-ip>}   (password $PASSWORD)
  Progress:        ssh $USERNAME@${IP:-<vm-ip>} sudo tail -f /var/log/cloud-init-output.log
  Ready when:      /var/lib/cmt-ready exists in the VM
  New code:        ssh $USERNAME@${IP:-<vm-ip>} sudo cmt-update
  Remove:          $0 --name $NAME --delete
EOF
