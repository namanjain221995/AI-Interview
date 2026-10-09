# Kokoro model files

Not committed (see the repository `.gitignore`). The Docker build copies them
from here when present and otherwise downloads them; in both cases the SHA-256
must match (`scripts/fetch_models.py`):

| File | SHA-256 | Source |
|---|---|---|
| `kokoro-v1.0.onnx` (fp32, 326 MB) | `7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5` | https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx |
| `voices-v1.0.bin` (54 voices, 28 MB) | `bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d` | https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin |

Use the fp32 model: on the DGX Spark's ARM CPU the int8 variant is about 4x slower.

`kokoro-v1.0-durations.onnx` is derived, not downloaded: the same graph with
the predicted phoneme durations exposed as a second output. The image builds it;
for local runs create it with

```bash
uv run --no-project --with onnx==1.23.2 --python 3.12 \
  python scripts/add_duration_output.py models/kokoro-v1.0.onnx models/kokoro-v1.0-durations.onnx
```

Without it the service still works and estimates viseme timings instead.
