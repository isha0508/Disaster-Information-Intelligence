"""
training/train_transformer_humanitarian.py
============================================
Humanitarian Category Classifier — DistilBERT fine-tuning.

Model    : distilbert-base-uncased
Task     : 10-class humanitarian category classification (HumAID)
Metric   : Macro F1 (primary — severe class imbalance present)

Designed to run both:
  • Locally  (RTX 4050 Laptop, 6 GB VRAM)
  • On Kaggle (P100 / T4 / V100, output → /kaggle/working/)

──────────────────────────────────────────────────────────────
SMOKE-TEST CHECKPOINT WARNING
──────────────────────────────────────────────────────────────
models/transformer_humanitarian/best_model/
contains a SMOKE-TEST checkpoint only (200 samples, epoch 2,
val Macro F1 ≈ 0.19).  It is NOT a trained model.
Full training saves to a separate directory controlled by
--output_dir so the smoke artefacts are never overwritten.
──────────────────────────────────────────────────────────────

Local full run:
    python training/train_transformer_humanitarian.py

Kaggle full run (in a Kaggle notebook cell):
    !python training/train_transformer_humanitarian.py \\
        --output_dir /kaggle/working/distilbert_humanitarian \\
        --data_cache /kaggle/working/humaid_cache \\
        --epochs 5 \\
        --batch_size 32 \\
        --grad_accum 2 \\
        --lr 2e-5 \\
        --max_len 128 \\
        --patience 2

Smoke test:
    python training/train_transformer_humanitarian.py --smoke
"""

import sys
import json
import time
import argparse
import warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")   # must be before any other matplotlib import
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader
from torch.optim import AdamW
from transformers import (
    DistilBertTokenizerFast,
    DistilBertForSequenceClassification,
    get_linear_schedule_with_warmup,
)
from sklearn.metrics import (
    f1_score, accuracy_score, precision_score, recall_score,
    classification_report, confusion_matrix,
)
from sklearn.utils.class_weight import compute_class_weight

# ─────────────────────────────────────────────────────────────────────────────
# Path resolution — works locally AND on Kaggle
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent          # local project root

# Add project root to sys.path so local modules resolve correctly.
# On Kaggle, the user must either:
#   (a) upload the preprocessing/ package as a dataset, or
#   (b) add an inline sys.path.insert before running the script.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ─────────────────────────────────────────────────────────────────────────────
# Canonical label list (verified from downloaded HumAID dataset)
# ─────────────────────────────────────────────────────────────────────────────
HUMAID_CLASSES = [
    "caution_and_advice",
    "displaced_people_and_evacuations",
    "infrastructure_and_utility_damage",
    "injured_or_dead_people",
    "missing_or_found_people",
    "not_humanitarian",
    "other_relevant_information",
    "requests_or_urgent_needs",
    "rescue_volunteering_or_donation_effort",
    "sympathy_and_support",
]
LABEL2ID = {lbl: i for i, lbl in enumerate(HUMAID_CLASSES)}
ID2LABEL  = {i: lbl for i, lbl in enumerate(HUMAID_CLASSES)}

# ─────────────────────────────────────────────────────────────────────────────
# Argument parser — all tuneable parameters exposed as CLI args
# ─────────────────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fine-tune DistilBERT on HumAID humanitarian category classification.",
    )

    # ── Run mode ──────────────────────────────────────────────────────────────
    p.add_argument(
        "--smoke", action="store_true",
        help="Smoke test: 200 train / 100 val / 100 test samples, 2 epochs. "
             "Results saved as *_smoke.json. Does NOT overwrite full-run artefacts.",
    )

    # ── Data ──────────────────────────────────────────────────────────────────
    p.add_argument(
        "--data_cache", type=str, default=None,
        help="Path to the HuggingFace dataset cache directory. "
             "Defaults to <project_root>/data/raw/humaid/ locally. "
             "Set to /kaggle/working/humaid_cache on Kaggle.",
    )

    # ── Output ────────────────────────────────────────────────────────────────
    p.add_argument(
        "--output_dir", type=str, default=None,
        help="Root directory for model checkpoints and results. "
             "Defaults to <project_root>/models/transformer_humanitarian/full_run/ locally. "
             "Set to /kaggle/working/distilbert_humanitarian on Kaggle.",
    )

    # ── Model ─────────────────────────────────────────────────────────────────
    p.add_argument(
        "--pretrained", type=str, default="distilbert-base-uncased",
        help="HuggingFace model identifier.",
    )

    # ── Training hyperparameters ──────────────────────────────────────────────
    p.add_argument("--epochs",      type=int,   default=5,    help="Maximum training epochs.")
    p.add_argument("--batch_size",  type=int,   default=32,   help="Per-device batch size.")
    p.add_argument("--grad_accum",  type=int,   default=2,    help="Gradient accumulation steps (effective batch = batch_size × grad_accum).")
    p.add_argument("--lr",          type=float, default=2e-5, help="Peak learning rate for AdamW.")
    p.add_argument("--max_len",     type=int,   default=128,  help="Token sequence length (128 covers over 95 pct of HumAID tweets).")
    p.add_argument("--warmup_ratio",type=float, default=0.06, help="Fraction of total steps used for linear warmup.")
    p.add_argument("--weight_decay",type=float, default=0.01, help="AdamW weight decay.")
    p.add_argument("--patience",    type=int,   default=2,    help="Early-stopping patience (epochs without val Macro F1 improvement).")
    p.add_argument("--seed",        type=int,   default=42,   help="Random seed for reproducibility.")

    return p


# ─────────────────────────────────────────────────────────────────────────────
# Data loading — supports both local cache and fresh HF download
# ─────────────────────────────────────────────────────────────────────────────
def load_data(cache_dir: Path):
    """
    Load HumAID splits.  Uses the project's humaid_loader if available,
    otherwise falls back to a direct load_dataset call (Kaggle path).
    """
    try:
        from preprocessing.humaid_loader import load_humaid
        data = load_humaid(cache_dir=cache_dir)
        print("  [loader] Used preprocessing.humaid_loader")
    except ImportError:
        # Kaggle fallback — preprocessing package not on path
        from datasets import load_dataset
        ds = load_dataset(
            "QCRI/HumAID-all",
            cache_dir=str(cache_dir),
            verification_mode="no_checks",
        )
        import pandas as pd
        features = ds["train"].features
        data = {}
        for split in ("train", "validation", "test"):
            df = ds[split].to_pandas()
            if hasattr(features["class_label"], "names"):
                names = features["class_label"].names
                df["class_label"] = df["class_label"].apply(
                    lambda x: names[x] if isinstance(x, int) else x
                )
            data[split] = df[["tweet_text", "class_label"]].copy()
        print("  [loader] Used direct load_dataset (fallback)")
    return data


def clean(text) -> str:
    """Clean a tweet. Uses project cleaner if available, else minimal fallback."""
    try:
        from preprocessing.text_cleaner import clean_text
        return clean_text(text)
    except ImportError:
        import re, unicodedata
        if not isinstance(text, str):
            return ""
        text = unicodedata.normalize("NFKC", text)
        text = re.sub(r"https?://\S+|www\.\S+", "", text)
        text = re.sub(r"@\w+:?", "", text)
        text = re.sub(r"#(\w+)", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip().lower()
        return text


# ─────────────────────────────────────────────────────────────────────────────
# PyTorch Dataset
# ─────────────────────────────────────────────────────────────────────────────
class TweetDataset(Dataset):
    def __init__(self, texts: list, labels: list, tokenizer, max_len: int):
        self.encodings = tokenizer(
            texts,
            truncation=True,
            padding="max_length",
            max_length=max_len,
            return_tensors="pt",
        )
        self.labels = torch.tensor(labels, dtype=torch.long)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return {
            "input_ids":      self.encodings["input_ids"][idx],
            "attention_mask": self.encodings["attention_mask"][idx],
            "labels":         self.labels[idx],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_model(model, loader, device, class_weights_tensor) -> dict:
    model.eval()
    all_preds, all_labels = [], []
    total_loss = 0.0
    loss_fn = torch.nn.CrossEntropyLoss(weight=class_weights_tensor)

    with torch.no_grad():
        for batch in loader:
            input_ids      = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels         = batch["labels"].to(device)
            outputs        = model(input_ids=input_ids, attention_mask=attention_mask)
            loss           = loss_fn(outputs.logits, labels)
            total_loss    += loss.item()
            preds          = outputs.logits.argmax(dim=-1).cpu().numpy()
            all_preds.extend(preds)
            all_labels.extend(labels.cpu().numpy())

    return {
        "loss":        total_loss / len(loader),
        "accuracy":    float(accuracy_score(all_labels, all_preds)),
        "macro_f1":    float(f1_score(all_labels, all_preds, average="macro",    zero_division=0)),
        "weighted_f1": float(f1_score(all_labels, all_preds, average="weighted", zero_division=0)),
        "macro_precision": float(precision_score(all_labels, all_preds, average="macro", zero_division=0)),
        "macro_recall":    float(recall_score(   all_labels, all_preds, average="macro", zero_division=0)),
        "preds":  all_preds,
        "labels": all_labels,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Figure helpers
# ─────────────────────────────────────────────────────────────────────────────
def save_training_curves(history: list, best_epoch: int, figures_dir: Path):
    epochs_done = [r["epoch"] for r in history]
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    axes[0].plot(epochs_done, [r["train_loss"] for r in history],
                 marker="o", label="Train loss")
    axes[0].plot(epochs_done, [r["val_loss"]   for r in history],
                 marker="s", label="Val loss")
    axes[0].axvline(best_epoch, color="gray", linestyle="--",
                    linewidth=0.8, label=f"best epoch={best_epoch}")
    axes[0].set_xlabel("Epoch"); axes[0].set_ylabel("Loss")
    axes[0].set_title("Training & Validation Loss"); axes[0].legend()

    axes[1].plot(epochs_done, [r["val_macro_f1"]    for r in history],
                 marker="o", label="Val Macro F1")
    axes[1].plot(epochs_done, [r["val_weighted_f1"] for r in history],
                 marker="s", label="Val Weighted F1")
    axes[1].axvline(best_epoch, color="gray", linestyle="--", linewidth=0.8)
    axes[1].set_xlabel("Epoch"); axes[1].set_ylabel("F1")
    axes[1].set_title("Validation F1 Scores"); axes[1].legend()

    plt.suptitle("DistilBERT Humanitarian Classifier — Training History",
                 fontsize=13, fontweight="bold")
    plt.tight_layout()
    path = figures_dir / "training_curves.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {path}")


def save_confusion_matrix(labels, preds, figures_dir: Path, title_suffix: str = ""):
    cm = confusion_matrix(labels, preds, labels=list(range(len(HUMAID_CLASSES))))
    short = [l.replace("_", "\n") for l in HUMAID_CLASSES]
    fig, ax = plt.subplots(figsize=(14, 11))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                xticklabels=short, yticklabels=short, ax=ax,
                annot_kws={"size": 8})
    ax.set_xlabel("Predicted"); ax.set_ylabel("Actual")
    ax.set_title(f"DistilBERT Humanitarian Classifier — Test Confusion Matrix{title_suffix}")
    plt.xticks(fontsize=7, rotation=45, ha="right")
    plt.yticks(fontsize=7, rotation=0)
    plt.tight_layout()
    path = figures_dir / "confusion_matrix_test.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {path}")


def save_per_class_f1_chart(labels, preds, figures_dir: Path):
    per_class = f1_score(
        labels, preds, average=None,
        labels=list(range(len(HUMAID_CLASSES))), zero_division=0,
    )
    short = [l.replace("_", "\n") for l in HUMAID_CLASSES]
    colors = ["#d62728" if f < 0.5 else "#ff7f0e" if f < 0.7 else "#2ca02c"
              for f in per_class]
    fig, ax = plt.subplots(figsize=(13, 5))
    ax.bar(range(len(HUMAID_CLASSES)), per_class, color=colors)
    ax.set_xticks(range(len(HUMAID_CLASSES)))
    ax.set_xticklabels(short, fontsize=7, rotation=45, ha="right")
    ax.set_ylabel("F1 Score"); ax.set_ylim(0, 1.05)
    ax.axhline(0.5, color="gray", linestyle="--", linewidth=0.8)
    ax.set_title("DistilBERT — Per-Class F1 (Test Split)")
    for i, f in enumerate(per_class):
        ax.text(i, f + 0.02, f"{f:.2f}", ha="center", fontsize=8)
    plt.tight_layout()
    path = figures_dir / "per_class_f1_test.png"
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"  Saved: {path}")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    args = build_parser().parse_args()

    # ── Reproducibility ───────────────────────────────────────────────────────
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    # ── Resolve paths ─────────────────────────────────────────────────────────
    if args.data_cache:
        data_cache = Path(args.data_cache)
    else:
        data_cache = PROJECT_ROOT / "data" / "raw" / "humaid"

    if args.output_dir:
        output_root = Path(args.output_dir)
    else:
        # Local default: separate subdir from smoke-test artefacts
        output_root = PROJECT_ROOT / "models" / "transformer_humanitarian" / "full_run"

    # For smoke test, write to a clearly labelled subdir
    if args.smoke:
        output_root = output_root.parent / "smoke_run"

    checkpoint_dir = output_root / "best_model"
    results_dir    = output_root / "results"
    figures_dir    = output_root / "figures"
    for d in (checkpoint_dir, results_dir, figures_dir):
        d.mkdir(parents=True, exist_ok=True)

    # ── Device ────────────────────────────────────────────────────────────────
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    print("=" * 65)
    print("Humanitarian Transformer — DistilBERT Fine-tuning")
    print("=" * 65)
    print(f"  Pretrained  : {args.pretrained}")
    print(f"  Device      : {device}")
    if device.type == "cuda":
        print(f"  GPU         : {torch.cuda.get_device_name(0)}")
        free_vram = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"  VRAM        : {free_vram:.1f} GB")
    print(f"  Smoke test  : {args.smoke}")
    print(f"  Output root : {output_root}")
    print(f"  Data cache  : {data_cache}")
    print(f"  Epochs      : {args.epochs}")
    print(f"  Batch size  : {args.batch_size}  (grad_accum={args.grad_accum}  "
          f"→ effective={args.batch_size * args.grad_accum})")
    print(f"  LR          : {args.lr}")
    print(f"  Max length  : {args.max_len}")
    print(f"  Patience    : {args.patience}")
    print()

    # ── Load data ─────────────────────────────────────────────────────────────
    print("[1] Loading HumAID ...")
    data = load_data(data_cache)

    if args.smoke:
        smoke_n = {"train": 200, "validation": 100, "test": 100}
        print(f"  [SMOKE] Subsetting to {smoke_n} samples ...")
        for split, n in smoke_n.items():
            data[split] = data[split].sample(n, random_state=args.seed)

    for split, df in data.items():
        print(f"  {split:12s}: {len(df):,} rows")

    # ── Text cleaning ─────────────────────────────────────────────────────────
    print("\n[2] Cleaning text ...")
    for split in data:
        data[split] = data[split].copy()
        data[split]["clean"]    = data[split]["tweet_text"].apply(clean)
        data[split]["label_id"] = data[split]["class_label"].map(LABEL2ID)

    # Validate — catch label mapping failures early
    for split in data:
        nulls = data[split]["label_id"].isnull().sum()
        if nulls > 0:
            unknown = data[split].loc[data[split]["label_id"].isnull(),
                                      "class_label"].unique()
            raise ValueError(f"[{split}] {nulls} unmapped labels: {unknown}")

    # ── Class weights ─────────────────────────────────────────────────────────
    train_label_ids  = data["train"]["label_id"].tolist()
    class_weights    = compute_class_weight(
        "balanced",
        classes=np.arange(len(HUMAID_CLASSES)),
        y=train_label_ids,
    )
    class_weights_t  = torch.tensor(class_weights, dtype=torch.float).to(device)
    print(f"\n  Class weights: min={class_weights.min():.2f}  "
          f"max={class_weights.max():.2f}  "
          f"ratio={class_weights.max()/class_weights.min():.1f}x")

    # ── Tokenise ──────────────────────────────────────────────────────────────
    print(f"\n[3] Tokenising (max_len={args.max_len}) ...")
    warnings.filterwarnings("ignore", category=FutureWarning)
    tokenizer = DistilBertTokenizerFast.from_pretrained(args.pretrained)

    def make_loader(split, shuffle):
        ds = TweetDataset(
            data[split]["clean"].tolist(),
            data[split]["label_id"].tolist(),
            tokenizer, args.max_len,
        )
        return DataLoader(ds, batch_size=args.batch_size,
                          shuffle=shuffle, num_workers=0, pin_memory=(device.type=="cuda"))

    train_loader = make_loader("train",      shuffle=True)
    val_loader   = make_loader("validation", shuffle=False)
    test_loader  = make_loader("test",       shuffle=False)

    # ── Model ─────────────────────────────────────────────────────────────────
    print(f"\n[4] Loading {args.pretrained} ...")
    warnings.filterwarnings("ignore")
    model = DistilBertForSequenceClassification.from_pretrained(
        args.pretrained,
        num_labels=len(HUMAID_CLASSES),
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    )
    model.to(device)
    warnings.resetwarnings()

    n_params     = sum(p.numel() for p in model.parameters())
    n_trainable  = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"  Parameters  : {n_params/1e6:.1f}M total  {n_trainable/1e6:.1f}M trainable")

    # ── Optimiser + scheduler ─────────────────────────────────────────────────
    optimizer    = AdamW(model.parameters(), lr=args.lr,
                         weight_decay=args.weight_decay)
    total_opt_steps = (len(train_loader) // args.grad_accum) * args.epochs
    warmup_steps    = int(total_opt_steps * args.warmup_ratio)
    scheduler       = get_linear_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_opt_steps,
    )
    print(f"  Optimiser steps : {total_opt_steps}  warmup={warmup_steps}")

    # ── Training loop ─────────────────────────────────────────────────────────
    print(f"\n[5] Training (max {args.epochs} epochs, patience={args.patience}) ...")
    history    = []
    best_f1    = 0.0
    best_epoch = 0
    no_improve = 0
    loss_fn    = torch.nn.CrossEntropyLoss(weight=class_weights_t)
    t_start    = time.time()

    for epoch in range(1, args.epochs + 1):
        model.train()
        epoch_loss = 0.0
        optimizer.zero_grad()

        for step, batch in enumerate(train_loader):
            input_ids      = batch["input_ids"].to(device)
            attention_mask = batch["attention_mask"].to(device)
            labels_b       = batch["labels"].to(device)

            outputs = model(input_ids=input_ids, attention_mask=attention_mask)
            loss    = loss_fn(outputs.logits, labels_b) / args.grad_accum
            loss.backward()
            epoch_loss += loss.item() * args.grad_accum

            if (step + 1) % args.grad_accum == 0 or (step + 1) == len(train_loader):
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()

        avg_train_loss = epoch_loss / len(train_loader)
        val_metrics    = evaluate_model(model, val_loader, device, class_weights_t)

        row = {
            "epoch":           epoch,
            "train_loss":      round(avg_train_loss,              4),
            "val_loss":        round(val_metrics["loss"],         4),
            "val_accuracy":    round(val_metrics["accuracy"],     4),
            "val_macro_f1":    round(val_metrics["macro_f1"],     4),
            "val_weighted_f1": round(val_metrics["weighted_f1"],  4),
            "val_macro_precision": round(val_metrics["macro_precision"], 4),
            "val_macro_recall":    round(val_metrics["macro_recall"],    4),
        }
        history.append(row)

        elapsed = (time.time() - t_start) / 60
        print(f"  Epoch {epoch}/{args.epochs}  "
              f"train_loss={row['train_loss']:.4f}  "
              f"val_loss={row['val_loss']:.4f}  "
              f"val_macro_f1={row['val_macro_f1']:.4f}  "
              f"val_acc={row['val_accuracy']:.4f}  "
              f"[{elapsed:.1f} min]")

        # Best checkpoint
        if val_metrics["macro_f1"] > best_f1:
            best_f1    = val_metrics["macro_f1"]
            best_epoch = epoch
            no_improve = 0
            model.save_pretrained(checkpoint_dir)
            tokenizer.save_pretrained(checkpoint_dir)
            print(f"    ✓ New best  val Macro F1={best_f1:.4f}  → saved checkpoint")
        else:
            no_improve += 1
            print(f"    · No improvement ({no_improve}/{args.patience})")
            if no_improve >= args.patience:
                print(f"\n  Early stopping triggered at epoch {epoch}.")
                break

    total_time = time.time() - t_start
    print(f"\n  Training time : {total_time/60:.1f} min")
    print(f"  Best epoch    : {best_epoch}  val Macro F1={best_f1:.4f}")

    # ── Test evaluation — load best checkpoint ────────────────────────────────
    print("\n[6] Loading best checkpoint for test evaluation ...")
    best_model = DistilBertForSequenceClassification.from_pretrained(checkpoint_dir)
    best_model.to(device)
    test_metrics = evaluate_model(best_model, test_loader, device, class_weights_t)

    print(f"\n  Test Accuracy    : {test_metrics['accuracy']:.4f}")
    print(f"  Test Macro F1    : {test_metrics['macro_f1']:.4f}")
    print(f"  Test Weighted F1 : {test_metrics['weighted_f1']:.4f}")
    print(f"  Test Macro Prec  : {test_metrics['macro_precision']:.4f}")
    print(f"  Test Macro Rec   : {test_metrics['macro_recall']:.4f}")

    cls_report = classification_report(
        test_metrics["labels"], test_metrics["preds"],
        target_names=HUMAID_CLASSES, zero_division=0,
    )
    print("\n  Per-class report (test):")
    print(cls_report)

    # ── Figures ───────────────────────────────────────────────────────────────
    print("\n[7] Saving figures ...")
    save_training_curves(history, best_epoch, figures_dir)
    save_confusion_matrix(test_metrics["labels"], test_metrics["preds"],
                          figures_dir,
                          title_suffix=" (SMOKE)" if args.smoke else "")
    save_per_class_f1_chart(test_metrics["labels"], test_metrics["preds"],
                             figures_dir)

    # ── Per-class F1 dict ─────────────────────────────────────────────────────
    per_class_f1_arr = f1_score(
        test_metrics["labels"], test_metrics["preds"],
        average=None, labels=list(range(len(HUMAID_CLASSES))), zero_division=0,
    )
    per_class_f1 = {HUMAID_CLASSES[i]: round(float(per_class_f1_arr[i]), 4)
                    for i in range(len(HUMAID_CLASSES))}

    # ── Save training config ──────────────────────────────────────────────────
    training_config = {
        "pretrained":       args.pretrained,
        "max_len":          args.max_len,
        "batch_size":       args.batch_size,
        "grad_accum":       args.grad_accum,
        "effective_batch":  args.batch_size * args.grad_accum,
        "lr":               args.lr,
        "weight_decay":     args.weight_decay,
        "warmup_ratio":     args.warmup_ratio,
        "epochs_max":       args.epochs,
        "epochs_run":       history[-1]["epoch"],
        "best_epoch":       best_epoch,
        "patience":         args.patience,
        "seed":             args.seed,
        "smoke_test":       args.smoke,
        "class_imbalance":  "compute_class_weight(balanced) via CrossEntropyLoss weight",
        "device":           str(device),
        "gpu_name":         (torch.cuda.get_device_name(0)
                             if device.type == "cuda" else "CPU"),
    }
    (output_root / "training_config.json").write_text(
        json.dumps(training_config, indent=2), encoding="utf-8"
    )

    # ── Save full results JSON ────────────────────────────────────────────────
    results_summary = {
        "model":             args.pretrained,
        "task":              "humanitarian_category",
        "smoke_test":        args.smoke,
        "note":              ("SMOKE TEST ONLY — not a trained model"
                              if args.smoke else "FULL TRAINING RUN"),
        "training_config":   training_config,
        "train_time_min":    round(total_time / 60, 2),
        "best_val_macro_f1": round(best_f1, 4),
        "validation_history": history,
        "test": {
            "accuracy":        round(test_metrics["accuracy"],        4),
            "macro_f1":        round(test_metrics["macro_f1"],        4),
            "weighted_f1":     round(test_metrics["weighted_f1"],     4),
            "macro_precision": round(test_metrics["macro_precision"], 4),
            "macro_recall":    round(test_metrics["macro_recall"],    4),
        },
        "per_class_f1_test": per_class_f1,
        "classification_report_test": cls_report,
    }

    # Filename clearly signals smoke vs full
    out_fname = "transformer_results_smoke.json" if args.smoke else "transformer_results.json"
    out_path  = results_dir / out_fname
    out_path.write_text(json.dumps(results_summary, indent=2), encoding="utf-8")
    print(f"\nResults JSON : {out_path}")

    # Also write to the top-level evaluation/results dir for the project
    # so evaluate_transformer.py can find it regardless of output_dir
    proj_results = PROJECT_ROOT / "evaluation" / "results" / "transformer_humanitarian"
    proj_results.mkdir(parents=True, exist_ok=True)
    proj_out = proj_results / out_fname
    proj_out.write_text(json.dumps(results_summary, indent=2), encoding="utf-8")
    print(f"Project copy : {proj_out}")

    # Copy figures to project evaluation dir too
    import shutil
    proj_figs = PROJECT_ROOT / "evaluation" / "figures" / "transformer_humanitarian"
    proj_figs.mkdir(parents=True, exist_ok=True)
    for fig_file in figures_dir.glob("*.png"):
        dst = proj_figs / fig_file.name
        shutil.copy2(fig_file, dst)
    print(f"Figures copy : {proj_figs}")

    print("\n" + "=" * 65)
    print("TRANSFORMER TRAINING COMPLETE")
    print(f"  Smoke test      : {args.smoke}")
    print(f"  Best val Macro F1: {best_f1:.4f}  (epoch {best_epoch})")
    print(f"  Test Macro F1   : {test_metrics['macro_f1']:.4f}")
    print(f"  Test Accuracy   : {test_metrics['accuracy']:.4f}")
    print(f"  Test Weighted F1: {test_metrics['weighted_f1']:.4f}")
    print(f"  Checkpoint      : {checkpoint_dir}")
    print(f"  Results JSON    : {out_path}")
    print("=" * 65)


if __name__ == "__main__":
    main()
