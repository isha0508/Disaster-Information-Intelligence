"""
evaluation/evaluate_humanitarian.py
=====================================
Load and evaluate a saved humanitarian category classifier.
Supports both TF-IDF+LR (joblib) and DistilBERT (HuggingFace) models.

Run:
    python evaluation/evaluate_humanitarian.py --model tfidf
    python evaluation/evaluate_humanitarian.py --model transformer
"""

import sys
import json
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.metrics import (
    accuracy_score, f1_score, precision_score, recall_score,
    classification_report, confusion_matrix,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from preprocessing.text_cleaner import clean_text
from preprocessing.humaid_loader import load_humaid, HUMAID_CLASSES

RESULTS_DIR = ROOT / "evaluation" / "results" / "humanitarian"
FIGURES_DIR = ROOT / "evaluation" / "figures" / "humanitarian"

parser = argparse.ArgumentParser()
parser.add_argument("--model", choices=["tfidf", "transformer"], default="tfidf")
parser.add_argument("--split", default="test")
args = parser.parse_args()

print(f"Evaluating humanitarian classifier [{args.model}] on {args.split} split ...")

data  = load_humaid()
split = data[args.split]
split["clean"] = split["tweet_text"].apply(clean_text)
X    = split["clean"].tolist()
y_true = split["class_label"].tolist()

if args.model == "tfidf":
    import joblib
    model_path = ROOT / "models" / "humanitarian" / "humanitarian_tfidf_lr.joblib"
    if not model_path.exists():
        print(f"ERROR: model not found at {model_path}")
        print("Run: python training/train_humanitarian.py")
        sys.exit(1)
    pipeline = joblib.load(model_path)
    y_pred   = pipeline.predict(X)
    y_prob   = pipeline.predict_proba(X)

elif args.model == "transformer":
    import torch
    from torch.utils.data import Dataset, DataLoader
    from transformers import DistilBertTokenizerFast, DistilBertForSequenceClassification

    LABEL2ID = {lbl: i for i, lbl in enumerate(HUMAID_CLASSES)}
    ID2LABEL = {i: lbl for i, lbl in enumerate(HUMAID_CLASSES)}
    model_dir = ROOT / "models" / "transformer_humanitarian" / "best_model"
    if not model_dir.exists():
        print(f"ERROR: model not found at {model_dir}")
        print("Run: python training/train_transformer_humanitarian.py")
        sys.exit(1)

    device    = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = DistilBertTokenizerFast.from_pretrained(model_dir)
    model     = DistilBertForSequenceClassification.from_pretrained(model_dir)
    model.to(device)
    model.eval()

    class SimpleDataset(Dataset):
        def __init__(self, texts, tokenizer, max_len=128):
            self.enc = tokenizer(texts, truncation=True, padding="max_length",
                                 max_length=max_len, return_tensors="pt")
        def __len__(self): return self.enc["input_ids"].shape[0]
        def __getitem__(self, i):
            return {"input_ids": self.enc["input_ids"][i],
                    "attention_mask": self.enc["attention_mask"][i]}

    ds     = SimpleDataset(X, tokenizer)
    loader = DataLoader(ds, batch_size=64, shuffle=False, num_workers=0)
    all_logits = []
    with torch.no_grad():
        for batch in loader:
            out = model(input_ids=batch["input_ids"].to(device),
                        attention_mask=batch["attention_mask"].to(device))
            all_logits.append(out.logits.cpu().numpy())
    logits = np.vstack(all_logits)
    y_prob = torch.softmax(torch.tensor(logits), dim=-1).numpy()
    y_pred = [ID2LABEL[i] for i in logits.argmax(axis=1)]

# ── Metrics ───────────────────────────────────────────────────────────────────
print(f"\nAccuracy     : {accuracy_score(y_true, y_pred):.4f}")
print(f"Macro F1     : {f1_score(y_true, y_pred, average='macro', zero_division=0):.4f}")
print(f"Weighted F1  : {f1_score(y_true, y_pred, average='weighted', zero_division=0):.4f}")
print()
print(classification_report(y_true, y_pred, labels=HUMAID_CLASSES, zero_division=0))

# ── Confusion matrix ──────────────────────────────────────────────────────────
cm = confusion_matrix(y_true, y_pred, labels=HUMAID_CLASSES)
short = [l.replace("_", "\n") for l in HUMAID_CLASSES]
fig, ax = plt.subplots(figsize=(14, 11))
sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
            xticklabels=short, yticklabels=short, ax=ax, annot_kws={"size": 8})
ax.set_xlabel("Predicted"); ax.set_ylabel("Actual")
ax.set_title(f"Humanitarian [{args.model}] — {args.split} Confusion Matrix")
plt.xticks(fontsize=7, rotation=45, ha="right")
plt.yticks(fontsize=7, rotation=0)
plt.tight_layout()
fig_path = FIGURES_DIR / f"confusion_matrix_{args.model}_{args.split}.png"
plt.savefig(fig_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"Confusion matrix saved: {fig_path}")
