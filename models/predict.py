"""
models/predict.py
==================
Unified prediction interface for the Disaster Information Intelligence system.

This module provides a clean API for all classifiers so that downstream
components (NER, severity engine, GIS, FastAPI, dashboard) can call
classifiers without knowing model-specific details.

Usage
-----
    from models.predict import HumanitarianPredictor, RelevancePredictor

    hum = HumanitarianPredictor()
    result = hum.predict("People trapped under rubble in Karachi. Help needed.")
    # {"label": "requests_or_urgent_needs", "confidence": 0.91, "top_3": [...], ...}

    rel = RelevancePredictor()
    result = rel.predict("Just had a great day at the beach!")
    # {"label": "not_humanitarian", "confidence": 0.88, ...}
"""

import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from preprocessing.text_cleaner import clean_text
from preprocessing.humaid_loader import HUMAID_CLASSES, RELEVANCE_NAMES

# Canonical transformer checkpoints live with the Phase 3 research dumps.
# They are NOT the smoke-test folder under models/_smoke_test/.
CANONICAL_HUMANITARIAN_DISTILBERT = ROOT / "notebooks" / "phase_3" / "distilbert"
CANONICAL_DISASTER_TYPE = ROOT / "notebooks" / "phase_3" / "disaster_type"
SMOKE_TRANSFORMER_HUMANITARIAN = (
    ROOT / "models" / "_smoke_test" / "transformer_humanitarian" / "best_model"
)
DISASTER_TYPE_LABELS = ["earthquake", "fire", "flood", "hurricane"]


def _transformer_checkpoint_ready(model_dir: Path) -> bool:
    """True when a Hugging Face folder has config plus at least one weight file."""
    if not model_dir.is_dir():
        return False
    has_config = (model_dir / "config.json").exists()
    has_weights = (
        (model_dir / "model.safetensors").exists()
        or (model_dir / "pytorch_model.bin").exists()
        or any(model_dir.glob("*.safetensors"))
    )
    return has_config and has_weights

# ─────────────────────────────────────────────────────────────────────────────
# Base class
# ─────────────────────────────────────────────────────────────────────────────

class BasePredictor:
    """Abstract base for all classifiers."""

    model_name: str = "base"
    task: str = "classification"
    _loaded: bool = False

    def _ensure_loaded(self):
        if not self._loaded:
            self._load()
            self._loaded = True

    def _load(self):
        raise NotImplementedError

    def predict(self, text: str) -> Dict[str, Any]:
        raise NotImplementedError

    def predict_batch(self, texts: List[str]) -> List[Dict[str, Any]]:
        return [self.predict(t) for t in texts]

    def _safe_clean(self, text) -> str:
        return clean_text(text)


# ─────────────────────────────────────────────────────────────────────────────
# Humanitarian Category Predictor (TF-IDF + LR)
# ─────────────────────────────────────────────────────────────────────────────

class HumanitarianPredictor(BasePredictor):
    """
    Predicts humanitarian category using TF-IDF + Logistic Regression.

    Returns top-3 predictions by default.
    """
    model_name = "humanitarian_tfidf_lr"
    task       = "humanitarian_category"
    MODEL_PATH = ROOT / "models" / "humanitarian" / "humanitarian_tfidf_lr.joblib"

    def _load(self):
        import joblib
        if not self.MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Model not found: {self.MODEL_PATH}\n"
                "Run: python training/train_humanitarian.py"
            )
        self._pipeline   = joblib.load(self.MODEL_PATH)
        self._label_names = HUMAID_CLASSES

    def predict(self, text: str, top_k: int = 3) -> Dict[str, Any]:
        """
        Predict humanitarian category.

        Parameters
        ----------
        text : str
        top_k : int, number of top predictions to return (default 3)

        Returns
        -------
        dict with keys:
            label        : str   — top predicted class
            confidence   : float — probability of top class
            top_k        : list of {"label": str, "confidence": float}
            model        : str
            task         : str
            cleaned_text : str
        """
        self._ensure_loaded()

        if not text or not isinstance(text, str) or not text.strip():
            return {"label": None, "confidence": 0.0, "top_k": [],
                    "model": self.model_name, "task": self.task,
                    "cleaned_text": "", "error": "empty_input"}

        cleaned = self._safe_clean(text)
        if not cleaned:
            return {"label": None, "confidence": 0.0, "top_k": [],
                    "model": self.model_name, "task": self.task,
                    "cleaned_text": cleaned, "error": "empty_after_cleaning"}

        probs   = self._pipeline.predict_proba([cleaned])[0]
        classes = self._pipeline.classes_

        top_indices = probs.argsort()[::-1][:top_k]
        top_preds   = [{"label": classes[i], "confidence": round(float(probs[i]), 4)}
                       for i in top_indices]

        return {
            "label":       classes[top_indices[0]],
            "confidence":  round(float(probs[top_indices[0]]), 4),
            "top_k":       top_preds,
            "model":       self.model_name,
            "task":        self.task,
            "cleaned_text": cleaned,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Relevance Predictor (TF-IDF + LR)
# ─────────────────────────────────────────────────────────────────────────────

class RelevancePredictor(BasePredictor):
    """
    Predicts humanitarian relevance (humanitarian / not_humanitarian).

    NOTE: This is a HUMANITARIAN RELEVANCE classifier derived from HumAID.
    It is not a general disaster/non-disaster detector.
    """
    model_name = "relevance_tfidf_lr"
    task       = "humanitarian_relevance"
    MODEL_PATH = ROOT / "models" / "relevance" / "relevance_tfidf_lr.joblib"

    def _load(self):
        import joblib
        if not self.MODEL_PATH.exists():
            raise FileNotFoundError(
                f"Model not found: {self.MODEL_PATH}\n"
                "Run: python training/train_relevance.py"
            )
        self._pipeline = joblib.load(self.MODEL_PATH)

    def predict(self, text: str) -> Dict[str, Any]:
        """
        Predict humanitarian relevance.

        Returns
        -------
        dict with:
            label        : "humanitarian" or "not_humanitarian"
            confidence   : float
            model        : str
            task         : str
            cleaned_text : str
            caveat       : str — labelling note
        """
        self._ensure_loaded()

        if not text or not isinstance(text, str) or not text.strip():
            return {"label": None, "confidence": 0.0,
                    "model": self.model_name, "task": self.task,
                    "cleaned_text": "", "error": "empty_input"}

        cleaned = self._safe_clean(text)
        probs   = self._pipeline.predict_proba([cleaned])[0]
        pred_id = int(self._pipeline.predict([cleaned])[0])
        conf    = float(probs[pred_id])

        return {
            "label":       RELEVANCE_NAMES[pred_id],
            "confidence":  round(conf, 4),
            "model":       self.model_name,
            "task":        self.task,
            "cleaned_text": cleaned,
            "caveat":      ("HUMANITARIAN RELEVANCE only. "
                            "not_humanitarian=0 maps to tweets lacking "
                            "humanitarian info, not to non-disaster content."),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Transformer Humanitarian Predictor
# ─────────────────────────────────────────────────────────────────────────────

class TransformerHumanitarianPredictor(BasePredictor):
    """
    Predicts humanitarian category using the canonical Phase 3 DistilBERT run
    (notebooks/phase_3/distilbert/). Does not use the smoke-test checkpoint.
    """
    model_name = "distilbert_humanitarian"
    task       = "humanitarian_category"
    MODEL_DIR  = CANONICAL_HUMANITARIAN_DISTILBERT

    def _load(self):
        self._load_error = None
        if not _transformer_checkpoint_ready(self.MODEL_DIR):
            self._load_error = {
                "error": "CHECKPOINT_UNAVAILABLE",
                "reason": (
                    "Canonical humanitarian DistilBERT checkpoint not found at "
                    f"{self.MODEL_DIR}. This is a large local/Kaggle artefact "
                    "(not stored in Git). Smoke-test weights at "
                    f"{SMOKE_TRANSFORMER_HUMANITARIAN} must not be used."
                ),
            }
            return
        try:
            import torch
            from transformers import (
                DistilBertTokenizerFast,
                DistilBertForSequenceClassification,
            )
        except ImportError as exc:
            self._load_error = {
                "error": "DEPENDENCY_UNAVAILABLE",
                "reason": (
                    "torch/transformers are required to load DistilBERT. "
                    f"{exc}"
                ),
            }
            return
        self._device    = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._tokenizer = DistilBertTokenizerFast.from_pretrained(str(self.MODEL_DIR))
        self._model     = DistilBertForSequenceClassification.from_pretrained(
            str(self.MODEL_DIR)
        )
        self._model.to(self._device)
        self._model.eval()
        self._label_names = HUMAID_CLASSES

    def predict(self, text: str, top_k: int = 3) -> Dict[str, Any]:
        self._ensure_loaded()
        if getattr(self, "_load_error", None):
            return {
                "label": None, "confidence": None, "top_k": [],
                "model": self.model_name, "task": self.task,
                "cleaned_text": "",
                **self._load_error,
            }

        if not text or not isinstance(text, str) or not text.strip():
            return {"label": None, "confidence": 0.0, "top_k": [],
                    "model": self.model_name, "task": self.task,
                    "cleaned_text": "", "error": "empty_input"}

        import torch
        import torch.nn.functional as F

        cleaned = self._safe_clean(text)
        enc = self._tokenizer(
            cleaned,
            truncation=True,
            padding="max_length",
            max_length=128,
            return_tensors="pt",
        )
        with torch.no_grad():
            logits = self._model(
                input_ids=enc["input_ids"].to(self._device),
                attention_mask=enc["attention_mask"].to(self._device),
            ).logits
        probs = F.softmax(logits, dim=-1)[0].cpu().numpy()

        top_indices = probs.argsort()[::-1][:top_k]
        top_preds   = [{"label": self._label_names[i],
                        "confidence": round(float(probs[i]), 4)}
                       for i in top_indices]

        return {
            "label":       self._label_names[top_indices[0]],
            "confidence":  round(float(probs[top_indices[0]]), 4),
            "top_k":       top_preds,
            "model":       self.model_name,
            "task":        self.task,
            "cleaned_text": cleaned,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Disaster Type — canonical Phase 3 DistilBERT (HumAID-event-type)
# ─────────────────────────────────────────────────────────────────────────────

class DisasterTypePredictor(BasePredictor):
    """
    Predicts disaster type using the canonical Phase 3 DistilBERT checkpoint
    (notebooks/phase_3/disaster_type/). Classes: earthquake, fire, flood, hurricane.

    If the large checkpoint is not present locally, predict() returns an explicit
    CHECKPOINT_UNAVAILABLE error rather than a stub NOT_IMPLEMENTED result.
    """
    model_name = "distilbert_disaster_type"
    task       = "disaster_type"
    MODEL_DIR  = CANONICAL_DISASTER_TYPE

    def _load(self):
        self._load_error = None
        if not _transformer_checkpoint_ready(self.MODEL_DIR):
            self._load_error = {
                "error": "CHECKPOINT_UNAVAILABLE",
                "reason": (
                    "Canonical disaster-type DistilBERT checkpoint not found at "
                    f"{self.MODEL_DIR}. Weights are a ~255 MB local artefact "
                    "(not stored in Git). See models/MODEL_MANIFEST.md."
                ),
            }
            return
        try:
            import torch
            from transformers import (
                DistilBertTokenizerFast,
                DistilBertForSequenceClassification,
            )
        except ImportError as exc:
            self._load_error = {
                "error": "DEPENDENCY_UNAVAILABLE",
                "reason": (
                    "torch/transformers are required to load DistilBERT. "
                    f"{exc}"
                ),
            }
            return
        self._device    = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self._tokenizer = DistilBertTokenizerFast.from_pretrained(str(self.MODEL_DIR))
        self._model     = DistilBertForSequenceClassification.from_pretrained(
            str(self.MODEL_DIR)
        )
        self._model.to(self._device)
        self._model.eval()
        cfg_labels = getattr(self._model.config, "id2label", None) or {}
        self._label_names = [
            cfg_labels.get(i, cfg_labels.get(str(i), DISASTER_TYPE_LABELS[i]))
            for i in range(len(DISASTER_TYPE_LABELS))
        ]

    def predict(self, text: str, top_k: int = 3) -> Dict[str, Any]:
        self._ensure_loaded()
        if getattr(self, "_load_error", None):
            return {
                "label": None, "confidence": None, "top_k": [],
                "model": self.model_name, "task": self.task,
                "cleaned_text": "",
                **self._load_error,
            }

        if not text or not isinstance(text, str) or not text.strip():
            return {"label": None, "confidence": 0.0, "top_k": [],
                    "model": self.model_name, "task": self.task,
                    "cleaned_text": "", "error": "empty_input"}

        import torch
        import torch.nn.functional as F

        cleaned = self._safe_clean(text)
        enc = self._tokenizer(
            cleaned,
            truncation=True,
            padding="max_length",
            max_length=128,
            return_tensors="pt",
        )
        with torch.no_grad():
            logits = self._model(
                input_ids=enc["input_ids"].to(self._device),
                attention_mask=enc["attention_mask"].to(self._device),
            ).logits
        probs = F.softmax(logits, dim=-1)[0].cpu().numpy()

        top_indices = probs.argsort()[::-1][:top_k]
        top_preds = [{"label": self._label_names[i],
                      "confidence": round(float(probs[i]), 4)}
                     for i in top_indices]

        return {
            "label":        self._label_names[top_indices[0]],
            "confidence":   round(float(probs[top_indices[0]]), 4),
            "top_k":        top_preds,
            "model":        self.model_name,
            "task":         self.task,
            "cleaned_text": cleaned,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Convenience wrapper — full pipeline prediction
# ─────────────────────────────────────────────────────────────────────────────

def predict_humanitarian(text: str, top_k: int = 3) -> Dict[str, Any]:
    """Predict humanitarian category (TF-IDF+LR)."""
    return HumanitarianPredictor().predict(text, top_k=top_k)


def predict_relevance(text: str) -> Dict[str, Any]:
    """Predict humanitarian relevance."""
    return RelevancePredictor().predict(text)


def predict_disaster_type(text: str) -> Dict[str, Any]:
    """Predict disaster type using the canonical Phase 3 DistilBERT model."""
    return DisasterTypePredictor().predict(text)


def predict_all(text: str) -> Dict[str, Any]:
    """
    Run all available classifiers on a single text.
    Returns a combined structured dict.
    """
    relevance     = predict_relevance(text)
    humanitarian  = predict_humanitarian(text)
    disaster_type = predict_disaster_type(text)
    return {
        "input_text":    text,
        "relevance":     relevance,
        "humanitarian":  humanitarian,
        "disaster_type": disaster_type,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Self-demo
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    DEMO_TEXTS = [
        "People are trapped under rubble in Karachi. Need rescue teams urgently!",
        "Red Cross is distributing food and water to 500 flood survivors.",
        "The bridge on highway 5 has completely collapsed after the earthquake.",
        "At least 47 people confirmed dead, 200 injured after the cyclone.",
        "Just watched a great movie tonight, nothing special happening.",
        "#FloodAlert evacuate low-lying areas immediately. #HurricaneIdai",
        "Our thoughts and prayers are with the people affected by the disaster.",
        "Missing: Maria Santos, 34, last seen near evacuation center 3.",
    ]

    hum_pred = HumanitarianPredictor()
    rel_pred = RelevancePredictor()

    print("=" * 70)
    print("Prediction Interface Demo")
    print("=" * 70)

    for text in DEMO_TEXTS:
        hum = hum_pred.predict(text)
        rel = rel_pred.predict(text)
        print(f"\nText: {text[:80]}")
        print(f"  Relevance    : {rel['label']}  (conf={rel['confidence']:.2f})")
        print(f"  Humanitarian : {hum['label']}  (conf={hum['confidence']:.2f})")
        print(f"  Top-3:")
        for p in hum.get("top_k", []):
            print(f"    {p['label']:<45} {p['confidence']:.3f}")
