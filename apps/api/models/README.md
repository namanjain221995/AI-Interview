# Model files (downloaded, not tracked)

`scripts/download_assets.sh vad face` places the ONNX models here (sha256-verified):

| File | Source | Licence |
|---|---|---|
| `silero_vad.onnx` | snakers4/silero-vad (pinned commit) | MIT |
| `face_detection_yunet_2023mar.onnx` | opencv/opencv_zoo (pinned commit, Git LFS) | MIT |

The API image copies this directory (`apps/api/Dockerfile`), so it must exist even before the
download: this README is the tracked placeholder. Without `silero_vad.onnx` the realtime runtime falls
back to the energy VAD (`realtime.vad.silero_unavailable` in the logs) and turn-taking degrades.
