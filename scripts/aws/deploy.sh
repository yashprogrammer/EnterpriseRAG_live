#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/01_ecr_build_push.sh"
"$SCRIPT_DIR/14_ecr_build_push_ui.sh"
"$SCRIPT_DIR/12_cfn_update_image.sh"
"$SCRIPT_DIR/09_smoke.sh"
