# Interview speech-to-text (optional compose profile "stt"): Whisper large-v3-turbo served by vLLM's
# OpenAI-compatible /v1/audio/transcriptions. The shared Whisper server on this node (full large-v3 on a
# transformers pipeline, one request at a time) stays configured as the fallback replica.
#
# Base: the vLLM build already used on this node (arm64, CUDA 13, GB10), pinned by digest; its image ships
# without vLLM's audio extras, which transcription needs.
FROM vllm/vllm-openai@sha256:809545e017527a0853d102bf77165909f70cf75b9089764107b4c5ead52cbb6c

RUN uv pip install --system --no-cache librosa==0.11.0 soundfile==0.13.1 \
    && python3 -c "import librosa, soundfile; print('audio extras', librosa.__version__, soundfile.__version__)"
