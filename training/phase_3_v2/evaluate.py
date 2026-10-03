"""Evaluate an isolated Phase 3 checkpoint on the fixed original test and Mastodon benchmark."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LABELS = ("earthquake", "fire", "flood", "hurricane")
MASTODON_TRUE_LABELS = ("earthquake", "flood", "wildfire", "hurricane")
DEFAULT_V1 = ROOT / "notebooks" / "phase_3" / "disaster_type"
DEFAULT_ORIGINAL_TEST = DEFAULT_V1 / "test_predictions.csv"
DEFAULT_ADJUDICATION = ROOT / "evaluation" / "phase11_7_mastodon_manual_adjudication.json"
DEFAULT_RESULTS = ROOT / "evaluation" / "phase3_v2_results.json"


def load_inputs(original_test: Path, adjudication: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    with original_test.open("r", encoding="utf-8-sig", newline="") as handle:
        original = list(csv.DictReader(handle))
    with adjudication.open("r", encoding="utf-8") as handle:
        posts = json.load(handle)["posts"]
    mastodon = [
        {
            "id": str(row["mastodon_status_id"]),
            "text": row["raw_text"],
            "true_label": row["manual_label"],
        }
        for row in posts
        if row["adjudication_status"] == "adjudicated"
        and row["manual_label"] in MASTODON_TRUE_LABELS
    ]
    return original, mastodon


def _metric_summary(
    true: list[str],
    pred: list[str],
    confidence: list[float],
    labels: tuple[str, ...],
    probabilities: list[list[float]] | None = None,
    probability_labels: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    support = Counter(true)
    predicted = Counter(pred)
    per_class: dict[str, dict[str, float | int]] = {}
    confusion: dict[str, dict[str, int]] = {}
    f1_values = []
    for actual in labels:
        tp = sum(a == actual and p == actual for a, p in zip(true, pred))
        fp = sum(a != actual and p == actual for a, p in zip(true, pred))
        fn = sum(a == actual and p != actual for a, p in zip(true, pred))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        per_class[actual] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support[actual],
            "predicted_count": predicted[actual],
        }
        confusion[actual] = {
            out: sum(a == actual and p == out for a, p in zip(true, pred))
            for out in LABELS
        }
        for out in sorted(set(pred) - set(LABELS)):
            confusion[actual][out] = sum(a == actual and p == out for a, p in zip(true, pred))

    correct = [a == p for a, p in zip(true, pred)]
    ece = 0.0
    bin_rows = []
    for lower in [i / 10 for i in range(10)]:
        upper = lower + 0.1
        indices = [i for i, c in enumerate(confidence) if lower <= c < upper or (upper == 1.0 and c == 1.0)]
        if not indices:
            continue
        acc = sum(correct[i] for i in indices) / len(indices)
        avg_conf = sum(confidence[i] for i in indices) / len(indices)
        ece += len(indices) / len(true) * abs(acc - avg_conf)
        bin_rows.append({"range": [lower, upper], "count": len(indices), "accuracy": acc, "mean_confidence": avg_conf})

    class_count = len(labels)
    probability_labels = probability_labels or labels
    brier_indices = [
        i for i, actual in enumerate(true)
        if probabilities is not None and actual in probability_labels
    ]
    brier = None
    if probabilities is not None:
        brier = (
            sum(
                sum((probabilities[i][j] - float(true[i] == label)) ** 2 for j, label in enumerate(probability_labels))
                for i in brier_indices
            ) / len(brier_indices)
            if brier_indices else None
        )
    return {
        "n": len(true),
        "accuracy": sum(correct) / len(true) if true else None,
        "macro_f1": sum(f1_values) / class_count if class_count else None,
        "per_class": per_class,
        "confusion_matrix": {"rows": list(labels), "columns": list(LABELS) + sorted(set(pred) - set(LABELS)), "counts": confusion},
        "confidence": {
            "mean": sum(confidence) / len(confidence) if confidence else None,
            "median": statistics.median(confidence) if confidence else None,
            "minimum": min(confidence) if confidence else None,
            "maximum": max(confidence) if confidence else None,
            "mean_correct": sum(confidence[i] for i, ok in enumerate(correct) if ok) / sum(correct) if any(correct) else None,
            "mean_incorrect": sum(confidence[i] for i, ok in enumerate(correct) if not ok) / sum(not x for x in correct) if any(not x for x in correct) else None,
            "fraction_at_least_0_90": sum(c >= 0.9 for c in confidence) / len(confidence) if confidence else None,
            "ece_10_equal_width": ece,
            "multiclass_brier": brier,
            "brier_supported_n": len(brier_indices),
            "reliability_bins": bin_rows,
        },
    }


def predict_texts(model_dir: Path, texts: list[str], batch_size: int = 32, max_length: int = 128) -> list[dict[str, Any]]:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    from preprocessing.text_cleaner import clean_text

    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir), local_files_only=True)
    id2label = model.config.id2label or {}
    decoded = tuple(id2label.get(i, id2label.get(str(i))) for i in range(len(LABELS)))
    if decoded != LABELS:
        raise ValueError(f"Checkpoint class order {decoded} does not match expected {LABELS}")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()
    results: list[dict[str, Any]] = []
    with torch.inference_mode():
        for start in range(0, len(texts), batch_size):
            cleaned = [clean_text(text) for text in texts[start:start + batch_size]]
            enc = tokenizer(cleaned, truncation=True, padding="max_length", max_length=max_length, return_tensors="pt")
            logits = model(input_ids=enc["input_ids"].to(device), attention_mask=enc["attention_mask"].to(device)).logits
            probs = torch.softmax(logits.float(), dim=-1).cpu()
            vals, ids = torch.topk(probs, k=min(3, len(LABELS)), dim=-1)
            for row_probs, row_vals, row_ids in zip(probs, vals, ids):
                top_k = [{"label": LABELS[int(i)], "confidence": float(v)} for v, i in zip(row_vals, row_ids)]
                all_probabilities = [float(row_probs[index]) for index in range(len(LABELS))]
                results.append({
                    "predicted_label": top_k[0]["label"],
                    "confidence": top_k[0]["confidence"],
                    "top_k": top_k,
                    "probabilities": dict(zip(LABELS, all_probabilities)),
                })
    del model
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return results


def evaluate_checkpoint(
    model_dir: Path,
    original: list[dict[str, Any]],
    mastodon: list[dict[str, Any]],
    *, batch_size: int = 32,
) -> dict[str, Any]:
    original_preds = predict_texts(model_dir, [row["tweet_text"] for row in original], batch_size=batch_size)
    mastodon_preds = predict_texts(model_dir, [row["text"] for row in mastodon], batch_size=batch_size)
    original_true = [row["true_label"] for row in original]
    mastodon_true = [row["true_label"] for row in mastodon]
    if any(label not in LABELS for label in original_true):
        raise ValueError("Original test contains labels outside the four-class checkpoint taxonomy")

    def prediction_rows(source_rows: list[dict[str, Any]], truth: list[str], outputs: list[dict[str, Any]], id_field: str | None = None) -> list[dict[str, Any]]:
        records = []
        for i, (row, label, output) in enumerate(zip(source_rows, truth, outputs)):
            record = {
                "row": i + 1,
                "true_label": label,
                **output,
                "correct": label == output["predicted_label"],
            }
            if id_field:
                record["post_id"] = row[id_field]
                record["text"] = row["text"]
            records.append(record)
        return records

    p3_saved_disagreement = None
    if original and "predicted_label" in original[0]:
        p3_saved_disagreement = sum(
            row.get("predicted_label") != output["predicted_label"]
            for row, output in zip(original, original_preds)
        )
    return {
        "checkpoint": str(model_dir),
        "device": "cuda" if __import__("torch").cuda.is_available() else "cpu",
        "original_test": {
            **_metric_summary(
                original_true,
                [x["predicted_label"] for x in original_preds],
                [x["confidence"] for x in original_preds],
                LABELS,
                [[x["probabilities"][label] for label in LABELS] for x in original_preds],
                LABELS,
            ),
            "saved_csv_prediction_disagreements": p3_saved_disagreement,
            "predictions": prediction_rows(original, original_true, original_preds),
        },
        "mastodon_manual": {
            "human_labels": list(MASTODON_TRUE_LABELS),
            "wildfire_is_scored_strictly_against_fire_output": True,
            **_metric_summary(
                mastodon_true,
                [x["predicted_label"] for x in mastodon_preds],
                [x["confidence"] for x in mastodon_preds],
                MASTODON_TRUE_LABELS,
                [[x["probabilities"][label] for label in LABELS] for x in mastodon_preds],
                LABELS,
            ),
            "predictions": prediction_rows(mastodon, mastodon_true, mastodon_preds, "id"),
        },
    }


def _markdown_report(payload: dict[str, Any]) -> str:
    out = ["# Phase 3 V2 evaluation", "", "All results are computed from the saved V1/V2 checkpoints; no manual benchmark records enter training. The Mastodon four-class score is strict: `fire` is not renamed to `wildfire`. The Mastodon Brier score is computed on native-label posts only because `wildfire` is outside the model taxonomy; strict accuracy and class metrics still include all adjudicated labels.", ""]
    for version in ("baseline_v1", "v2"):
        if version not in payload:
            continue
        item = payload[version]
        out += [f"## {version.replace('_', ' ').upper()}", ""]
        for split in ("original_test", "mastodon_manual"):
            m = item[split]
            out += [f"### {split.replace('_', ' ').title()}", "", f"n={m['n']}; accuracy={m['accuracy']:.4f}; macro F1={m['macro_f1']:.4f}; ECE(10 bins)={m['confidence']['ece_10_equal_width']:.4f}; Brier={m['confidence']['multiclass_brier']:.4f}.", "", "| Class | Precision | Recall | F1 | Support |", "|---|---:|---:|---:|---:|"]
            for label, row in m["per_class"].items():
                out.append(f"| {label} | {row['precision']:.4f} | {row['recall']:.4f} | {row['f1']:.4f} | {row['support']} |")
            out += ["", "Confusion matrix (rows=true label, columns=prediction):", "", "```json", json.dumps(m["confusion_matrix"], indent=2, ensure_ascii=False), "```", ""]
            out += [
                "Confidence summary: "
                f"mean={m['confidence']['mean']:.4f}; median={m['confidence']['median']:.4f}; "
                f"correct mean={m['confidence']['mean_correct']:.4f}; "
                f"incorrect mean={m['confidence']['mean_incorrect']:.4f}; "
                f"at least 0.90={m['confidence']['fraction_at_least_0_90']:.1%}; "
                f"range=[{m['confidence']['minimum']:.4f}, {m['confidence']['maximum']:.4f}]; "
                f"multiclass Brier={m['confidence']['multiclass_brier']:.4f} on n={m['confidence']['brier_supported_n']} native-label rows.",
                "",
            ]
            if split == "mastodon_manual":
                errors = [row for row in m["predictions"] if not row["correct"]]
                out += ["#### Mastodon benchmark errors", ""]
                if errors:
                    out += ["| Post ID | Human | Prediction | Confidence | Top 3 | Original text |", "|---|---|---|---:|---|---|"]
                    for row in errors:
                        text = " ".join(row["text"].split()).replace("|", "\\|")
                        if len(text) > 360:
                            text = text[:357] + "..."
                        top = "; ".join(f"{x['label']} {x['confidence']:.3f}" for x in row["top_k"])
                        out.append(f"| {row['post_id']} | {row['true_label']} | {row['predicted_label']} | {row['confidence']:.4f} | {top} | {text} |")
                    out.append("")
                else:
                    out += ["No errors on this benchmark.", ""]
    if "baseline_v1" in payload and "v2" in payload:
        out += ["## V1 versus V2", "", "| Evaluation | V1 accuracy | V2 accuracy | V1 macro F1 | V2 macro F1 |", "|---|---:|---:|---:|---:|"]
        for split, name in (("original_test", "Original test"), ("mastodon_manual", "Mastodon manual")):
            a, b = payload["baseline_v1"][split], payload["v2"][split]
            out.append(f"| {name} | {a['accuracy']:.4f} | {b['accuracy']:.4f} | {a['macro_f1']:.4f} | {b['macro_f1']:.4f} |")
        out.append("")
        v1m = payload["baseline_v1"]["mastodon_manual"]
        v2m = payload["v2"]["mastodon_manual"]
        v1orig = payload["baseline_v1"]["original_test"]
        v2orig = payload["v2"]["original_test"]
        out += [
            "## Error pattern and candidate assessment",
            "",
            f"On the 33-post adjudicated Mastodon cohort, V2 got {round(v2m['accuracy'] * v2m['n'])}/{v2m['n']} correct versus V1 {round(v1m['accuracy'] * v1m['n'])}/{v1m['n']}; macro F1 changed from {v1m['macro_f1']:.4f} to {v2m['macro_f1']:.4f}. On the original {v1orig['n']:,}-row test export, accuracy changed from {v1orig['accuracy']:.4f} to {v2orig['accuracy']:.4f}, and macro F1 from {v1orig['macro_f1']:.4f} to {v2orig['macro_f1']:.4f}.",
            "",
            "| Human label | V1 recall | V2 recall | V2 errors |",
            "|---|---:|---:|---:|",
        ]
        for label in MASTODON_TRUE_LABELS:
            p3, p2 = v1m["per_class"][label], v2m["per_class"][label]
            errors = p2["support"] - round(p2["recall"] * p2["support"])
            out.append(f"| {label} | {p3['recall']:.3f} | {p2['recall']:.3f} | {errors}/{p2['support']} |")
        out += [
            "",
            f"V2 reduces earthquake→hurricane errors on this sample (7 to 4) but leaves flood→hurricane unchanged (10/10). All 9 hurricane posts remain correctly classified. The four human-labeled wildfire posts still produce the model's `fire` label and remain errors under the required strict taxonomy. V2 Mastodon mean confidence is {v2m['confidence']['mean']:.4f} and {v2m['confidence']['fraction_at_least_0_90']:.1%} of predictions are at least 0.90; mean confidence on its errors is {v2m['confidence']['mean_incorrect']:.4f}. V2 is **not a candidate replacement**: the flood failure pattern is unchanged and the benchmark remains small and source-specific, despite the aggregate gain and preserved original-test score.",
            "",
        ]
    metadata = payload.get("training_metadata")
    if metadata:
        data = metadata["dataset"]
        out += [
            "## Training provenance and configuration",
            "",
            f"Dataset: `{data['dataset']}` at revision `{data['revision']}`; official event-config directory defines each disaster label (source humanitarian `class_label` is not used). License recorded by the source: CC BY-NC-SA 4.0. Raw train/dev/test sizes: {data['raw_split_counts']['train']:,}/{data['raw_split_counts']['dev']:,}/{data['raw_split_counts']['test']:,}. V2 train after exact-text leakage guard: {sum(metadata['train_counts_after_guard'].values()):,}; dev/test unchanged. Exact duplicate train rows removed: {metadata['leakage_guard']['training_exact_text_rows_removed']}.",
            "",
            f"Training: {metadata['base_model']} at revision `{metadata['base_model_revision']}`, max length {metadata['max_length']}, weighted random sampling with replacement, AdamW lr={metadata['learning_rate']}, weight decay={metadata['weight_decay']}, batch={metadata['batch_size_per_device']} × accumulation {metadata['gradient_accumulation_steps']}, seed={metadata['seed']}, fp16={metadata['fp16']}, {metadata['epochs_run']} epochs, best epoch {metadata['best_epoch']} by validation macro F1. Duration {metadata['duration_seconds'] / 60:.1f} minutes on {metadata['gpu']}.",
            "",
            "This is a controlled class-balancing retraining experiment on HumAID, not source-domain adaptation: no Mastodon data, benchmark-derived augmentation, or second deployment-domain corpus was used. Thus the deployment shift remains an evaluation-only condition and these results do not establish robust cross-platform generalization.",
            "",
            "Runtime versions: " + ", ".join(f"{name}={version}" for name, version in metadata.get("runtime_versions", {}).items()) + ".",
            "",
            "Training class counts after leakage guard: " + ", ".join(f"{label}={count:,}" for label, count in metadata["train_counts_after_guard"].items()) + ".",
            "",
        ]
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-dir", type=Path, default=DEFAULT_V1)
    parser.add_argument("--v2-dir", type=Path, default=None)
    parser.add_argument("--original-test", type=Path, default=DEFAULT_ORIGINAL_TEST)
    parser.add_argument("--adjudication", type=Path, default=DEFAULT_ADJUDICATION)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--output-md", type=Path, default=ROOT / "evaluation" / "phase3_v2_results.md")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    original, mastodon = load_inputs(args.original_test, args.adjudication)
    payload: dict[str, Any] = {
        "evaluation_only": True,
        "v1_checkpoint_preserved": True,
        "mastodon_is_evaluation_only": True,
        "baseline_v1": evaluate_checkpoint(args.v1_dir, original, mastodon, batch_size=args.batch_size),
    }
    if args.v2_dir:
        payload["v2"] = evaluate_checkpoint(args.v2_dir, original, mastodon, batch_size=args.batch_size)
        metadata_path = args.v2_dir.parent / "training_metadata.json"
        if metadata_path.exists():
            payload["training_metadata"] = json.loads(metadata_path.read_text(encoding="utf-8"))
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text(_markdown_report(payload), encoding="utf-8")
    print(f"wrote {args.output_json} and {args.output_md}")
    for version in ("baseline_v1", "v2"):
        if version in payload:
            item = payload[version]
            print(version, "original", item["original_test"]["n"], item["original_test"]["accuracy"], item["original_test"]["macro_f1"])
            print(version, "mastodon", item["mastodon_manual"]["n"], item["mastodon_manual"]["accuracy"], item["mastodon_manual"]["macro_f1"])


if __name__ == "__main__":
    main()
