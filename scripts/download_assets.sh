#!/usr/bin/env bash
# Downloads the large binary assets that are not committed to git and verifies
# every file against a pinned SHA-256. Idempotent: files that already match are
# left alone; a missing or mismatching file is (re)downloaded to a temporary
# name and only moved into place after its checksum matches.
#
#   scripts/download_assets.sh                # everything
#   scripts/download_assets.sh tts vad        # a subset: tts | vad | avatar | face
#   scripts/download_assets.sh --check        # verify only, never download (exit 1 on problems)
#
# Assets:
#   tts     Kokoro v1.0 ONNX model + voices        -> services/tts/models/
#   vad     Silero VAD ONNX (server-side endpointing) -> apps/api/models/silero_vad.onnx
#   avatar  MPFB interviewer avatar (CC0)          -> apps/web/public/avatars/source/mpfb.glb
#           (the shipped, optimized copy is produced by `make optimize-avatar`)
#   face    Face check: YuNet ONNX (opencv_zoo, MIT)  -> apps/api/models/face_detection_yunet_2023mar.onnx
#           BlazeFace short-range (Apache-2.0)        -> apps/web/public/mediapipe/models/
#           MediaPipe Tasks Vision 0.10.17 WASM (Apache-2.0, same version as
#           apps/web/package.json; self-hosted, never loaded from a CDN at runtime)
#                                                     -> apps/web/public/mediapipe/wasm/
set -euo pipefail
# shellcheck source=scripts/_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

# group|relative destination|url|sha256|bytes
ASSETS=(
  "tts|services/tts/models/kokoro-v1.0.onnx|https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx|7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5|325532387"
  "tts|services/tts/models/voices-v1.0.bin|https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin|bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d|28214398"
  "vad|apps/api/models/silero_vad.onnx|https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx|1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3|2327524"
  "avatar|apps/web/public/avatars/source/mpfb.glb|https://raw.githubusercontent.com/met4citizen/TalkingHead/main/avatars/mpfb.glb|63c645a2a863b9972e9a9c2ed576a1de4c390b8475508e1473e69c87a3ee299c|36815920"
  "face|apps/api/models/face_detection_yunet_2023mar.onnx|https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx|8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4|232589"
  "face|apps/web/public/mediapipe/models/blaze_face_short_range.tflite|https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite|b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f|229746"
  "face|apps/web/public/mediapipe/wasm/vision_wasm_internal.js|https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.17/wasm/vision_wasm_internal.js|33a4125f825b343d2d9773951a73692f40bee368c9b591af8ff652fd501af90b|203819"
  "face|apps/web/public/mediapipe/wasm/vision_wasm_internal.wasm|https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.17/wasm/vision_wasm_internal.wasm|c88cf472dd5cab0a3954b071e5f442102ded3701dcccc987a7a02ee8f54aae85|9514390"
)

check_only=false
groups=()
for arg in "$@"; do
  case "$arg" in
    --check) check_only=true ;;
    tts | vad | avatar | face) groups+=("$arg") ;;
    -h | --help)
      sed -n '2,20p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $arg (expected tts, vad, avatar, face or --check)" ;;
  esac
done

wanted() {
  local group="$1" g
  [[ ${#groups[@]} -eq 0 ]] && return 0
  for g in "${groups[@]}"; do [[ "$g" == "$group" ]] && return 0; done
  return 1
}

fetch() {
  local url="$1" out="$2"
  if command -v curl >/dev/null 2>&1; then
    local progress=(--silent --show-error)
    [[ -t 2 ]] && progress=(--progress-bar)
    curl --fail --location --proto '=https' --tlsv1.2 --retry 3 --retry-delay 3 \
      --connect-timeout 20 "${progress[@]}" --output "$out" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only --tries=3 --timeout=60 --show-progress -q -O "$out" "$url"
  else
    die "curl or wget is required"
  fi
}

human() { awk -v b="$1" 'BEGIN { split("B KiB MiB GiB", u); i = 1; while (b >= 1024 && i < 4) { b /= 1024; i++ } printf "%.1f %s", b, u[i] }'; }

problems=0
for entry in "${ASSETS[@]}"; do
  IFS='|' read -r group rel url sha bytes <<<"$entry"
  wanted "$group" || continue
  dest="$ROOT/$rel"

  if [[ -f "$dest" ]]; then
    actual="$(sha256_of "$dest")"
    if [[ "$actual" == "$sha" ]]; then
      ok "$rel ($(human "$bytes"), sha256 verified)"
      continue
    fi
    warn "$rel has an unexpected checksum ($actual)"
  elif $check_only; then
    fail "$rel is missing"
  fi

  if $check_only; then
    problems=$((problems + 1))
    continue
  fi

  mkdir -p "$(dirname "$dest")"
  tmp="$(mktemp "${dest}.part.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  info "downloading $rel ($(human "$bytes")) from $url"
  if ! fetch "$url" "$tmp"; then
    rm -f "$tmp"
    fail "download failed: $url"
    problems=$((problems + 1))
    continue
  fi
  actual="$(sha256_of "$tmp")"
  if [[ "$actual" != "$sha" ]]; then
    rm -f "$tmp"
    fail "$rel: checksum mismatch (expected $sha, got $actual); file discarded"
    problems=$((problems + 1))
    continue
  fi
  chmod 0644 "$tmp"
  mv -f "$tmp" "$dest"
  trap - EXIT
  ok "$rel downloaded and verified"
done

if [[ $problems -gt 0 ]]; then
  die "$problems asset(s) missing or invalid"
fi

if wanted avatar && [[ ! -f "$ROOT/apps/web/public/avatars/interviewer.glb" ]]; then
  warn "apps/web/public/avatars/interviewer.glb (optimized avatar) is missing: run 'make optimize-avatar'"
fi
