"""Expose Kokoro's predicted phoneme durations as a second ONNX graph output.

kokoro-v1.0.onnx computes per-token durations internally
(``/encoder/predictor`` -> round -> clamp(min=1) -> ``/encoder/Cast``) but only
returns the waveform. This adds an Identity node publishing that tensor as the
output ``duration`` (int64, one value per token including the two pad tokens),
the layout kokoro-onnx's ``create_timed`` reads to report when each phoneme is
spoken. The waveform output is unchanged (bit-identical audio).

Usage: python add_duration_output.py SOURCE.onnx DEST.onnx
Needs the ``onnx`` package (only at build time, not in the service image).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import onnx
from onnx import helper

DURATION_TENSOR = "/encoder/Cast_output_0"
DURATION_NODE = "/encoder/Cast"
OUTPUT_NAME = "duration"


def add_duration_output(model: onnx.ModelProto) -> onnx.ModelProto:
    graph = model.graph
    if any(output.name == OUTPUT_NAME for output in graph.output):
        return model
    cast = next((node for node in graph.node if node.name == DURATION_NODE), None)
    if cast is None or DURATION_TENSOR not in cast.output:
        raise SystemExit(f"{DURATION_NODE} producing {DURATION_TENSOR} not found: not a kokoro-v1.0 export")
    upstream = {output: node for node in graph.node for output in node.output}
    clip = upstream.get(cast.input[0])
    rounding = upstream.get(clip.input[0]) if clip is not None else None
    if clip is None or clip.op_type != "Clip" or rounding is None or rounding.op_type != "Round":
        raise SystemExit("unexpected graph around the duration predictor (expected Round -> Clip -> Cast)")
    to_type = next(attr.i for attr in cast.attribute if attr.name == "to")
    graph.node.append(
        helper.make_node("Identity", [DURATION_TENSOR], [OUTPUT_NAME], name="kokoro_duration_output")
    )
    graph.output.append(helper.make_tensor_value_info(OUTPUT_NAME, to_type, [1, "tokens_with_pad"]))
    onnx.checker.check_model(model)
    return model


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("dest", type=Path)
    args = parser.parse_args()
    started = time.perf_counter()
    model = add_duration_output(onnx.load(str(args.source)))
    tmp = args.dest.with_suffix(args.dest.suffix + ".tmp")
    onnx.save(model, str(tmp))
    tmp.replace(args.dest)
    outputs = [output.name for output in model.graph.output]
    print(f"wrote {args.dest} outputs={outputs} in {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
