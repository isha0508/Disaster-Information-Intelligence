"""
training/train_relevance.py
============================
Humanitarian Relevance Classifier — TF-IDF + Logistic Regression baseline.

Answers: "Does this tweet contain humanitarian/disaster-related information?"

IMPORTANT LABELLING NOTE
--------------------------
HumAID is NOT a pure binary disaster/non-disaster dataset.
  - NOT_HUMANITARIAN (0) = "not_humanitarian" class only
  - HUMANITARIAN     (1) = all 9 other HumAID classes

This is a HUMANITARIAN RELEVANCE classifier, not a general disaster detector.
The "not_humanitarian" class contains topically disaster-adjacent tweets that
were judged not to meet the humanitarian information threshold.  This produces
class imbalance: ~8.2% negative vs ~91.8% positive in train.

Run:
    python training/train_relevance.py
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
from preprocessing.humaid_loader import load_humaid, RELEVANCE_MAP, RELEVANCE_NAMES

MODEL_DIR   = ROOT / "models" / "relevance"
RESULTS_DIR = ROOT / "evaluation" / "results" / "relevance"
FIGURES_DIR = ROOT / "evaluation" / "figures" / "relevance"
for d in (MODEL_DIR, RESULTS_DIR, FIGURES_DIR):
    d.mkdir(parents=True, exist_ok=True)

RANDOM_STATE = 42
TARGET_NAMES = ["not_humanitarian", "humanitarian"]

print("=" * 65)
print("Humanitarian Relevance Classifier — TF-IDF + LR")
print("=" * 65)

# ── Load ──────────────────────────────────────────────────────────────────────
print("\n[1] Loading HumAID ...")
data = load_humaid()

print("\n[2] Mapping to binary relevance labels ...")
for split, df in data.items():
    df["relevance"] = df["class_label"].map(RELEVANCE_MAP)
    counts = df["relevance"].value_counts()
    total  = len(df)
    print(f"    {split}: HUM={counts.get(1,0):,} ({100*counts.get(1,0)/total:.1f}%)  "
          f"NOT={counts.get(0,0):,} ({100*counts.get(0,0)/total:.1f}%)")

# ── Preprocess ────────────────────────────────────────────────────────────────
print("\n[3] Cleaning text ...")
for split in data:
    data[split]["clean"] = data[split]["tweet_text"].apply(clean_text)

X_train = data["train"]["clean"].tolist()
y_train = data["train"]["relevance"].tolist()
X_val   = data["validation"]["clean"].tolist()
y_val   = data["validation"]["relevance"].tolist()
X_test  = data["test"]["clean"].tolist()
y_test  = data["test"]["relevance"].tolist()

# ── Fit ───────────────────────────────────────────────────────────────────────
print("\n[4] Fitting TF-IDF + Logistic Regression ...")
t0 = time.time()
pipeline = Pipeline([
    ("tfidf", TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=50_000,
        sublinear_tf=True,
        min_df=2,
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

# ── Evaluate ──────────────────────────────────────────────────────────────────
def evaluate(X, y_true, split_name):
    y_pred = pipeline.predict(X)
    results = {
        "split":           split_name,
        "accuracy":        float(accuracy_score(y_true, y_pred)),
        "macro_f1":        float(f1_score(y_true, y_pred, average="macro",    zero_division=0)),
        "weighted_f1":     float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
        "macro_precision": float(precision_score(y_true, y_pred, average="macro",    zero_division=0)),
        "macro_recall":    float(recall_score(   y_true, y_pred, average="macro",    zero_division=0)),
        "report":          classification_report(y_true, y_pred,
                               target_names=TARGET_NAMES, zero_division=0),
        "confusion_matrix": confusion_matrix(y_true, y_pred).tolist(),
    }
    print(f"\n  [{split_name}]  Accuracy={results['accuracy']:.4f}  "
          f"Macro-F1={results['macro_f1']:.4f}  "
          f"Weighted-F1={results['weighted_f1']:.4f}")
    print(results["report"])
    return results, y_pred

print("\n── Validation ──────────────────────────────────────────────")
val_results, y_val_pred = evaluate(X_val, y_val, "validation")

print("\n── Test ────────────────────────────────────────────────────")
test_results, y_test_pred = evaluate(X_test, y_test, "test")

# ── Confusion matrix ──────────────────────────────────────────────────────────
for results, split_name in [(val_results, "validation"), (test_results, "test")]:
    cm = np.array(results["confusion_matrix"])
    fig, ax = plt.subplots(figsize=(6, 5))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                xticklabels=TARGET_NAMES, yticklabels=TARGET_NAMES, ax=ax)
    ax.set_xlabel("Predicted"); ax.set_ylabel("Actual")
    ax.set_title(f"Relevance Classifier — {split_name}")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / f"confusion_matrix_{split_name}.png", dpi=150)
    plt.close()

# ── Save ──────────────────────────────────────────────────────────────────────
joblib.dump(pipeline, MODEL_DIR / "relevance_tfidf_lr.joblib")

summary = {
    "model":       "TF-IDF(1-2gram, 50k) + LogisticRegression(balanced)",
    "task":        "humanitarian_relevance",
    "labelling":   "not_humanitarian→0, all_other_9_classes→1",
    "caveat":      "HUMANITARIAN RELEVANCE baseline. Not a general disaster detector.",
    "train_time_s": round(train_time, 2),
    "validation":  {k: v for k, v in val_results.items()
                    if k not in ("report", "confusion_matrix")},
    "test":        {k: v for k, v in test_results.items()
                    if k not in ("report", "confusion_matrix")},
}
with open(RESULTS_DIR / "relevance_results.json", "w") as f:
    json.dump(summary, f, indent=2)

print("\n" + "=" * 65)
print("RELEVANCE BASELINE COMPLETE")
print(f"  Val  Macro-F1: {val_results['macro_f1']:.4f}")
print(f"  Test Macro-F1: {test_results['macro_f1']:.4f}")
print("=" * 65)
