"""
evaluation/evaluate_relevance.py
==================================
Load and evaluate the saved relevance classifier.

Run:
    python evaluation/evaluate_relevance.py
"""

import sys
import json
from pathlib import Path

import numpy as np
import joblib
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
from preprocessing.humaid_loader import load_humaid, RELEVANCE_MAP

RESULTS_DIR = ROOT / "evaluation" / "results" / "relevance"
FIGURES_DIR = ROOT / "evaluation" / "figures" / "relevance"
MODEL_PATH  = ROOT / "models" / "relevance" / "relevance_tfidf_lr.joblib"
TARGET_NAMES = ["not_humanitarian", "humanitarian"]

if not MODEL_PATH.exists():
    print(f"Model not found: {MODEL_PATH}")
    print("Run: python training/train_relevance.py")
    sys.exit(1)

print("Loading relevance classifier ...")
pipeline = joblib.load(MODEL_PATH)
data = load_humaid()

for split_name, df in data.items():
    df["clean"]     = df["tweet_text"].apply(clean_text)
    df["relevance"] = df["class_label"].map(RELEVANCE_MAP)
    X      = df["clean"].tolist()
    y_true = df["relevance"].tolist()
    y_pred = pipeline.predict(X)

    print(f"\n── {split_name} ──")
    print(f"  Accuracy  : {accuracy_score(y_true, y_pred):.4f}")
    print(f"  Macro F1  : {f1_score(y_true, y_pred, average='macro', zero_division=0):.4f}")
    print(f"  Weighted F1: {f1_score(y_true, y_pred, average='weighted', zero_division=0):.4f}")
    print(classification_report(y_true, y_pred, target_names=TARGET_NAMES, zero_division=0))

    cm = confusion_matrix(y_true, y_pred)
    fig, ax = plt.subplots(figsize=(6, 5))
    sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
                xticklabels=TARGET_NAMES, yticklabels=TARGET_NAMES, ax=ax)
    ax.set_xlabel("Predicted"); ax.set_ylabel("Actual")
    ax.set_title(f"Relevance Classifier — {split_name}")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / f"confusion_{split_name}.png", dpi=150)
    plt.close()

print("\nDone.")
