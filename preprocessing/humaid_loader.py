"""
preprocessing/humaid_loader.py
================================
Reusable HumAID dataset loader.

Loads QCRI/HumAID-all from local cache, decodes ClassLabel integers to
string names, and returns clean pandas DataFrames for each split.

Usage
-----
    from preprocessing.humaid_loader import load_humaid, HUMAID_CLASSES

    splits = load_humaid()          # {"train": df, "validation": df, "test": df}
    df_train = splits["train"]      # columns: tweet_text, class_label
"""

import sys
from pathlib import Path
from typing import Dict

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "raw" / "humaid"

# Canonical label list (verified from downloaded dataset)
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

# Binary relevance mapping derived from HumAID
# NOT_HUMANITARIAN = 0 (the not_humanitarian class only)
# HUMANITARIAN     = 1 (all 9 remaining classes)
# NOTE: This is a HUMANITARIAN RELEVANCE label, not a general disaster detector.
RELEVANCE_MAP = {lbl: (0 if lbl == "not_humanitarian" else 1) for lbl in HUMAID_CLASSES}
RELEVANCE_NAMES = {0: "not_humanitarian", 1: "humanitarian"}


def load_humaid(
    splits: tuple = ("train", "validation", "test"),
    cache_dir: Path = DATA_DIR,
) -> Dict[str, pd.DataFrame]:
    """
    Load HumAID-all splits from local cache.

    Parameters
    ----------
    splits : tuple of split names to load
    cache_dir : path to the local HuggingFace cache directory

    Returns
    -------
    dict mapping split name → pandas DataFrame with columns:
        tweet_text  (str)
        class_label (str, decoded to human-readable name)
    """
    from datasets import load_dataset

    ds = load_dataset(
        "QCRI/HumAID-all",
        cache_dir=str(cache_dir),
        verification_mode="no_checks",
    )

    result = {}
    features = ds[splits[0]].features

    for split in splits:
        df = ds[split].to_pandas()

        # Decode ClassLabel integers → string names if needed
        if hasattr(features["class_label"], "names"):
            names = features["class_label"].names
            df["class_label"] = df["class_label"].apply(
                lambda x: names[x] if isinstance(x, int) else x
            )

        result[split] = df[["tweet_text", "class_label"]].copy()

    return result


def verify_splits(splits_dict: Dict[str, pd.DataFrame], verbose: bool = True) -> bool:
    """Verify expected row counts, columns, labels, and no nulls."""
    expected = {"train": 53531, "validation": 7793, "test": 15160}
    ok = True

    for name, df in splits_dict.items():
        row_ok = len(df) == expected.get(name, len(df))
        col_ok = list(df.columns) == ["tweet_text", "class_label"]
        null_ok = df["tweet_text"].isnull().sum() == 0
        labels = sorted(df["class_label"].unique())
        label_ok = labels == HUMAID_CLASSES

        if verbose:
            status = "OK" if (row_ok and col_ok and null_ok and label_ok) else "WARN"
            print(f"  [{status}] {name}: {len(df):,} rows | "
                  f"cols={col_ok} | nulls={not null_ok} | "
                  f"labels={'match' if label_ok else 'MISMATCH'}")

        ok = ok and row_ok and col_ok and null_ok and label_ok

    return ok


if __name__ == "__main__":
    print("Loading HumAID dataset ...")
    data = load_humaid()
    print("Verification:")
    ok = verify_splits(data)
    print(f"\nAll checks passed: {ok}")
    for name, df in data.items():
        dist = df["class_label"].value_counts()
        print(f"\n{name} distribution:")
        for lbl, cnt in dist.items():
            print(f"  {lbl:<50} {cnt:>6,}  ({100*cnt/len(df):5.1f}%)")
