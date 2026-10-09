# On-device face check asset licenses

Self-hosted and served from this origin only (no CDN at runtime). Fetched and verified against pinned SHA-256
values by `scripts/download_assets.sh face`.

| File | Source | License | SHA-256 |
|---|---|---|---|
| `wasm/vision_wasm_internal.js`, `wasm/vision_wasm_internal.wasm` | `@mediapipe/tasks-vision` 0.10.17 (npm, the same version as `apps/web/package.json`), Google | Apache-2.0 | `33a4125f825b343d2d9773951a73692f40bee368c9b591af8ff652fd501af90b`, `c88cf472dd5cab0a3954b071e5f442102ded3701dcccc987a7a02ee8f54aae85` |
| `models/blaze_face_short_range.tflite` | MediaPipe BlazeFace short-range face detector (float16, v1), https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/ | Apache-2.0 | `b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f` |

The server-side detector (`apps/api/models/face_detection_yunet_2023mar.onnx`, OpenCV Zoo YuNet, MIT,
SHA-256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`) is fetched by the same script.
