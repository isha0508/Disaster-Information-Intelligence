"""
preprocessing/explore_humaid.py
================================
Exploratory Data Analysis for QCRI/HumAID-all dataset.
Loads the dataset from the local cache, performs comprehensive analysis,
saves figures to docs/eda/, and writes docs/humaid_eda_report.md.

Run:
    python preprocessing/explore_humaid.py
"""

import sys
import re
import os
import json
import collections
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # non-interactive backend — safe for scripts
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import seaborn as sns
import emoji
from datasets import load_dataset

# ─────────────────────────────────────────────────────────────────────────────
# PATHS
# ─────────────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).resolve().parent.parent
DATA_DIR   = ROOT / "data" / "raw" / "humaid"
EDA_DIR    = ROOT / "docs" / "eda"
REPORT_PATH = ROOT / "docs" / "humaid_eda_report.md"
EDA_DIR.mkdir(parents=True, exist_ok=True)

sns.set_theme(style="whitegrid", palette="muted")

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def count_urls(text: str) -> int:
    return len(re.findall(r"https?://\S+|www\.\S+", text))

def count_mentions(text: str) -> int:
    return len(re.findall(r"@\w+", text))

def count_hashtags(text: str) -> int:
    return len(re.findall(r"#\w+", text))

def count_emojis(text: str) -> int:
    return emoji.emoji_count(text)

def count_repeated_punct(text: str) -> int:
    return len(re.findall(r"[!?.]{2,}", text))

def word_count(text: str) -> int:
    return len(text.split())

def char_count(text: str) -> int:
    return len(text)

def is_empty(text: str) -> bool:
    return text is None or str(text).strip() == ""

def stats_dict(values) -> dict:
    arr = np.array(values, dtype=float)
    return {
        "min":    float(np.min(arr)),
        "max":    float(np.max(arr)),
        "mean":   float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p25":    float(np.percentile(arr, 25)),
        "p75":    float(np.percentile(arr, 75)),
    }

# ─────────────────────────────────────────────────────────────────────────────
# 1. LOAD DATASET
# ─────────────────────────────────────────────────────────────────────────────
print("=" * 65)
print("HumAID Exploratory Data Analysis")
print("=" * 65)
print(f"\n[1] Loading dataset from {DATA_DIR} ...")

ds = load_dataset(
    "QCRI/HumAID-all",
    cache_dir=str(DATA_DIR),
    verification_mode="no_checks",
)

splits = {name: ds[name].to_pandas() for name in ("train", "validation", "test")}

# Decode ClassLabel integers → string names if necessary
features = ds["train"].features
label_col = "class_label"
if hasattr(features[label_col], "names"):
    label_names = features[label_col].names
    for name, df in splits.items():
        splits[name][label_col] = splits[name][label_col].apply(
            lambda x: label_names[x] if isinstance(x, int) else x
        )

df_all = pd.concat(splits.values(), keys=splits.keys()).reset_index(level=0).rename(
    columns={"level_0": "split"}
)

print("  Loaded splits:")
for name, df in splits.items():
    print(f"    {name:12s}: {len(df):,} rows")
print(f"  Total tweets    : {len(df_all):,}")

# ─────────────────────────────────────────────────────────────────────────────
# 2. SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] Schema:")
print(splits["train"].dtypes)

missing = {name: df.isnull().sum().to_dict() for name, df in splits.items()}
print("\n  Missing values per split:")
for name, m in missing.items():
    print(f"    {name}: {m}")

# ─────────────────────────────────────────────────────────────────────────────
# 3. CLASS ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] Class analysis:")
all_labels = sorted(df_all[label_col].unique())
print(f"  Unique labels ({len(all_labels)}): {all_labels}")

class_stats = {}  # { label: {split: count} }
for label in all_labels:
    class_stats[label] = {}
    for name, df in splits.items():
        class_stats[label][name] = int((df[label_col] == label).sum())

# Per-split distribution
split_dist = {}
for name, df in splits.items():
    counts = df[label_col].value_counts().sort_values(ascending=False)
    total = len(df)
    split_dist[name] = counts
    print(f"\n  {name} ({total:,} rows):")
    for lbl, cnt in counts.items():
        print(f"    {lbl:<50} {cnt:>6,}  ({100*cnt/total:5.1f}%)")

# Overall (train only for dominance check, as it's canonical)
train_counts = split_dist["train"]
majority_class = train_counts.idxmax()
minority_class = train_counts.idxmin()
print(f"\n  Majority class (train): {majority_class}  ({train_counts.max():,})")
print(f"  Minority class (train): {minority_class}  ({train_counts.min():,})")
imbalance_ratio = train_counts.max() / train_counts.min()
print(f"  Imbalance ratio       : {imbalance_ratio:.1f}:1")

# ─────────────────────────────────────────────────────────────────────────────
# 4. TEXT ANALYSIS  (computed on full combined dataset for distribution plots)
# ─────────────────────────────────────────────────────────────────────────────
print("\n[4] Text analysis (computing length features on all splits) ...")

df_all["char_len"]  = df_all["tweet_text"].apply(char_count)
df_all["word_cnt"]  = df_all["tweet_text"].apply(word_count)

text_stats = {}
for name, df in splits.items():
    chars = df["tweet_text"].apply(char_count)
    words = df["tweet_text"].apply(word_count)
    text_stats[name] = {
        "char": stats_dict(chars),
        "word": stats_dict(words),
    }
    print(f"\n  {name}:")
    print(f"    char length — min:{chars.min()}  max:{chars.max()}  "
          f"mean:{chars.mean():.1f}  median:{chars.median():.1f}")
    print(f"    word count  — min:{words.min()}  max:{words.max()}  "
          f"mean:{words.mean():.1f}  median:{words.median():.1f}")

# ─────────────────────────────────────────────────────────────────────────────
# 5. DATA QUALITY
# ─────────────────────────────────────────────────────────────────────────────
print("\n[5] Data quality checks ...")

# Empty / whitespace
empty_counts = {name: int(df["tweet_text"].apply(is_empty).sum())
                for name, df in splits.items()}
print(f"  Empty/whitespace tweets: {empty_counts}")

# Exact duplicates within each split
dup_within = {name: int(df["tweet_text"].duplicated().sum())
              for name, df in splits.items()}
print(f"  Exact duplicates within split: {dup_within}")

# Cross-split duplicates
train_texts = set(splits["train"]["tweet_text"])
val_texts   = set(splits["validation"]["tweet_text"])
test_texts  = set(splits["test"]["tweet_text"])

cross_train_val  = len(train_texts & val_texts)
cross_train_test = len(train_texts & test_texts)
cross_val_test   = len(val_texts & test_texts)
cross_all        = len(train_texts & val_texts & test_texts)
print(f"  Cross-split duplicates: train∩val={cross_train_val}, "
      f"train∩test={cross_train_test}, val∩test={cross_val_test}, "
      f"all-three={cross_all}")

# Feature counts (on all data combined)
print("  Computing URL / mention / hashtag / emoji / repeated-punct counts ...")
df_all["n_urls"]      = df_all["tweet_text"].apply(count_urls)
df_all["n_mentions"]  = df_all["tweet_text"].apply(count_mentions)
df_all["n_hashtags"]  = df_all["tweet_text"].apply(count_hashtags)
df_all["n_emojis"]    = df_all["tweet_text"].apply(count_emojis)
df_all["n_rep_punct"] = df_all["tweet_text"].apply(count_repeated_punct)

feature_summary = {
    "tweets_with_url":        int((df_all["n_urls"]      > 0).sum()),
    "tweets_with_mention":    int((df_all["n_mentions"]  > 0).sum()),
    "tweets_with_hashtag":    int((df_all["n_hashtags"]  > 0).sum()),
    "tweets_with_emoji":      int((df_all["n_emojis"]    > 0).sum()),
    "tweets_with_rep_punct":  int((df_all["n_rep_punct"] > 0).sum()),
    "total_tweets":           len(df_all),
}
for k, v in feature_summary.items():
    if k != "total_tweets":
        pct = 100 * v / feature_summary["total_tweets"]
        print(f"    {k:<30} {v:>6,}  ({pct:5.1f}%)")

# ─────────────────────────────────────────────────────────────────────────────
# 6. REPRESENTATIVE EXAMPLES
# ─────────────────────────────────────────────────────────────────────────────
print("\n[6] Representative examples (one per class from train split):")
examples = []
for label in sorted(all_labels):
    subset = splits["train"][splits["train"][label_col] == label]
    row = subset.sample(1, random_state=42).iloc[0]
    examples.append({"label": label, "tweet": row["tweet_text"]})
    print(f"\n  [{label}]")
    print(f"    {row['tweet_text'][:160]}")

# ─────────────────────────────────────────────────────────────────────────────
# 7. VISUALISATIONS
# ─────────────────────────────────────────────────────────────────────────────
print("\n[7] Generating visualisations ...")

# ── 7a. Class distribution (train / validation / test side-by-side) ──────────
fig, axes = plt.subplots(1, 3, figsize=(22, 7), sharey=False)
for ax, (name, df) in zip(axes, splits.items()):
    counts = df[label_col].value_counts().sort_values(ascending=True)
    short_labels = [l.replace("_", "\n") for l in counts.index]
    bars = ax.barh(short_labels, counts.values, color=sns.color_palette("muted", len(counts)))
    ax.set_title(f"{name.capitalize()} split\n({len(df):,} tweets)", fontsize=13, fontweight="bold")
    ax.set_xlabel("Number of tweets", fontsize=11)
    for bar, val in zip(bars, counts.values):
        ax.text(bar.get_width() + 20, bar.get_y() + bar.get_height() / 2,
                f"{val:,}", va="center", fontsize=8)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))

plt.suptitle("HumAID-all: Class Distribution per Split", fontsize=15, fontweight="bold", y=1.01)
plt.tight_layout()
fig_path = EDA_DIR / "class_distribution.png"
plt.savefig(fig_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {fig_path}")

# ── 7b. Tweet character-length distribution ───────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(18, 5), sharey=False)
for ax, (name, df) in zip(axes, splits.items()):
    chars = df["tweet_text"].apply(char_count)
    ax.hist(chars, bins=60, color=sns.color_palette("muted")[0], edgecolor="white", linewidth=0.4)
    ax.axvline(chars.mean(),   color="red",    linestyle="--", linewidth=1.2, label=f"mean={chars.mean():.0f}")
    ax.axvline(chars.median(), color="orange", linestyle=":",  linewidth=1.2, label=f"median={chars.median():.0f}")
    ax.set_title(f"{name.capitalize()} — character length", fontsize=12)
    ax.set_xlabel("Characters per tweet")
    ax.set_ylabel("Count")
    ax.legend(fontsize=9)
plt.suptitle("HumAID-all: Tweet Character-Length Distribution", fontsize=14, fontweight="bold")
plt.tight_layout()
fig_path = EDA_DIR / "char_length_distribution.png"
plt.savefig(fig_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {fig_path}")

# ── 7c. Word-count distribution ───────────────────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(18, 5), sharey=False)
for ax, (name, df) in zip(axes, splits.items()):
    words = df["tweet_text"].apply(word_count)
    ax.hist(words, bins=50, color=sns.color_palette("muted")[2], edgecolor="white", linewidth=0.4)
    ax.axvline(words.mean(),   color="red",    linestyle="--", linewidth=1.2, label=f"mean={words.mean():.1f}")
    ax.axvline(words.median(), color="orange", linestyle=":",  linewidth=1.2, label=f"median={words.median():.1f}")
    ax.set_title(f"{name.capitalize()} — word count", fontsize=12)
    ax.set_xlabel("Words per tweet")
    ax.set_ylabel("Count")
    ax.legend(fontsize=9)
plt.suptitle("HumAID-all: Tweet Word-Count Distribution", fontsize=14, fontweight="bold")
plt.tight_layout()
fig_path = EDA_DIR / "word_count_distribution.png"
plt.savefig(fig_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {fig_path}")

# ── 7d. Class imbalance ratio bar (train only) ────────────────────────────────
fig, ax = plt.subplots(figsize=(12, 5))
sorted_counts = split_dist["train"].sort_values(ascending=False)
short_x = [l.replace("_", "\n") for l in sorted_counts.index]
bars = ax.bar(short_x, sorted_counts.values,
              color=sns.color_palette("muted", len(sorted_counts)))
ax.set_title("HumAID Train Split — Class Imbalance", fontsize=14, fontweight="bold")
ax.set_ylabel("Number of tweets")
ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
for bar in bars:
    ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 80,
            f"{int(bar.get_height()):,}", ha="center", va="bottom", fontsize=9)
plt.xticks(fontsize=8)
plt.tight_layout()
fig_path = EDA_DIR / "class_imbalance_train.png"
plt.savefig(fig_path, dpi=150, bbox_inches="tight")
plt.close()
print(f"  Saved: {fig_path}")

# ─────────────────────────────────────────────────────────────────────────────
# 8. BUILD REPORT
# ─────────────────────────────────────────────────────────────────────────────
print("\n[8] Writing EDA report ...")

# Collect all per-split class tables
def class_table_md(name, df):
    counts = df[label_col].value_counts().sort_values(ascending=False)
    total = len(df)
    rows = ["| Class Label | Count | Percentage |",
            "|---|---:|---:|"]
    for lbl, cnt in counts.items():
        rows.append(f"| {lbl} | {cnt:,} | {100*cnt/total:.1f}% |")
    return "\n".join(rows)

def stats_table_md(s: dict) -> str:
    return (f"min={s['min']:.0f}, max={s['max']:.0f}, "
            f"mean={s['mean']:.1f}, median={s['median']:.1f}, "
            f"p25={s['p25']:.0f}, p75={s['p75']:.0f}")

report_lines = [
    "# HumAID EDA Report",
    "**Dataset:** QCRI/HumAID-all (Hugging Face)**  ",
    "**Project:** Disaster Information Intelligence and Decision Support System**  ",
    "**Phase:** 3 — Data Understanding and Baseline NLP**  ",
    f"**Generated by:** `preprocessing/explore_humaid.py`**  ",
    "",
    "---",
    "",
    "## 1. Dataset Size",
    "",
    "| Split | Rows |",
    "|---|---:|",
]
for name, df in splits.items():
    report_lines.append(f"| {name} | {len(df):,} |")
report_lines += [
    f"| **Total** | **{len(df_all):,}** |",
    "",
    "---",
    "",
    "## 2. Schema",
    "",
    "| Column | Type | Missing Values (train / val / test) |",
    "|---|---|---|",
]
for col in splits["train"].columns:
    m = " / ".join(str(missing[s].get(col, 0)) for s in ("train", "validation", "test"))
    dtype = str(splits["train"][col].dtype)
    report_lines.append(f"| {col} | {dtype} | {m} |")

report_lines += [
    "",
    "---",
    "",
    "## 3. Class Analysis",
    "",
    f"**Number of unique classes:** {len(all_labels)}",
    "",
    "**All class labels:**",
    "",
]
for i, lbl in enumerate(sorted(all_labels), 1):
    report_lines.append(f"{i}. `{lbl}`")

for name, df in splits.items():
    report_lines += [
        "",
        f"### {name.capitalize()} split ({len(df):,} rows)",
        "",
        class_table_md(name, df),
    ]

report_lines += [
    "",
    f"**Majority class (train):** `{majority_class}` — {train_counts.max():,} tweets ({100*train_counts.max()/len(splits['train']):.1f}%)",
    f"  ",
    f"**Minority class (train):** `{minority_class}` — {train_counts.min():,} tweets ({100*train_counts.min()/len(splits['train']):.1f}%)",
    f"  ",
    f"**Imbalance ratio (train):** {imbalance_ratio:.1f}:1",
    "",
    "---",
    "",
    "## 4. Text Analysis",
    "",
    "### Character Length",
    "",
    "| Split | Min | Max | Mean | Median | P25 | P75 |",
    "|---|---:|---:|---:|---:|---:|---:|",
]
for name in ("train", "validation", "test"):
    s = text_stats[name]["char"]
    report_lines.append(
        f"| {name} | {s['min']:.0f} | {s['max']:.0f} | {s['mean']:.1f} | {s['median']:.1f} | {s['p25']:.0f} | {s['p75']:.0f} |"
    )

report_lines += [
    "",
    "### Word Count",
    "",
    "| Split | Min | Max | Mean | Median | P25 | P75 |",
    "|---|---:|---:|---:|---:|---:|---:|",
]
for name in ("train", "validation", "test"):
    s = text_stats[name]["word"]
    report_lines.append(
        f"| {name} | {s['min']:.0f} | {s['max']:.0f} | {s['mean']:.1f} | {s['median']:.1f} | {s['p25']:.0f} | {s['p75']:.0f} |"
    )

n_total = feature_summary["total_tweets"]
report_lines += [
    "",
    "---",
    "",
    "## 5. Data Quality",
    "",
    "### Empty / Whitespace Tweets",
    "",
    "| Split | Empty tweets |",
    "|---|---:|",
]
for name in ("train", "validation", "test"):
    report_lines.append(f"| {name} | {empty_counts[name]} |")

report_lines += [
    "",
    "### Exact Duplicates Within Split",
    "",
    "| Split | Duplicate tweets |",
    "|---|---:|",
]
for name in ("train", "validation", "test"):
    report_lines.append(f"| {name} | {dup_within[name]:,} |")

report_lines += [
    "",
    "### Cross-Split Duplicate Tweets",
    "",
    "| Pair | Shared tweets |",
    "|---|---:|",
    f"| train ∩ validation | {cross_train_val:,} |",
    f"| train ∩ test       | {cross_train_test:,} |",
    f"| validation ∩ test  | {cross_val_test:,} |",
    f"| all three splits   | {cross_all:,} |",
    "",
    "### Noise Features (all splits combined)",
    "",
    "| Feature | Tweets affected | Percentage |",
    "|---|---:|---:|",
    f"| Contains URL       | {feature_summary['tweets_with_url']:,} | {100*feature_summary['tweets_with_url']/n_total:.1f}% |",
    f"| Contains @mention  | {feature_summary['tweets_with_mention']:,} | {100*feature_summary['tweets_with_mention']/n_total:.1f}% |",
    f"| Contains #hashtag  | {feature_summary['tweets_with_hashtag']:,} | {100*feature_summary['tweets_with_hashtag']/n_total:.1f}% |",
    f"| Contains emoji     | {feature_summary['tweets_with_emoji']:,} | {100*feature_summary['tweets_with_emoji']/n_total:.1f}% |",
    f"| Repeated punctuation | {feature_summary['tweets_with_rep_punct']:,} | {100*feature_summary['tweets_with_rep_punct']/n_total:.1f}% |",
    "",
    "---",
    "",
    "## 6. Representative Examples",
    "",
    "One tweet sampled per class from the train split.",
    "",
]
for ex in examples:
    report_lines.append(f"**`{ex['label']}`**  ")
    # Escape pipe characters to avoid breaking markdown tables
    safe_tweet = ex["tweet"].replace("\n", " ").replace("|", "\\|")
    report_lines.append(f"> {safe_tweet[:300]}")
    report_lines.append("")

report_lines += [
    "---",
    "",
    "## 7. Visualisations",
    "",
    "All figures saved to `docs/eda/`.",
    "",
    "| Figure | File |",
    "|---|---|",
    "| Class distribution (all splits) | `docs/eda/class_distribution.png` |",
    "| Tweet character-length distribution | `docs/eda/char_length_distribution.png` |",
    "| Tweet word-count distribution | `docs/eda/word_count_distribution.png` |",
    "| Class imbalance — train split | `docs/eda/class_imbalance_train.png` |",
    "",
    "---",
    "",
    "## 8. Key Findings Summary",
    "",
    "| Observation | Value |",
    "|---|---|",
    f"| Total tweets | {len(df_all):,} |",
    f"| Train / Validation / Test | {len(splits['train']):,} / {len(splits['validation']):,} / {len(splits['test']):,} |",
    f"| Columns | 2 (tweet_text, class_label) |",
    f"| Unique class labels | {len(all_labels)} |",
    f"| Missing tweet_text | 0 |",
    f"| Empty / whitespace tweets | {sum(empty_counts.values())} |",
    f"| Within-split duplicates (train) | {dup_within['train']:,} |",
    f"| Cross-split duplicates (train∩test) | {cross_train_test:,} |",
    f"| Majority class | {majority_class} ({100*train_counts.max()/len(splits['train']):.1f}%) |",
    f"| Minority class | {minority_class} ({100*train_counts.min()/len(splits['train']):.1f}%) |",
    f"| Imbalance ratio | {imbalance_ratio:.1f}:1 |",
    f"| Tweets with URLs | {feature_summary['tweets_with_url']:,} ({100*feature_summary['tweets_with_url']/n_total:.1f}%) |",
    f"| Tweets with @mentions | {feature_summary['tweets_with_mention']:,} ({100*feature_summary['tweets_with_mention']/n_total:.1f}%) |",
    f"| Tweets with #hashtags | {feature_summary['tweets_with_hashtag']:,} ({100*feature_summary['tweets_with_hashtag']/n_total:.1f}%) |",
    f"| Tweets with emojis | {feature_summary['tweets_with_emoji']:,} ({100*feature_summary['tweets_with_emoji']/n_total:.1f}%) |",
    "",
    "---",
    "",
    "*Status: EDA complete. No models trained. No commits made.*",
]

REPORT_PATH.write_text("\n".join(report_lines), encoding="utf-8")
print(f"  Report written to: {REPORT_PATH}")

# ─────────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY TO STDOUT
# ─────────────────────────────────────────────────────────────────────────────
print()
print("=" * 65)
print("ANALYSIS COMPLETE — SUMMARY")
print("=" * 65)
print(f"  Total tweets          : {len(df_all):,}")
print(f"  Train / Val / Test    : {len(splits['train']):,} / {len(splits['validation']):,} / {len(splits['test']):,}")
print(f"  Class labels          : {len(all_labels)}")
print(f"  Within-split dups     : train={dup_within['train']:,}  val={dup_within['validation']:,}  test={dup_within['test']:,}")
print(f"  Cross dups train∩test : {cross_train_test:,}")
print(f"  Empty tweets          : {sum(empty_counts.values())}")
print(f"  Imbalance ratio       : {imbalance_ratio:.1f}:1")
print(f"  Majority class        : {majority_class}")
print(f"  Minority class        : {minority_class}")
print(f"  Figures saved         : {EDA_DIR}")
print(f"  Report saved          : {REPORT_PATH}")
print("=" * 65)
