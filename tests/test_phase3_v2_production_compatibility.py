"""Production-contract tests for the canonical Phase 3 V2 checkpoint.

V2 uses the existing DisasterTypePredictor implementation and checkpoint.
The test subclass pins that path explicitly to keep this test's target clear.
"""

from pathlib import Path

import pytest

from models.predict import (
    CANONICAL_DISASTER_TYPE,
    DISASTER_TYPE_LABELS,
    DisasterTypePredictor,
)
from preprocessing.text_cleaner import clean_text


ROOT = Path(__file__).resolve().parents[1]
V2_CHECKPOINT = ROOT / "models" / "phase_3_v2" / "best_checkpoint"
LABELS = ["earthquake", "fire", "flood", "hurricane"]
REPRESENTATIVE_TEXTS = [
    ("earthquake", "A magnitude 5.8 earthquake damaged buildings near the coast."),
    ("flood", "Floodwater entered homes after the river overtopped its banks."),
    ("hurricane", "Hurricane winds forced coastal residents to evacuate."),
    ("fire", "A wildfire spread through dry grassland and threatened nearby homes."),
    ("short", "Flood warning."),
    ("long_report", "Following days of heavy rain, several districts reported "
     "flooding, blocked roads, damaged bridges, and families needing shelter."),
    ("noisy_social", "RT @alerts: #Flooding!!! roads closed rn 😟 https://example.org/report"),
    ("multiple_terms", "An earthquake damaged the town while heavy rain caused "
     "flooding and officials monitored a nearby wildfire."),
]


class V2ProductionContractPredictor(DisasterTypePredictor):
    """Production predictor contract with the candidate checkpoint path only."""

    MODEL_DIR = V2_CHECKPOINT


@pytest.fixture(scope="module")
def v2_predictor():
    predictor = V2ProductionContractPredictor()
    result = predictor.predict(REPRESENTATIVE_TEXTS[0][1])
    assert result.get("error") is None, result
    return predictor


def test_candidate_checkpoint_is_complete_and_keeps_production_ontology():
    assert V2_CHECKPOINT.is_dir()
    assert (V2_CHECKPOINT / "config.json").is_file()
    assert (V2_CHECKPOINT / "model.safetensors").is_file()
    assert (V2_CHECKPOINT / "tokenizer.json").is_file()
    assert V2_CHECKPOINT == CANONICAL_DISASTER_TYPE
    assert DISASTER_TYPE_LABELS == LABELS


def test_v2_production_interface_schema_and_probabilities(v2_predictor):
    result = v2_predictor.predict("Flood warning.", top_k=3)
    assert set(("label", "confidence", "top_k", "model", "task", "cleaned_text")) <= set(result)
    assert result["model"] == "distilbert_disaster_type"
    assert result["task"] == "disaster_type"
    assert result["label"] in LABELS
    assert 0.0 <= result["confidence"] <= 1.0
    assert len(result["top_k"]) == 3
    assert all(item["label"] in LABELS and 0.0 <= item["confidence"] <= 1.0
               for item in result["top_k"])
    assert result["top_k"][0]["label"] == result["label"]
    assert result["top_k"][0]["confidence"] == result["confidence"]
    assert sum(item["confidence"] for item in result["top_k"]) <= 1.0001


@pytest.mark.parametrize(("case", "text"), REPRESENTATIVE_TEXTS, ids=[x[0] for x in REPRESENTATIVE_TEXTS])
def test_v2_representative_inputs_use_the_production_preprocessing(case, text, v2_predictor):
    result = v2_predictor.predict(text, top_k=4)
    assert result.get("error") is None, result
    assert result["label"] in LABELS
    assert result["cleaned_text"] == clean_text(text)
    assert len(result["top_k"]) == 4
    assert all(item["label"] in LABELS and 0.0 <= item["confidence"] <= 1.0
               for item in result["top_k"])
