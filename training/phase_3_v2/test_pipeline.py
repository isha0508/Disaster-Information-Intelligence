"""Focused unit tests for the isolated Phase 3 V2 data/evaluation helpers."""
import unittest
from training.phase_3_v2.dataset import LABELS, remove_training_leakage
from training.phase_3_v2.evaluate import _metric_summary
class DatasetPreparationTests(unittest.TestCase):
    def test_class_order_preserves_fire_and_hurricane(self):
        self.assertEqual(LABELS, ("earthquake", "fire", "flood", "hurricane"))
    def test_training_only_duplicate_removal_preserves_dev_and_test(self):
        source = {
            "train": [
                {"text": "shared", "label": "earthquake"},
                {"text": "train only", "label": "flood"},
            ],
            "dev": [{"text": "shared", "label": "hurricane"}],
            "test": [{"text": "test only", "label": "fire"}],
        }
        cleaned, report = remove_training_leakage(source)
        self.assertEqual([r["text"] for r in cleaned["train"]], ["train only"])
        self.assertEqual(cleaned["dev"], source["dev"])
        self.assertEqual(cleaned["test"], source["test"])
        self.assertEqual(report["training_exact_text_rows_removed"], 1)
        self.assertTrue(report["dev_count_unchanged"])
        self.assertTrue(report["test_count_unchanged"])
    def test_benchmark_wildfire_is_not_normalized_to_model_fire(self):
        result = _metric_summary(
            ["earthquake", "wildfire"],
            ["earthquake", "fire"],
            [0.99, 0.99],
            ("earthquake", "wildfire"),
        )
        self.assertEqual(result["accuracy"], 0.5)
        self.assertEqual(result["per_class"]["wildfire"]["f1"], 0.0)
        self.assertEqual(result["confusion_matrix"]["counts"]["wildfire"]["fire"], 1)
if __name__ == "__main__":
    unittest.main()
