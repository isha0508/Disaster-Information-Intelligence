"""
tests/test_classification.py
==============================
Tests for the Phase 3 classification components.

Tests:
  - Dataset loading and verification
  - Label mapping (binary relevance)
  - Preprocessing compatibility
  - Model loading (requires trained models)
  - Inference output format
  - Prediction schema validation
  - Invalid/empty input handling
  - Label validity

Run:
    python -m pytest tests/test_classification.py -v
    python -m pytest tests/test_classification.py -v -m "not requires_model"
"""

import sys
import json
from pathlib import Path

import pytest
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from preprocessing.text_cleaner import clean_text
from preprocessing.humaid_loader import (
    load_humaid, verify_splits, HUMAID_CLASSES, RELEVANCE_MAP, RELEVANCE_NAMES
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def humaid_data():
    """Load HumAID splits once for all tests."""
    return load_humaid()


@pytest.fixture(scope="module")
def hum_predictor():
    """Load humanitarian predictor — skips if model not trained."""
    model_path = PROJECT_ROOT / "models" / "humanitarian" / "humanitarian_tfidf_lr.joblib"
    if not model_path.exists():
        pytest.skip("Humanitarian model not trained. Run training/train_humanitarian.py")
    from models.predict import HumanitarianPredictor
    return HumanitarianPredictor()


@pytest.fixture(scope="module")
def rel_predictor():
    """Load relevance predictor — skips if model not trained."""
    model_path = PROJECT_ROOT / "models" / "relevance" / "relevance_tfidf_lr.joblib"
    if not model_path.exists():
        pytest.skip("Relevance model not trained. Run training/train_relevance.py")
    from models.predict import RelevancePredictor
    return RelevancePredictor()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Dataset loading
# ─────────────────────────────────────────────────────────────────────────────

class TestDatasetLoading:
    def test_all_splits_loaded(self, humaid_data):
        assert set(humaid_data.keys()) == {"train", "validation", "test"}

    def test_train_row_count(self, humaid_data):
        assert len(humaid_data["train"]) == 53531

    def test_validation_row_count(self, humaid_data):
        assert len(humaid_data["validation"]) == 7793

    def test_test_row_count(self, humaid_data):
        assert len(humaid_data["test"]) == 15160

    def test_total_rows(self, humaid_data):
        total = sum(len(df) for df in humaid_data.values())
        assert total == 76484

    def test_columns_present(self, humaid_data):
        for split, df in humaid_data.items():
            assert "tweet_text" in df.columns, f"tweet_text missing in {split}"
            assert "class_label" in df.columns, f"class_label missing in {split}"

    def test_no_null_tweet_text(self, humaid_data):
        for split, df in humaid_data.items():
            assert df["tweet_text"].isnull().sum() == 0, f"Nulls in {split}"

    def test_no_null_class_label(self, humaid_data):
        for split, df in humaid_data.items():
            assert df["class_label"].isnull().sum() == 0, f"Null labels in {split}"

    def test_exactly_ten_classes(self, humaid_data):
        labels = sorted(humaid_data["train"]["class_label"].unique())
        assert len(labels) == 10

    def test_class_labels_match_canonical(self, humaid_data):
        labels = sorted(humaid_data["train"]["class_label"].unique())
        assert labels == HUMAID_CLASSES

    def test_verify_function_passes(self, humaid_data):
        assert verify_splits(humaid_data, verbose=False) is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. Label mapping (binary relevance)
# ─────────────────────────────────────────────────────────────────────────────

class TestLabelMapping:
    def test_all_classes_have_relevance_mapping(self):
        for cls in HUMAID_CLASSES:
            assert cls in RELEVANCE_MAP

    def test_not_humanitarian_maps_to_zero(self):
        assert RELEVANCE_MAP["not_humanitarian"] == 0

    def test_all_other_classes_map_to_one(self):
        for cls in HUMAID_CLASSES:
            if cls != "not_humanitarian":
                assert RELEVANCE_MAP[cls] == 1, f"{cls} should map to 1"

    def test_relevance_names_correct(self):
        assert RELEVANCE_NAMES[0] == "not_humanitarian"
        assert RELEVANCE_NAMES[1] == "humanitarian"

    def test_binary_balance_in_train(self, humaid_data):
        df = humaid_data["train"].copy()
        df["rel"] = df["class_label"].map(RELEVANCE_MAP)
        n_neg = (df["rel"] == 0).sum()
        n_pos = (df["rel"] == 1).sum()
        assert n_neg > 0, "No negative examples found"
        assert n_pos > 0, "No positive examples found"
        # not_humanitarian is ~8.2% of train
        assert 3000 < n_neg < 7000


# ─────────────────────────────────────────────────────────────────────────────
# 3. Preprocessing compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestPreprocessingCompatibility:
    def test_clean_text_returns_string(self, humaid_data):
        sample = humaid_data["train"]["tweet_text"].iloc[0]
        result = clean_text(sample)
        assert isinstance(result, str)

    def test_clean_text_on_full_train_no_crash(self, humaid_data):
        # Sample 100 tweets and clean them — should not raise
        sample = humaid_data["train"]["tweet_text"].sample(100, random_state=42)
        results = sample.apply(clean_text)
        assert len(results) == 100
        assert results.apply(lambda x: isinstance(x, str)).all()

    def test_clean_text_no_nulls_in_output(self, humaid_data):
        sample = humaid_data["train"]["tweet_text"].sample(200, random_state=1)
        results = sample.apply(clean_text)
        assert results.isnull().sum() == 0

    def test_clean_text_not_empty_on_real_tweets(self, humaid_data):
        # Real tweets should not be reduced to empty strings
        sample = humaid_data["train"]["tweet_text"].sample(50, random_state=2)
        results = sample.apply(clean_text)
        empty_count = (results.str.strip() == "").sum()
        # Allow at most 2 edge cases in 50
        assert empty_count <= 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. Humanitarian predictor — output schema
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.requires_model
class TestHumanitarianPredictor:
    def test_predict_returns_dict(self, hum_predictor):
        result = hum_predictor.predict("People trapped under rubble need rescue.")
        assert isinstance(result, dict)

    def test_predict_has_required_keys(self, hum_predictor):
        result = hum_predictor.predict("Flood waters rising, 200 families displaced.")
        required = {"label", "confidence", "top_k", "model", "task", "cleaned_text"}
        assert required.issubset(result.keys())

    def test_predict_label_is_valid_humaid_class(self, hum_predictor):
        result = hum_predictor.predict("50 people dead after earthquake.")
        assert result["label"] in HUMAID_CLASSES

    def test_predict_confidence_is_float_in_range(self, hum_predictor):
        result = hum_predictor.predict("Aid workers distributing food.")
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0

    def test_predict_top_k_is_list(self, hum_predictor):
        result = hum_predictor.predict("Rescue teams active.", top_k=3)
        assert isinstance(result["top_k"], list)
        assert len(result["top_k"]) <= 3

    def test_predict_top_k_labels_are_valid(self, hum_predictor):
        result = hum_predictor.predict("Flood warning issued.", top_k=5)
        for item in result["top_k"]:
            assert item["label"] in HUMAID_CLASSES

    def test_predict_top_k_confidences_sum_approximately_1(self, hum_predictor):
        # Only true if top_k == all classes (10)
        result = hum_predictor.predict("Families need shelter.", top_k=10)
        total = sum(item["confidence"] for item in result["top_k"])
        assert 0.98 <= total <= 1.02

    def test_empty_string_handled(self, hum_predictor):
        result = hum_predictor.predict("")
        assert "error" in result
        assert result["label"] is None

    def test_none_input_handled(self, hum_predictor):
        result = hum_predictor.predict(None)
        # Should return error dict, not raise
        assert isinstance(result, dict)

    def test_whitespace_only_handled(self, hum_predictor):
        result = hum_predictor.predict("    ")
        assert isinstance(result, dict)

    def test_predict_batch(self, hum_predictor):
        texts = [
            "200 families displaced by floods.",
            "Rescue volunteers needed in district 4.",
        ]
        results = hum_predictor.predict_batch(texts)
        assert len(results) == 2
        for r in results:
            assert r["label"] in HUMAID_CLASSES

    def test_model_name_correct(self, hum_predictor):
        result = hum_predictor.predict("Aid trucks arriving tomorrow.")
        assert result["model"] == "humanitarian_tfidf_lr"

    def test_task_correct(self, hum_predictor):
        result = hum_predictor.predict("Aid trucks arriving tomorrow.")
        assert result["task"] == "humanitarian_category"

    def test_cleaned_text_is_lowercase(self, hum_predictor):
        result = hum_predictor.predict("FLOOD ALERT in Karachi!")
        assert result["cleaned_text"] == result["cleaned_text"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# 5. Relevance predictor — output schema
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.requires_model
class TestRelevancePredictor:
    def test_predict_returns_dict(self, rel_predictor):
        result = rel_predictor.predict("Flood victims need food.")
        assert isinstance(result, dict)

    def test_predict_label_is_valid(self, rel_predictor):
        result = rel_predictor.predict("Emergency shelter needed.")
        assert result["label"] in ("humanitarian", "not_humanitarian")

    def test_predict_confidence_in_range(self, rel_predictor):
        result = rel_predictor.predict("Just had lunch.")
        assert 0.0 <= result["confidence"] <= 1.0

    def test_disaster_tweet_is_humanitarian(self, rel_predictor):
        result = rel_predictor.predict(
            "Rescue teams searching for survivors after massive earthquake."
        )
        assert result["label"] == "humanitarian"

    def test_empty_input_handled(self, rel_predictor):
        result = rel_predictor.predict("")
        assert isinstance(result, dict)
        assert "error" in result

    def test_caveat_present(self, rel_predictor):
        result = rel_predictor.predict("Help needed after flood.")
        assert "caveat" in result

    def test_model_name_correct(self, rel_predictor):
        result = rel_predictor.predict("Storm damage reported.")
        assert result["model"] == "relevance_tfidf_lr"


@pytest.fixture(scope="module")
def disaster_predictor():
    """Canonical disaster-type predictor (may be CHECKPOINT_UNAVAILABLE)."""
    from models.predict import DisasterTypePredictor
    return DisasterTypePredictor()

class TestDisasterTypePredictor:
    def test_canonical_paths_and_name(self):
        from models.predict import (
            DisasterTypePredictor,
            CANONICAL_DISASTER_TYPE,
            DISASTER_TYPE_LABELS,
        )
        predictor = DisasterTypePredictor()
        assert predictor.model_name == "distilbert_disaster_type"
        assert predictor.MODEL_DIR == CANONICAL_DISASTER_TYPE
        assert DISASTER_TYPE_LABELS == ["earthquake", "fire", "flood", "hurricane"]

    def test_returns_dict(self, disaster_predictor):
        result = disaster_predictor.predict("Earthquake struck the city.")
        assert isinstance(result, dict)
        assert result["task"] == "disaster_type"
        assert result["model"] == "distilbert_disaster_type"

    def test_not_implemented_stub_removed(self, disaster_predictor):
        result = disaster_predictor.predict("Flood in Kerala.")
        assert result.get("error") != "NOT_IMPLEMENTED"

    def test_empty_input_handled(self, disaster_predictor):
        result = disaster_predictor.predict("")
        assert isinstance(result, dict)
        if result.get("error") in {"CHECKPOINT_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"}:
            assert result["label"] is None
        else:
            assert result.get("error") == "empty_input"

    def test_unavailable_is_explicit(self, disaster_predictor):
        """If weights or deps are missing, failure is explicit — not a fake label."""
        from models.predict import DISASTER_TYPE_LABELS
        result = disaster_predictor.predict("Cyclone approaching coast.")
        if result.get("error") in {"CHECKPOINT_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"}:
            assert result["label"] is None
            assert "reason" in result
            assert len(result["reason"]) > 10
        else:
            assert result["label"] in DISASTER_TYPE_LABELS
            assert isinstance(result["confidence"], float)
            assert 0.0 <= result["confidence"] <= 1.0


# ─────────────────────────────────────────────────────────────────────────────
# 7. End-to-end preprocessing → prediction pipeline
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.requires_model
class TestEndToEndPipeline:
    def test_raw_tweet_through_pipeline(self, hum_predictor):
        raw = "RT @RedCross: #FloodRelief 500 families need food & water https://t.co/abc"
        result = hum_predictor.predict(raw)
        assert result["label"] in HUMAID_CLASSES
        # URL and mention should be gone from cleaned text
        assert "http" not in result["cleaned_text"]
        assert "@" not in result["cleaned_text"]

    def test_emoji_tweet_through_pipeline(self, hum_predictor):
        raw = "🚨 Emergency: 200 people trapped under rubble 🙏 Need help NOW"
        result = hum_predictor.predict(raw)
        assert result["label"] in HUMAID_CLASSES
        assert isinstance(result["confidence"], float)

    def test_html_entity_tweet_through_pipeline(self, hum_predictor):
        raw = "Aid workers &amp; volunteers distribute food in flood zones"
        result = hum_predictor.predict(raw)
        assert result["label"] in HUMAID_CLASSES
        assert "&amp;" not in result["cleaned_text"]
