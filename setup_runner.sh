#!/bin/bash
# ════════════════════════════════════════════════════════════════════
#  Oracle Cloud Free Tier — GitHub Self-Hosted Runner Setup
#  Run this script on a fresh Oracle Cloud VM (ARM or x86 free tier)
#
#  Oracle Always Free specs:
#    ARM: 4 OCPUs, 24 GB RAM — completely free, forever
#    x86: 1/8 OCPU, 1 GB RAM — also free
#  Recommended: ARM instance (Ampere A1) — more than enough.
#
#  After setup, your GitHub Actions runs with ZERO scheduler delay.
#  The runner picks up jobs the instant they're queued.
#
#  Prerequisites:
#    1. Create Oracle Cloud account (always-free.oracle.com)
#    2. Launch Ubuntu 22.04 ARM instance (Always Free)
#    3. SSH into the instance
#    4. Run this script: bash setup_runner.sh YOUR_GITHUB_TOKEN YOUR_REPO_URL
#
#  Example:
#    bash setup_runner.sh ghp_xxxxxxxxxxxx https://github.com/you/odte-scanner
# ════════════════════════════════════════════════════════════════════

set -euo pipefail

GITHUB_TOKEN="${1:-}"
REPO_URL="${2:-}"

if [[ -z "$GITHUB_TOKEN" || -z "$REPO_URL" ]]; then
    echo "Usage: bash setup_runner.sh <GITHUB_TOKEN> <REPO_URL>"
    echo "Get token: GitHub → Settings → Developer Settings → Personal Access Tokens"
    echo "Scopes needed: repo, workflow, admin:org (if org repo)"
    exit 1
fi

echo "════════════════════════════════════════════"
echo "  Oracle Cloud Self-Hosted Runner Setup"
echo "  Repo: $REPO_URL"
echo "════════════════════════════════════════════"

# ── 1. System updates ─────────────────────────────────────────────
echo "[1/6] Updating system..."
sudo apt-get update -q
sudo apt-get install -y -q curl wget git python3 python3-pip python3-venv jq

# ── 2. Create runner user ─────────────────────────────────────────
echo "[2/6] Creating runner user..."
if ! id "runner" &>/dev/null; then
    sudo useradd -m -s /bin/bash runner
    echo "runner ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/runner
fi

# ── 3. Download GitHub Actions runner ─────────────────────────────
echo "[3/6] Downloading GitHub Actions runner..."
RUNNER_VERSION=$(curl -s https://api.github.com/repos/actions/runner/releases/latest | jq -r .tag_name | sed 's/v//')
ARCH=$(uname -m)
if [[ "$ARCH" == "aarch64" ]]; then
    RUNNER_ARCH="arm64"
else
    RUNNER_ARCH="x64"
fi

RUNNER_DIR="/home/runner/actions-runner"
sudo -u runner mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"

RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
echo "  Downloading: $RUNNER_URL"
sudo -u runner curl -sL "$RUNNER_URL" -o runner.tar.gz
sudo -u runner tar xzf runner.tar.gz
sudo -u runner rm runner.tar.gz

# ── 4. Register runner with GitHub ────────────────────────────────
echo "[4/6] Registering runner with GitHub..."

# Get registration token from GitHub API
REG_TOKEN=$(curl -s -X POST \
    -H "Authorization: token $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github.v3+json" \
    "${REPO_URL/github.com/api.github.com/repos}/actions/runners/registration-token" \
    | jq -r .token)

if [[ -z "$REG_TOKEN" || "$REG_TOKEN" == "null" ]]; then
    echo "ERROR: Could not get registration token. Check your GitHub token permissions."
    exit 1
fi

REPO_NAME=$(echo "$REPO_URL" | sed 's|.*/||')
sudo -u runner bash -c "
    cd $RUNNER_DIR
    ./config.sh \
        --url '$REPO_URL' \
        --token '$REG_TOKEN' \
        --name 'oracle-cloud-odte-runner' \
        --labels 'self-hosted,oracle-cloud,odte' \
        --work '_work' \
        --unattended \
        --replace
"

# ── 5. Install as systemd service (auto-start on reboot) ──────────
echo "[5/6] Installing as systemd service..."
cd "$RUNNER_DIR"
sudo ./svc.sh install runner
sudo ./svc.sh start

# ── 6. Python environment setup ───────────────────────────────────
echo "[6/6] Setting up Python environment..."
sudo -u runner pip3 install --upgrade pip
sudo -u runner pip3 install yfinance pandas numpy scipy pyyaml pytz requests

# ── Done ──────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════"
echo "  ✅ Runner setup complete!"
echo ""
echo "  Runner name: oracle-cloud-odte-runner"
echo "  Labels:      self-hosted, oracle-cloud, odte"
echo ""
echo "  Next steps:"
echo "  1. In your workflow YAML, change:"
echo "       runs-on: ubuntu-latest"
echo "     to:"
echo "       runs-on: [self-hosted, oracle-cloud]"
echo ""
echo "  2. Check runner status at:"
echo "     $REPO_URL/settings/actions/runners"
echo ""
echo "  Runner service commands:"
echo "    sudo systemctl status actions.runner.*"
echo "    sudo systemctl restart actions.runner.*"
echo "════════════════════════════════════════════"
