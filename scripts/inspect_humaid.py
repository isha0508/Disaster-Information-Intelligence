"""
HumAID Dataset Download and Inspection Script.
Downloads QCRI/HumAID-all, saves to data/raw/humaid/, and reports factual observations.
"""

import sys
import os
import json
import collections
from pathlib import Path

# -------------------------------------------------------
# 1. Resolve paths relative to project root
# -------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
SAVE_DIR = PROJECT_ROOT / "data" / "raw" / "humaid"
SAVE_DIR.mkdir(parents=True, exist_ok=True)

print("=" * 60)
print("HumAID Dataset Download + Inspection")
print("=" * 60)
print(f"Project root : {PROJECT_ROOT}")
print(f"Save path    : {SAVE_DIR}")
print()

# -------------------------------------------------------
# 2. Load dataset
# -------------------------------------------------------
from datasets import load_dataset

print("[1] Loading QCRI/HumAID-all from Hugging Face ...")
ds = load_dataset("QCRI/HumAID-all", cache_dir=str(SAVE_DIR), verification_mode="no_checks")
print("    Download complete.")
print()

# -------------------------------------------------------
# 3. Splits
# -------------------------------------------------------
print("[2] Available splits:")
for split_name in ds.keys():
    print(f"    {split_name} : {len(ds[split_name]):,} rows")
print()

# -------------------------------------------------------
# 4. Column names
# -------------------------------------------------------
first_split = list(ds.keys())[0]
print("[3] Column names:")
print("   ", ds[first_split].column_names)
print()

# -------------------------------------------------------
# 5. Schema / feature types
# -------------------------------------------------------
print("[4] Features / schema:")
print("   ", ds[first_split].features)
print()

# -------------------------------------------------------
# 6. Class labels
# -------------------------------------------------------
print("[5] Unique class labels:")
features = ds[first_split].features
label_feature = None
all_label_names = []

for col, feat in features.items():
    if hasattr(feat, "names"):
        label_feature = col
        all_label_names = feat.names
        print(f"    Column '{col}' is a ClassLabel with {len(feat.names)} classes:")
        for i, name in enumerate(feat.names):
            print(f"        {i}: {name}")
        break

if not label_feature:
    print("    No ClassLabel feature found. Scanning for label-like string columns...")
    for col in ds[first_split].column_names:
        vals = ds[first_split][col]
        if col not in ("tweet_text", "text", "tweet_id", "id"):
            unique_vals = list(set(str(v) for v in vals))
            if len(unique_vals) <= 25:
                label_feature = col
                all_label_names = sorted(unique_vals)
                print(f"    Likely label column '{col}' with {len(unique_vals)} unique values:")
                for v in sorted(unique_vals):
                    print(f"        {v}")
                break
print()

# -------------------------------------------------------
# 7. Class distribution per split
# -------------------------------------------------------
print("[6] Class distribution per split:")

for split_name in ds.keys():
    split = ds[split_name]
    print(f"\n  --- {split_name} ({len(split):,} rows) ---")
    if label_feature and label_feature in split.column_names:
        raw_vals = split[label_feature]
        counts = collections.Counter(raw_vals)
        for key in sorted(counts.keys()):
            if all_label_names and isinstance(key, int):
                lbl = all_label_names[key]
            else:
                lbl = str(key)
            pct = 100.0 * counts[key] / len(split)
            print(f"    {lbl:<50} {counts[key]:>6,}  ({pct:5.1f}%)")
    else:
        print("    (Could not determine label column automatically)")
print()

# -------------------------------------------------------
# 8. Missing / null values in tweet_text
# -------------------------------------------------------
print("[7] Missing / null values in text column:")
text_col = None
for candidate in ("tweet_text", "text"):
    if candidate in ds[first_split].column_names:
        text_col = candidate
        break

if text_col:
    for split_name in ds.keys():
        vals = ds[split_name][text_col]
        null_count = sum(1 for v in vals if v is None or (isinstance(v, str) and v.strip() == ""))
        print(f"  {split_name}: {null_count} missing / empty values out of {len(vals):,}")
else:
    print("  Could not find tweet_text or text column.")
print()

# -------------------------------------------------------
# 9. Check for event / disaster-type metadata columns
# -------------------------------------------------------
print("[8] Checking for event / disaster-type metadata columns:")
all_cols = ds[first_split].column_names
event_related = [c for c in all_cols if any(
    kw in c.lower() for kw in ("event", "disaster", "type", "source", "location", "date")
)]
if event_related:
    print(f"  Found potential event/metadata columns: {event_related}")
    for col in event_related:
        sample_vals = sorted(set(str(v) for v in ds[first_split][col]))[:20]
        print(f"  Unique values for '{col}' (up to 20): {sample_vals}")
else:
    print("  RESULT: NO event or disaster-type metadata columns found in this dataset version.")
    print("  Available columns are:", all_cols)
print()

# -------------------------------------------------------
# 10. Representative examples
# -------------------------------------------------------
print("[9] 10 representative examples (evenly spaced from train split):")
sample_split = "train" if "train" in ds else first_split
sample_ds = ds[sample_split]
n_total = len(sample_ds)
indices = [int(i * n_total / 10) for i in range(10)]

for rank, idx in enumerate(indices, start=1):
    row = sample_ds[idx]
    print(f"\n  Example {rank} (index {idx}):")
    for col in sample_ds.column_names:
        val = row[col]
        if label_feature and col == label_feature and all_label_names and isinstance(val, int):
            val = all_label_names[val]
        print(f"    {col}: {str(val)[:120]}")

print()

# -------------------------------------------------------
# 11. Save a summary JSON
# -------------------------------------------------------
summary = {}
for split_name in ds.keys():
    split = ds[split_name]
    dist = {}
    if label_feature and label_feature in split.column_names:
        raw_counts = collections.Counter(split[label_feature])
        for k, v in raw_counts.items():
            lbl = all_label_names[k] if all_label_names and isinstance(k, int) else str(k)
            dist[lbl] = v
    summary[split_name] = {
        "rows": len(split),
        "columns": split.column_names,
        "class_distribution": dist
    }

summary_path = SAVE_DIR / "inspection_summary.json"
with open(summary_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)
print(f"[10] Summary JSON saved to: {summary_path}")
print()
print("=" * 60)
print("INSPECTION COMPLETE")
print("=" * 60)
