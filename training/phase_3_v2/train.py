"""Train a reproducible Phase 3 V2 checkpoint without touching production V1."""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import time
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from training.phase_3_v2.dataset import LABELS, ROOT, load_raw_splits, remove_training_leakage

BASE_MODEL = "distilbert-base-uncased"
BASE_MODEL_REVISION = "12040accade4e8a0f71eabdb258fecc2e7e948be"
MAX_LENGTH = 128


def seed_everything(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def tokenize_records(rows: list[dict[str, Any]], tokenizer, label_to_id: dict[str, int]):
    from preprocessing.text_cleaner import clean_text
    import torch

    texts = [clean_text(row["text"]) for row in rows]
    encoded = tokenizer(texts, truncation=True, padding="max_length", max_length=MAX_LENGTH, return_tensors="pt")
    labels = torch.tensor([label_to_id[row["label"]] for row in rows], dtype=torch.long)
    class Dataset(torch.utils.data.Dataset):
        def __len__(self):
            return len(labels)

        def __getitem__(self, index):
            return {
                "input_ids": encoded["input_ids"][index],
                "attention_mask": encoded["attention_mask"][index],
                "labels": labels[index],
            }
    return Dataset()


def score_model(model, loader, device) -> dict[str, Any]:
    import torch
    from sklearn.metrics import accuracy_score, f1_score

    model.eval()
    true_ids, pred_ids = [], []
    with torch.inference_mode():
        for batch in loader:
            labels = batch["labels"].to(device)
            logits = model(
                input_ids=batch["input_ids"].to(device),
                attention_mask=batch["attention_mask"].to(device),
            ).logits
            true_ids.extend(labels.cpu().tolist())
            pred_ids.extend(logits.argmax(dim=-1).cpu().tolist())
    return {
        "accuracy": float(accuracy_score(true_ids, pred_ids)),
        "macro_f1": float(f1_score(true_ids, pred_ids, labels=list(range(len(LABELS))), average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(true_ids, pred_ids, labels=list(range(len(LABELS))), average="weighted", zero_division=0)),
        "support": len(true_ids),
    }


def train(args) -> dict[str, Any]:
    import torch
    from torch.utils.data import DataLoader, WeightedRandomSampler
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, get_linear_schedule_with_warmup

    seed_everything(args.seed)
    output_dir = args.output_dir
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Refusing to overwrite a non-empty V2 output directory: {output_dir}")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda" and not args.allow_cpu:
        raise RuntimeError("CUDA is unavailable. Re-run with --allow-cpu only after reviewing runtime practicality.")
    splits, data_manifest = load_raw_splits(cache_dir=args.data_cache, local_files_only=args.local_files_only)
    splits, leakage = remove_training_leakage(splits)
    label_to_id = {label: index for index, label in enumerate(LABELS)}
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL, revision=BASE_MODEL_REVISION)
    model = AutoModelForSequenceClassification.from_pretrained(
        BASE_MODEL,
        revision=BASE_MODEL_REVISION,
        num_labels=len(LABELS),
        id2label={i: label for i, label in enumerate(LABELS)},
        label2id=label_to_id,
    )
    model.to(device)

    train_data = tokenize_records(splits["train"], tokenizer, label_to_id)
    dev_data = tokenize_records(splits["dev"], tokenizer, label_to_id)
    test_data = tokenize_records(splits["test"], tokenizer, label_to_id)
    dev_loader = DataLoader(dev_data, batch_size=args.batch_size, shuffle=False, num_workers=0)
    test_loader = DataLoader(test_data, batch_size=args.batch_size, shuffle=False, num_workers=0)

    class_counts = Counter(row["label"] for row in splits["train"])
    label_indices = [label_to_id[row["label"]] for row in splits["train"]]
    sample_weights = torch.tensor([1.0 / class_counts[LABELS[index]] for index in label_indices], dtype=torch.double)
    steps_per_epoch = (len(train_data) + args.batch_size * args.grad_accum - 1) // (args.batch_size * args.grad_accum)
    total_steps = steps_per_epoch * args.epochs
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate, weight_decay=args.weight_decay)
    scheduler = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=int(total_steps * args.warmup_ratio),
        num_training_steps=total_steps,
    )
    criterion = torch.nn.CrossEntropyLoss()
    scaler = torch.cuda.amp.GradScaler(enabled=device.type == "cuda" and args.fp16)
    checkpoint_dir = output_dir / "best_checkpoint"
    output_dir.mkdir(parents=True, exist_ok=True)
    best_macro_f1 = -1.0
    best_epoch = 0
    no_improve = 0
    history = []
    started = time.perf_counter()

    for epoch in range(1, args.epochs + 1):
        model.train()
        sampler = WeightedRandomSampler(
            sample_weights,
            num_samples=len(train_data),
            replacement=True,
            generator=torch.Generator().manual_seed(args.seed + epoch),
        )
        train_loader = DataLoader(train_data, batch_size=args.batch_size, sampler=sampler, num_workers=0)
        optimizer.zero_grad(set_to_none=True)
        losses = []
        for step, batch in enumerate(train_loader, start=1):
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels = batch["labels"].to(device)
            with torch.cuda.amp.autocast(enabled=device.type == "cuda" and args.fp16):
                logits = model(input_ids=input_ids, attention_mask=attention_mask).logits
                loss = criterion(logits, labels) / args.grad_accum
            scaler.scale(loss).backward()
            losses.append(float(loss.detach().cpu()) * args.grad_accum)
            if step % args.grad_accum == 0 or step == len(train_loader):
                scaler.unscale_(optimizer)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(optimizer)
                scaler.update()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)

        validation = score_model(model, dev_loader, device)
        row = {
            "epoch": epoch,
            "train_loss": float(np.mean(losses)),
            "validation": validation,
        }
        history.append(row)
        print(f"epoch={epoch} train_loss={row['train_loss']:.4f} dev_accuracy={validation['accuracy']:.4f} dev_macro_f1={validation['macro_f1']:.4f}")
        if validation["macro_f1"] > best_macro_f1:
            best_macro_f1 = validation["macro_f1"]
            best_epoch = epoch
            no_improve = 0
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(checkpoint_dir, safe_serialization=True)
            tokenizer.save_pretrained(checkpoint_dir)
        else:
            no_improve += 1
            if no_improve >= args.patience:
                break

    best_model = AutoModelForSequenceClassification.from_pretrained(checkpoint_dir, local_files_only=True).to(device)
    test_metrics = score_model(best_model, test_loader, device)
    duration_seconds = time.perf_counter() - started
    config = {
        "experiment": "phase3_disaster_type_v2_balanced_sampler",
        "production_integration": False,
        "taxonomy": list(LABELS),
        "label2id": label_to_id,
        "id2label": {str(i): label for i, label in enumerate(LABELS)},
        "base_model": BASE_MODEL,
        "base_model_revision": BASE_MODEL_REVISION,
        "tokenizer": BASE_MODEL,
        "max_length": MAX_LENGTH,
        "truncation": True,
        "padding": "max_length",
        "text_cleaner": "preprocessing.text_cleaner.clean_text (same as current production inference)",
        "dataset": data_manifest,
        "leakage_guard": leakage,
        "train_counts_after_guard": dict(class_counts),
        "validation_counts": dict(Counter(row["label"] for row in splits["dev"])),
        "test_counts": dict(Counter(row["label"] for row in splits["test"])),
        "sampling": "WeightedRandomSampler; inverse class frequency, replacement=True; one epoch has the original train row count",
        "loss": "CrossEntropyLoss (unweighted; balancing is by sampler)",
        "optimizer": "AdamW",
        "learning_rate": args.learning_rate,
        "weight_decay": args.weight_decay,
        "warmup_ratio": args.warmup_ratio,
        "batch_size_per_device": args.batch_size,
        "gradient_accumulation_steps": args.grad_accum,
        "effective_batch_size": args.batch_size * args.grad_accum,
        "max_epochs": args.epochs,
        "epochs_run": len(history),
        "best_epoch": best_epoch,
        "early_stopping": {"metric": "validation macro F1", "patience": args.patience},
        "seed": args.seed,
        "fp16": bool(args.fp16 and device.type == "cuda"),
        "device": str(device),
        "gpu": torch.cuda.get_device_name(0) if device.type == "cuda" else None,
        "duration_seconds": duration_seconds,
        "validation_history": history,
        "original_test_metrics": test_metrics,
        "class_weighting": False,
        "runtime_versions": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "transformers": __import__("transformers").__version__,
            "huggingface_hub": __import__("huggingface_hub").__version__,
            "scikit_learn": __import__("sklearn").__version__,
            "numpy": np.__version__,
            "cuda_runtime": torch.version.cuda,
        },
    }
    (output_dir / "training_metadata.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"best_epoch={best_epoch} original_test_accuracy={test_metrics['accuracy']:.4f} original_test_macro_f1={test_metrics['macro_f1']:.4f}")
    return config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "models" / "phase_3_v2")
    parser.add_argument("--data-cache", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--grad-accum", type=int, default=2)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.06)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fp16", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--allow-cpu", action="store_true")
    args = parser.parse_args()
    if args.data_cache is None:
        from training.phase_3_v2.dataset import DEFAULT_CACHE
        args.data_cache = DEFAULT_CACHE
    if args.batch_size < 1 or args.grad_accum < 1 or args.epochs < 1:
        parser.error("batch-size, grad-accum, and epochs must be positive")
    train(args)


if __name__ == "__main__":
    main()
