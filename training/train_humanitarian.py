"""
training/train_humanitarian.py
================================
Humanitarian Category Classifier — TF-IDF + Logistic Regression baseline.

Task: Classify tweets into 10 HumAID humanitarian categories.
Primary metric: Macro F1 (due to severe class imbalance).

Run:
    python training/train_humanitarian.py
"""

import sys
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score,
    classification_report, confusion_matrix,
)
from sklearn.pipeline import Pipeline

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from preprocessing.text_cleaner import clean_text
from preprocessing.humaid_loader import load_humaid, HUMAID_CLASSES

MODEL_DIR   = ROOT / "models" / "humanitarian"
RESULTS_DIR = ROOT / "evaluation" / "results" / "humanitarian"
FIGURES_DIR = ROOT / "evaluation" / "figures" / "humanitarian"
for d in (MODEL_DIR, RESULTS_DIR, FIGURES_DIR):
    d.mkdir(parents=True, exist_ok=True)

RANDOM_STATE = 42

print("=" * 65)
print("Humanitarian Category Classifier — TF-IDF + LR Baseline")
print("=" * 65)

# ── Load data ─────────────────────────────────────────────────────────────────
print("\n[1] Loading HumAID ...")
data = load_humaid()
for split, df in data.items():
    print(f"    {split}: {len(df):,} rows")

# ── Preprocess ────────────────────────────────────────────────────────────────
print("\n[2] Cleaning text ...")
for split in data:
    data[split]["clean"] = data[split]["tweet_text"].apply(clean_text)

X_train = data["train"]["clean"].tolist()
y_train = data["train"]["class_label"].tolist()
X_val   = data["validation"]["clean"].tolist()
y_val   = data["validation"]["class_label"].tolist()
X_test  = data["test"]["clean"].tolist()
y_test  = data["test"]["class_label"].tolist()

# ── Build and fit pipeline ────────────────────────────────────────────────────
print("\n[3] Fitting TF-IDF + Logistic Regression ...")
t0 = time.time()

pipeline = Pipeline([
    ("tfidf", TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=80_000,
        sublinear_tf=True,
        min_df=2,
        analyzer="word",
    )),
    ("clf", LogisticRegression(
        class_weight="balanced",
        max_iter=1000,
        C=1.0,
        solver="lbfgs",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )),
])

pipeline.fit(X_train, y_train)
train_time = time.time() - t0
print(f"    Training time: {train_time:.1f}s")

# ── Evaluation helper ─────────────────────────────────────────────────────────
def evaluate(pipeline, X, y_true, split_name, label_names):
    y_pred = pipeline.predict(X)
    y_prob = pipeline.predict_proba(X)

    results = {
        "split":           split_name,
        "accuracy":        float(accuracy_score(y_true, y_pred)),
        "macro_precision": float(precision_score(y_true, y_pred, average="macro",    zero_division=0)),
        "macro_recall":    float(recall_score(   y_true, y_pred, average="macro",    zero_division=0)),
        "macro_f1":        float(f1_score(       y_true, y_pred, average="macro",    zero_division=0)),
        "weighted_f1":     float(f1_score(       y_true, y_pred, average="weighted", zero_division=0)),
        "report":          classification_report(y_true, y_pred,
                               labels=label_names, zero_division=0),
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=label_names).tolist(),
    }
    print(f"\n  [{split_name}]  Accuracy={results['accuracy']:.4f}  "
          f"Macro-F1={results['macro_f1']:.4f}  "
          f"Weighted-F1={results['weighted_f1']:.4f}")
    print(results["report"])
    return results, y_pred, y_prob

print("\n[4] Evaluating on validation split ...")
val_results, y_val_pred, y_val_prob = evaluate(
    pipeline, X_val, y_val, "validation", HUMAID_CLASSES)

print("\n[5] Evaluating on test split ...")
test_results, y_test_pred, y_test_prob = evaluate(
    pipeline, X_test, y_test, "test", HUMAID_CLASSES)

# ── Confusion matrix figures ──────────────────────────────────────────────────
short_labels = [l.replace("_", "\n") for l in HUMAID_CLASSES]

for results, split_name in [(val_results, "validation"), (test_results, "test")]:
    cm = np.array(results["confusion_matrix"])
    fig, ax = plt.subplots(figsize=(14, 11))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                xticklabels=short_labels, yticklabels=short_labels, ax=ax,
                annot_kws={"size": 8})
    ax.set_xlabel("Predicted", fontsize=11)
    ax.set_ylabel("Actual", fontsize=11)
    ax.set_title(f"Humanitarian Classifier (TF-IDF+LR) — {split_name} Confusion Matrix",
                 fontsize=12)
    plt.xticks(fontsize=7, rotation=45, ha="right")
    plt.yticks(fontsize=7, rotation=0)
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / f"confusion_matrix_{split_name}.png", dpi=150,
                bbox_inches="tight")
    plt.close()

# ── Per-class F1 bar chart ────────────────────────────────────────────────────
from sklearn.metrics import f1_score as f1_per
test_f1s = f1_score(y_test, y_test_pred, average=None, labels=HUMAID_CLASSES,
                    zero_division=0)
fig, ax = plt.subplots(figsize=(12, 5))
colors = ["#d62728" if f < 0.5 else "#2ca02c" for f in test_f1s]
ax.bar(range(len(HUMAID_CLASSES)), test_f1s, color=colors)
ax.set_xticks(range(len(HUMAID_CLASSES)))
ax.set_xticklabels(short_labels, fontsize=8, rotation=45, ha="right")
ax.set_ylabel("F1 Score")
ax.set_ylim(0, 1.05)
ax.set_title("Humanitarian Classifier — Per-Class F1 (Test Split)")
ax.axhline(0.5, color="gray", linestyle="--", linewidth=0.8)
for i, f in enumerate(test_f1s):
    ax.text(i, f + 0.02, f"{f:.2f}", ha="center", fontsize=8)
plt.tight_layout()
plt.savefig(FIGURES_DIR / "per_class_f1_test.png", dpi=150, bbox_inches="tight")
plt.close()

# ── Save model ────────────────────────────────────────────────────────────────
model_path = MODEL_DIR / "humanitarian_tfidf_lr.joblib"
joblib.dump(pipeline, model_path)
print(f"\nModel saved: {model_path}")

# ── Save predictions for error analysis ──────────────────────────────────────
test_df = data["test"].copy()
test_df["predicted"]  = y_test_pred
test_df["correct"]    = test_df["class_label"] == test_df["predicted"]
test_df["confidence"] = [float(np.max(p)) for p in y_test_prob]
test_df.drop(columns=["clean"], inplace=True, errors="ignore")
test_df.to_csv(RESULTS_DIR / "test_predictions.csv", index=False, encoding="utf-8")

# ── Save results JSON ─────────────────────────────────────────────────────────
per_class_f1 = {lbl: float(f) for lbl, f in zip(HUMAID_CLASSES, test_f1s)}
summary = {
    "model":       "TF-IDF(1-2gram, 80k) + LogisticRegression(balanced)",
    "task":        "humanitarian_category",
    "train_time_s": round(train_time, 2),
    "tfidf_params": {"ngram_range": [1,2], "max_features": 80000,
                     "sublinear_tf": True, "min_df": 2},
    "lr_params":    {"C": 1.0, "class_weight": "balanced",
                     "multi_class": "multinomial", "max_iter": 1000},
    "validation":  {k: v for k, v in val_results.items()
                    if k not in ("report", "confusion_matrix")},
    "test":        {k: v for k, v in test_results.items()
                    if k not in ("report", "confusion_matrix")},
    "per_class_f1_test": per_class_f1,
}
with open(RESULTS_DIR / "humanitarian_results.json", "w") as f:
    json.dump(summary, f, indent=2)

print("\n" + "=" * 65)
print("HUMANITARIAN BASELINE COMPLETE")
print(f"  Val  Macro-F1   : {val_results['macro_f1']:.4f}")
print(f"  Test Macro-F1   : {test_results['macro_f1']:.4f}")
print(f"  Test Accuracy   : {test_results['accuracy']:.4f}")
print(f"  Test Weighted-F1: {test_results['weighted_f1']:.4f}")
print("=" * 65)
