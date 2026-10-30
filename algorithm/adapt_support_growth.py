#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Business-local initial/evolution support adaptation entry.

Unlike the earlier audit-only wrapper, this entry actually performs the model
adaptation used by the submitted reference implementation:

1. frozen DINOv2 ViT-S/14 multi-layer feature extraction;
2. normal global memory + position-aware memory + local PCA;
3. support defect / hard-normal prototype construction;
4. residual Adapter/MIL fitting;
5. repeated support-only OOF method/threshold selection;
6. final support model fitting and portable array export.

The non-public final/test labels are never read by this script.
"""
from __future__ import annotations

# Business-local growth entry; the original submission remains unchanged.
import sys, os, json
from pathlib import Path
_business_root = Path(__file__).resolve().parents[1]
_original_dir = Path(os.getenv("AOI_ORIGINAL_DIR") or json.loads((_business_root / "config.json").read_text(encoding="utf-8-sig"))["original_dir"])
sys.path.insert(0, str(_original_dir / "code"))

import argparse
import csv
import dataclasses
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from official_core import (
    configure_reference_environment,
    image_files,
    load_reference_core,
    make_records,
    manifest_hash,
    sha256_file,
    write_json,
)
from feature_backend import extract_feature_map
from model_io import save_support_model


def parse_args():
    ap = argparse.ArgumentParser(description="Fit AOI from initial 100+30 or cumulative human-confirmed support images")
    ap.add_argument("--normal-dir", required=True)
    ap.add_argument("--defect-dir", required=True)
    ap.add_argument("--output-dir", default="support_adapted_model")
    ap.add_argument("--weights", default=os.getenv("AOI_LOCAL_WEIGHTS", ""), help="DINOv2-S weights; omitted only when the configured backend does not need PyTorch extraction")
    ap.add_argument("--feature-backend", choices=["torch", "ascend", "auto"], default="torch")
    ap.add_argument("--device", default="auto", help="torch backend: auto/cuda/cpu")
    ap.add_argument("--feature-batch-size", type=int, default=4)
    ap.add_argument("--dino-om", default="")
    ap.add_argument("--feature-engine", default="")
    ap.add_argument("--oof-repeats", type=int, default=3)
    ap.add_argument("--oof-folds", type=int, default=3)
    ap.add_argument("--adapter-epochs", type=int, default=10)
    ap.add_argument("--safe-hours", type=float, default=11.0)
    ap.add_argument("--adaptation-type", choices=["initial", "evolution"], default="initial")
    ap.add_argument("--allow-nonstandard-counts", action="store_true", help="development only; official protocol is exactly 100+30")
    return ap.parse_args()


def main():
    a = parse_args()
    out = Path(a.output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cache_dir = out / "feature_cache"

    normal_paths = image_files(a.normal_dir)
    defect_paths = image_files(a.defect_dir)
    if a.adaptation_type == "evolution" and (a.allow_nonstandard_counts or (a.oof_repeats, a.oof_folds, a.adapter_epochs) != (3, 3, 10)):
        raise SystemExit("Evolution requires formal 3 repeats / 3 folds / 10 epochs; development mode is forbidden")
    if a.adaptation_type == "evolution" and (len(normal_paths) < 9 or len(defect_paths) < 3):
        raise SystemExit("Evolution requires at least nine normal and three defect images for formal OOF")
    if a.adaptation_type == "initial" and not a.allow_nonstandard_counts and (len(normal_paths) != 100 or len(defect_paths) != 30):
        raise SystemExit(f"Official protocol requires exactly 100 normal + 30 defect images; got {len(normal_paths)} + {len(defect_paths)}")
    if len(normal_paths) < 2 or len(defect_paths) < 2:
        raise SystemExit("support adaptation requires at least two normal and two defect images")

    configure_reference_environment(
        output_dir=out / "reference_runtime",
        cache_dir=cache_dir,
        weights=a.weights or None,
        feature_batch_size=a.feature_batch_size,
        oof_repeats=a.oof_repeats,
        oof_folds=a.oof_folds,
        adapter_epochs=a.adapter_epochs,
        safe_hours=a.safe_hours,
    )
    os.environ["AOI_SUPPORT_NORMAL"] = str(len(normal_paths))
    os.environ["AOI_SUPPORT_DEFECT"] = str(len(defect_paths))
    B = load_reference_core()

    import torch
    device = torch.device("cuda" if a.device == "auto" and torch.cuda.is_available() else ("cpu" if a.device == "auto" else a.device))
    B.set_seed(B.CFG.split_seed)
    B.TIME_GUARD = B.TimeGuard(a.safe_hours)
    B.LOGGER = B.setup_logging(out / "logs")

    support_normal, support_defect = make_records(B, normal_paths, defect_paths)
    support = support_normal + support_defect

    # Auditable support manifest.
    with (out / "support_manifest.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["path", "label", "role", "sha256"])
        w.writeheader()
        for record in support:
            p = Path(record.path)
            w.writerow({"path": str(p), "label": int(record.label), "role": record.role, "sha256": sha256_file(p)})

    started = time.perf_counter()
    feature_map, feature_diag = extract_feature_map(
        B,
        support,
        a.feature_backend,
        cache_dir,
        weights=a.weights or None,
        device=a.device,
        feature_batch_size=a.feature_batch_size,
        dino_om=a.dino_om or None,
        feature_engine=a.feature_engine or None,
    )

    print("[ADAPT] support-only repeated OOF selection ...")
    oof = B.run_oof_v25(support, feature_map, "anonymous_private_task", device)
    oof_metrics = {m: oof.calibrations[m].oof_metrics for m in B.V25_METHODS}

    # Fit the final model on all 100+30 support examples.  No final/test image is
    # passed into either function below.
    print("[ADAPT] fitting final context + residual adapter on all support images ...")
    seed = B.CFG.split_seed + int(B.stable_hash("anonymous_private_task", 8), 16)
    context = B._v25_build_context_model(support_normal, support_defect, feature_map, seed + 17)
    adapter = B.train_v25_adapter(support, feature_map, context, device, seed + 71)

    metadata = {
        "protocol": "cumulative_feedback_formal" if a.adaptation_type == "evolution" else "competition_support_100_normal_30_defect",
        "adaptation_type": a.adaptation_type,
        "normal_count": len(support_normal),
        "defect_count": len(support_defect),
        "support_hash": manifest_hash(normal_paths + defect_paths),
        "final_private_labels_used": False,
        "product_name_used_as_feature": False,
        "backbone_frozen": True,
        "feature_backend": feature_diag,
        "split_seed": int(B.CFG.split_seed),
        "oof_repeats": int(B.V25_OOF_REPEATS),
        "oof_folds": int(B.V25_OOF_FOLDS),
        "adapter_epochs": int(B.V25_ADAPTER_EPOCHS),
        "elapsed_seconds": time.perf_counter() - started,
    }
    save_support_model(B, out, context=context, adapter=adapter, oof=oof, metadata=metadata)

    # Export the two large nearest-neighbour banks separately so CANN hotspot
    # ONNX/OM can be rebuilt from this support-specific model if desired.
    hotspot = out / "hotspot_banks"
    hotspot.mkdir(parents=True, exist_ok=True)
    global_bank = np.asarray(context.core.global_bank, dtype=np.float32)
    relation = np.asarray(adapter.memory.bank, dtype=np.float32)
    if global_bank.shape != (6144, 64):
        raise RuntimeError(f"unexpected global bank shape {global_bank.shape}; expected (6144, 64)")
    if relation.shape != (8192, 64):
        raise RuntimeError(f"unexpected relation memory shape {relation.shape}; expected (8192, 64)")
    global_bank.tofile(hotspot / "global_bank.bin")
    relation.tofile(hotspot / "relation_memory.bin")

    summary = {
        "status": "ok",
        "selected_method": oof.selected_method,
        "selected_threshold": float(oof.calibrations[B.V25_METHOD_SAFE].threshold),
        "selected_topk_ratio": float(oof.calibrations[B.V25_METHOD_SAFE].topk_ratio),
        "oof_metrics": oof_metrics,
        "selection_diagnostics": oof.selection_diagnostics,
        "model_dir": str(out),
        "hotspot_global_bank": str(hotspot / "global_bank.bin"),
        "hotspot_relation_memory": str(hotspot / "relation_memory.bin"),
        **metadata,
    }
    write_json(out / "adaptation_summary.json", summary)
    print("SUPPORT_ADAPT_OK")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

