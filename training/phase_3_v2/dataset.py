"""Pinned HumAID event-type loading and leakage-aware split preparation.
Targets come from the official event-type config directory. The source row's
``class_label`` is a HumAID humanitarian category and is deliberately ignored
as a disaster-type target.
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parents[2]
DATASET_ID = "QCRI/HumAID-event-type"
DATASET_REVISION = "75b4fee3ff3047a0dc79740ca9ded611c9700593"
LABELS = ("earthquake", "fire", "flood", "hurricane")
SPLITS = ("train", "dev", "test")
EXPECTED_COUNTS = {
    "train": {"earthquake": 6250, "fire": 7792, "flood": 7815, "hurricane": 31674},
    "dev": {"earthquake": 909, "fire": 1134, "flood": 1137, "hurricane": 4613},
    "test": {"earthquake": 1773, "fire": 2207, "flood": 2214, "hurricane": 8966},
}
DEFAULT_CACHE = ROOT / "data" / "raw" / "humaid_event_type_hub"
def _load_config_split(
    config: str,
    split: str,
    *,
    cache_dir: Path = DEFAULT_CACHE,
    local_files_only: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    if config not in LABELS:
        raise ValueError(f"Unsupported event-type config: {config}")
    if split not in SPLITS:
        raise ValueError(f"Unsupported split: {split}")
    from huggingface_hub import hf_hub_download
    filename = f"{config}/{split}.json"
    path = Path(hf_hub_download(
        repo_id=DATASET_ID,
        filename=filename,
        revision=DATASET_REVISION,
        repo_type="dataset",
        cache_dir=str(cache_dir),
        local_files_only=local_files_only,
    ))
    import json
    with path.open("r", encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError(f"Expected a JSON list in {filename}")
    return rows, str(path)
def load_raw_splits(
    *, cache_dir: Path = DEFAULT_CACHE, local_files_only: bool = False
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Load official train/dev/test JSON by pinned revision.
    This uses direct pinned-file downloads instead of ``datasets.load_dataset``
    because the upstream card YAML currently fails parsing in that loader.
    """
    import hashlib
    splits: dict[str, list[dict[str, Any]]] = {name: [] for name in SPLITS}
    file_hashes: dict[str, str] = {}
    for config in LABELS:
        for split in SPLITS:
            rows, path = _load_config_split(
                config, split, cache_dir=cache_dir, local_files_only=local_files_only
            )
            if len(rows) != EXPECTED_COUNTS[split][config]:
                raise ValueError(
                    f"Unexpected row count for {config}/{split}: {len(rows)}"
                )
            digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
            file_hashes[f"{config}/{split}.json"] = digest
            for row in rows:
                text = row.get("tweet_text")
                if not isinstance(text, str) or not text.strip():
                    raise ValueError(f"Blank/missing tweet_text in {config}/{split}")
                # The config is the disaster-event-type label. Keep the original
                # humanitarian class_label only as audit metadata.
                splits[split].append({
                    "tweet_id": str(row.get("tweet_id", "")),
                    "text": text,
                    "label": config,
                    "source_config": config,
                    "source_humanitarian_label": row.get("class_label"),
                })
    counts = {
        split: dict(Counter(row["label"] for row in splits[split]))
        for split in SPLITS
    }
    for split in SPLITS:
        if len(splits[split]) != sum(EXPECTED_COUNTS[split].values()):
            raise ValueError(f"Unexpected total for {split}: {len(splits[split])}")
        if counts[split] != EXPECTED_COUNTS[split]:
            raise ValueError(f"Unexpected class counts for {split}: {counts[split]}")
    return splits, {
        "dataset": DATASET_ID,
        "revision": DATASET_REVISION,
        "source_files_sha256": file_hashes,
        "raw_split_counts": {key: len(value) for key, value in splits.items()},
        "raw_class_counts": counts,
        "row_class_label_used_as_target": False,
    }
def remove_training_leakage(
    splits: dict[str, list[dict[str, Any]]]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Drop exact raw-text matches from train when the same text is in dev/test.
    Supplied dev/test rows are never modified. This is deterministic and does
    not read, import, or derive anything from the Mastodon benchmark.
    """
    evaluation_texts = {
        row["text"] for name in ("dev", "test") for row in splits[name]
    }
    train = splits["train"]
    kept = [row for row in train if row["text"] not in evaluation_texts]
    removed = [row for row in train if row["text"] in evaluation_texts]
    cleaned = {name: list(rows) for name, rows in splits.items()}
    cleaned["train"] = kept
    removed_by_label = dict(Counter(row["label"] for row in removed))
    return cleaned, {
        "training_exact_text_rows_removed": len(removed),
        "training_exact_text_rows_removed_by_label": removed_by_label,
        "train_counts_after_leakage_guard": dict(Counter(row["label"] for row in kept)),
        "dev_count_unchanged": len(cleaned["dev"]) == len(splits["dev"]),
        "test_count_unchanged": len(cleaned["test"]) == len(splits["test"]),
    }
