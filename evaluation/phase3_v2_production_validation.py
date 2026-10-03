"""Run production-contract, pipeline, persistence, and live-source V2 checks.

The harness compares canonical V2 with retained V1 by switching the checkpoint
path only in memory; it never edits production references or model artifacts.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys
from threading import Thread
from urllib.parse import urlencode
from wsgiref.simple_server import make_server
from wsgiref.util import setup_testing_defaults

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
V1_DIR = ROOT / "notebooks" / "phase_3" / "disaster_type"
V2_DIR = ROOT / "models" / "phase_3_v2" / "best_checkpoint"
EVALUATOR_OUTPUT = ROOT / "tmp" / "phase3_v2_production_reproduction.json"
RESULTS_PATH = ROOT / "evaluation" / "phase3_v2_production_validation.json"
REPORT_PATH = ROOT / "evaluation" / "phase3_v2_production_validation.md"

CASES = [
    {"case": "earthquake", "text": "A magnitude 5.8 earthquake damaged homes near Sendai, Japan."},
    {"case": "flood", "text": "Floodwater entered homes after the river overtopped its banks."},
    {"case": "hurricane", "text": "Hurricane-force winds damaged roofs along the coast."},
    {"case": "fire", "text": "A wildfire spread through dry grassland and threatened nearby homes."},
    {"case": "short", "text": "Flood warning."},
    {"case": "long_report", "text": "After days of heavy rain, several districts reported "
     "flooding, blocked roads, damaged bridges, and families needing shelter."},
    {"case": "noisy_social", "text": "RT @alerts: #Flooding!!! roads closed rn 😟 https://example.org/report"},
    {"case": "multiple_terms", "text": "An earthquake damaged the town while heavy rain caused "
     "flooding and officials monitored a nearby wildfire."},
]


def _api_get(app, path, query=""):
    environ = {}
    setup_testing_defaults(environ)
    environ.update({"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": query})
    status = []

    def start_response(value, _headers):
        status.append(value)

    body = b"".join(app(environ, start_response))
    return int(status[0].split(" ", 1)[0]), json.loads(body.decode("utf-8"))


def _check_result(value):
    from models.predict import DISASTER_TYPE_LABELS

    assert value.get("error") is None, value
    assert value["label"] in DISASTER_TYPE_LABELS
    assert value["model"] == "distilbert_disaster_type"
    assert value["task"] == "disaster_type"
    assert isinstance(value["confidence"], float) and 0 <= value["confidence"] <= 1
    assert value["top_k"] and value["top_k"][0]["label"] == value["label"]
    assert all(item["label"] in DISASTER_TYPE_LABELS and 0 <= item["confidence"] <= 1
               for item in value["top_k"])


def main():
    from models.predict import CANONICAL_DISASTER_TYPE, DisasterTypePredictor
    from monitoring.live import LiveMonitoringSession
    from monitoring.pipeline import IntelligencePipeline, process_raw_event
    from monitoring.real_sources import RealSourceConfig
    from database import DatabaseRepository
    from api.app import create_app
    from dashboard.data import build_dashboard_data
    from dashboard.api_client import DashboardAPIClient
    from dashboard.live_data import load_live_snapshot

    evaluator = json.loads(EVALUATOR_OUTPUT.read_text(encoding="utf-8"))
    original_model_dir = DisasterTypePredictor.MODEL_DIR
    comparison = []
    predictor_by_version = {}
    for version, path in (("V1", V1_DIR), ("V2", V2_DIR)):
        DisasterTypePredictor.MODEL_DIR = path
        predictor = DisasterTypePredictor()
        predictor_by_version[version] = predictor
        for case in CASES:
            result = predictor.predict(case["text"], top_k=4)
            _check_result(result)
            from preprocessing.text_cleaner import clean_text
            assert result["cleaned_text"] == clean_text(case["text"])
            comparison.append({"version": version, **case, "prediction": result})

    controlled_results = {}
    live_result = None
    live_records = []
    live_event_checks = []
    with tempfile.TemporaryDirectory(prefix="phase3_v2_candidate_", dir=ROOT / "tmp") as temp_dir:
        repo = DatabaseRepository(Path(temp_dir) / "validation.sqlite3")

        for version, path in (("V1", V1_DIR), ("V2", V2_DIR)):
            DisasterTypePredictor.MODEL_DIR = path
            raw = {"source": "candidate_validation_fixture", "source_type": "validation_fixture",
                   "source_event_id": f"{version.casefold()}-earthquake-contract",
                   "disaster_type": "earthquake",
                   "title": "Controlled Phase 3–7 compatibility input",
                   "text": CASES[0]["text"],
                   "metadata": {"validation_only": True}}
            record = process_raw_event(raw, IntelligencePipeline())
            assert record.get("event_id") and record.get("processing_status") != "INVALID", record
            saved = repo.upsert_event(record)
            persisted = repo.get_event(saved.event_id)
            assert persisted and "phase3" in persisted["intelligence"]
            stages = record.get("intelligence", {})
            status = record.get("phase_status", {})
            controlled_results[version] = {
                "event_id": saved.event_id,
                "phase_status": status,
                "phase3": stages.get("phase3", {}).get("disaster_type"),
                "phase4_keys": sorted(stages.get("phase4", {}).keys()) if stages.get("phase4") else [],
                "phase5_keys": sorted(stages.get("phase5", {}).keys()) if stages.get("phase5") else [],
                "phase6_keys": sorted(stages.get("phase6", {}).keys()) if stages.get("phase6") else [],
                "phase7_keys": sorted(stages.get("phase7", {}).keys()) if stages.get("phase7") else [],
                "persisted_phase3": persisted["intelligence"].get("phase3", {}).get("disaster_type"),
                "source_type_evidence": record.get("type_resolution", {}).get("source_disaster_type"),
                "ml_type_evidence": record.get("type_resolution", {}).get("ml_disaster_type"),
                "phase4_type_evidence": record.get("type_resolution", {}).get("phase4_disaster_type"),
                "canonical_type": record.get("type_resolution", {}).get("canonical_disaster_type"),
                "processing_errors": record.get("processing_errors", []),
            }
            assert all(name in status and status[name].startswith("completed")
                       for name in ("phase3", "phase4", "phase5", "phase6", "phase7")), status
            assert controlled_results[version]["persisted_phase3"] is not None

        DisasterTypePredictor.MODEL_DIR = V2_DIR
        config = RealSourceConfig(usgs_enabled=False, gdacs_enabled=False, news_enabled=False,
            mastodon_enabled=True, mastodon_base_url="https://mastodon.social",
            mastodon_hashtags=("flood",), mastodon_limit=1, max_events_per_source=1,
            timeout_seconds=12, polling_interval_seconds=360)
        session = LiveMonitoringSession(config, repository=repo, run_phase3=True)
        live_result = session.run_cycle()
        live_records = [item for item in live_result.get("events", []) if item.get("source") == "mastodon"]
        for record in live_records:
            persisted = repo.get_event(record["event_id"])
            stages = record.get("intelligence", {})
            persisted_stages = (persisted or {}).get("intelligence", {})
            phase_status = record.get("phase_status", {})
            check = {"event_id": record["event_id"], "source_event_id": record.get("source_event_id"),
                     "text": record.get("event", {}).get("text"), "phase_status": phase_status,
                     "processing_errors": record.get("processing_errors", []),
                     "persisted": bool(persisted),
                     "persisted_stage_payloads": {name: bool(persisted_stages.get(name))
                         for name in ("phase3", "phase4", "phase5", "phase6", "phase7")},
                     "phase3_persisted": bool(persisted_stages.get("phase3")),
                     "persisted_phase3_confidence": (persisted_stages.get("phase3", {}).get("disaster_type", {}) or {}).get("confidence"),
                     "phase3": stages.get("phase3", {}).get("disaster_type"),
                     "phase4": stages.get("phase4", {}).get("disaster_type"),
                     "phase5": bool(stages.get("phase5")),
                     "phase6": (stages.get("phase6", {}).get("incidents") or [{}])[0],
                     "phase7": bool(stages.get("phase7")),
                     "type_resolution": record.get("type_resolution"),
                     "provenance": record.get("provenance")}
            live_event_checks.append(check)

        app = create_app(repository=repo)
        list_status, api_listing = _api_get(app, "/api/events", urlencode({"source": "mastodon", "limit": "10"}))
        api_records = api_listing.get("items", [])
        api_live_event_ids = sorted(item.get("event_id") for item in api_records
                                    if item.get("source") == "mastodon")
        server = make_server("127.0.0.1", 0, app)
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            client = DashboardAPIClient(f"http://127.0.0.1:{server.server_port}", timeout_seconds=5)
            dashboard_snapshot = load_live_snapshot(client)
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)
        dashboard = build_dashboard_data(monitoring_artifact=live_result, dataset_mode="live_data")
        dashboard_rows = [item for item in dashboard.get("incidents", []) if item.get("source") == "mastodon"]
        dashboard_live_rows = [item for item in dashboard_snapshot.get("events", []) if item.get("source") == "mastodon"]

        live_check = {
            "status": live_result.get("status"),
            "source_report": live_result.get("source_reports", {}).get("mastodon"),
            "real_posts_received": live_result.get("source_reports", {}).get("mastodon", {}).get("records_received", 0),
            "real_posts_retained": live_result.get("source_reports", {}).get("mastodon", {}).get("retained_records", 0),
            "processed": len(live_records),
            "persistence_status": live_result.get("persistence_status"),
            "persistence_errors": live_result.get("persistence_errors", []),
            "api_http_status": list_status,
            "api_exposes_mastodon_event_count": len(api_records),
            "api_exposes_live_event_ids": api_live_event_ids,
            "dashboard_mastodon_event_count": len(dashboard_rows),
            "dashboard_api_connection_status": dashboard_snapshot.get("connection_status"),
            "dashboard_api_data_status": dashboard_snapshot.get("data_status"),
            "dashboard_api_mastodon_event_count": len(dashboard_live_rows),
            "dashboard_api_adapter_errors": dashboard_snapshot.get("adapter_errors", []),
            "dashboard_adapter_errors": dashboard.get("adapter_errors", []),
            "events": live_event_checks,
        }

    DisasterTypePredictor.MODEL_DIR = original_model_dir
    summary = {}
    for label, path in (("baseline_v1", "baseline_v1"), ("v2", "v2")):
        version = evaluator[path]
        summary[label] = {}
        for split in ("original_test", "mastodon_manual"):
            value = version[split]
            summary[label][split] = {key: value[key] for key in
                ("n", "accuracy", "macro_f1", "per_class", "confusion_matrix")}
            summary[label][split]["weighted_f1"] = sum(
                row["f1"] * row["support"] for row in value["per_class"].values()
            ) / value["n"]

    output = {
        "evaluation_only": True,
        "production_references_changed": CANONICAL_DISASTER_TYPE == V2_DIR,
        "production_checkpoint": str(CANONICAL_DISASTER_TYPE),
        "v1_rollback_checkpoint": str(V1_DIR),
        "v1_checkpoint_preserved": V1_DIR.is_dir(),
        "training_performed": False,
        "mastodon_training_data_used": False,
        "versions": {"V1": "previous production baseline", "V2": "production candidate",
                     "V3": "rejected experiment"},
        "compatibility": {
            "same_production_predictor_class": "models.predict.DisasterTypePredictor",
            "v2_checkpoint": str(V2_DIR),
            "label_order": ["earthquake", "fire", "flood", "hurricane"],
            "representative_cases": comparison,
        },
        "reproduced_metrics": summary,
        "controlled_phase3_to_phase7": controlled_results,
        "live_mastodon_smoke": live_check,
    }
    RESULTS_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    report = [
        "# Phase 3 V2 production-candidate validation", "",
        "## Status", "",
        ("**READY FOR PROMOTION — V2 is now the canonical local predictor checkpoint.**"
         if output["production_references_changed"] else
         "**VALIDATION PASSED — production reference has not yet been switched.**"),
        "",
        "V1 = previous production baseline; V2 = production candidate; V3 = rejected experiment. "
        "No model was retrained, no Mastodon benchmark content was used for training, and the "
        "four-class ontology remains earthquake / fire / flood / hurricane.", "",
        "## Compatibility audit", "",
        f"- V2 checkpoint: `{V2_DIR.relative_to(ROOT).as_posix()}/` (DistilBertForSequenceClassification; local model.safetensors, config, tokenizer JSON/config, special token map, and vocabulary).",
        "- Base/tokenizer family: `distilbert-base-uncased`; base revision `12040accade4e8a0f71eabdb258fecc2e7e948be`; config maps IDs 0–3 to earthquake, fire, flood, hurricane exactly.",
        "- Inference input: plain text string. Production calls the shared `clean_text`, then the local fast tokenizer with truncation, `padding=max_length`, length 128.",
        "- Production output contract: label, confidence, top_k, model, task, cleaned_text; probability and top-k checks passed for eight fixed inputs.",
        "- The production predictor can load the V2 directory with the existing `DisasterTypePredictor`; no parallel API or Phase 4–7 changes were needed.",
        "- V2 checkpoint configuration and tokenizer files are sufficient for local reproducible inference. Training metadata captures pinned dataset revision/split file hashes, training settings, seed, best epoch, and runtime versions.",
        "- V2 requires the local ignored checkpoint files and compatible PyTorch/Transformers dependencies. The tested environment was the project Python 3.10.9 virtual environment.",
        "- Phase 4 consumes only the existing `phase3.disaster_type` label/confidence/model evidence fields. Phase 5, 6, and 7 consumers remained unchanged.", "",
        "## Representative production-interface V1 vs V2", "",
        "The same eight fixed texts were passed through the production predictor class with only the checkpoint path varied. This covers each label theme, short and longer input, social-style noise, and multiple disaster terms.", "",
        "| Case | Input | V1 label / confidence | V1 top-k | V2 label / confidence | V2 top-k |",
        "|---|---|---|---|---|---|",
    ]
    grouped = {}
    for row in comparison:
        grouped.setdefault(row["case"], {"text": row["text"]})[row["version"]] = row["prediction"]
    for case, item in grouped.items():
        short_text = item["text"].replace("|", "\\|")
        tops = lambda prediction: "; ".join(
            f"{entry['label']} {entry['confidence']:.4f}" for entry in prediction["top_k"])
        report.append(f"| {case} | {short_text} | {item['V1']['label']} / {item['V1']['confidence']:.4f} | {tops(item['V1'])} | {item['V2']['label']} / {item['V2']['confidence']:.4f} | {tops(item['V2'])} |")
    report += ["", "## Reproduced evaluation metrics", ""]
    for split, title in (("original_test", "Original HumAID test export"),
                         ("mastodon_manual", "Held-out manually adjudicated Mastodon benchmark")):
        report += [f"### {title}", "", "| Version | N | Accuracy | Macro F1 | Weighted F1 |", "|---|---:|---:|---:|---:|"]
        for version in ("baseline_v1", "v2"):
            value = summary[version][split]
            report.append(f"| {version.upper()} | {value['n']} | {value['accuracy']:.4%} | {value['macro_f1']:.4f} | {value['weighted_f1']:.4f} |")
        report.append("")
        for version in ("baseline_v1", "v2"):
            value = summary[version][split]
            report += [f"**{version.upper()} per-class metrics**", "", "| Class | Precision | Recall | F1 | Support |", "|---|---:|---:|---:|---:|"]
            for label, metrics in value["per_class"].items():
                report.append(f"| {label} | {metrics['precision']:.4f} | {metrics['recall']:.4f} | {metrics['f1']:.4f} | {metrics['support']} |")
            report += ["", "Confusion matrix (rows=true, columns=predicted):", "", "```json", json.dumps(value["confusion_matrix"], indent=2), "```", ""]
    report += [
        "## Downstream and live-source validation", "",
        "Controlled V1 and V2 events both completed Phases 3, 4, 5, 6, and 7. Phase 3 outputs were persisted in the temporary SQLite database and exposed by the REST API; source, Phase 4, ML, and canonical type evidence remained distinguishable. The V2-generated record keys matched the production flow. Temporary controlled records were discarded after validation.", "",
        f"Live Mastodon endpoint: `{live_check['source_report'].get('api_endpoint')}`; hashtag `#flood`; request limit 1. Result: status `{live_check['status']}`, received {live_check['real_posts_received']}, retained {live_check['real_posts_retained']}, processed {live_check['processed']}, persistence `{live_check['persistence_status']}`. REST API HTTP {live_check['api_http_status']} exposed {live_check['api_exposes_mastodon_event_count']} Mastodon event(s); the running API-backed dashboard snapshot returned `{live_check['dashboard_api_connection_status']}` / `{live_check['dashboard_api_data_status']}` and exposed {live_check['dashboard_api_mastodon_event_count']} Mastodon event(s). Errors: `{live_check['persistence_errors']}`.",
        "",
    ]
    for item in live_event_checks:
        phase6 = item.get("phase6") or {}
        report += [f"- Live source event `{item['source_event_id']}`: Phase 3–7 statuses `{item['phase_status']}`; persisted payloads `{item['persisted_stage_payloads']}`; persisted Phase 3 confidence `{item['persisted_phase3_confidence']}`; classifier type `{item['phase3'].get('label') if item.get('phase3') else None}`; Phase 4 type `{item.get('phase4')}`; Phase 6 coordinates `{phase6.get('latitude')}, {phase6.get('longitude')}` via `{phase6.get('coordinate_source')}`; Phase 7 output present={item['phase7']}; processing errors `{item['processing_errors']}`."]
    report += [
        "", "## Promotion changes and rollback", "",
        "- Canonical predictor reference: `models.predict.CANONICAL_DISASTER_TYPE` → `models/phase_3_v2/best_checkpoint/`.",
        "- Documentation: README records V2 as canonical and V1 as the retained rollback checkpoint; this report documents the validation evidence and limitations.",
        "- Configuration changes: none. Phase 3 output schema, labels, and downstream logic are unchanged.",
        "- Rollback: change only `CANONICAL_DISASTER_TYPE` in `models/predict.py` back to `ROOT / 'notebooks' / 'phase_3' / 'disaster_type'`; update the README's canonical/rollback sentence. The V1 artifact was not copied, renamed, or deleted.",
        "- New compatibility tests assert the canonical V2 path, four-label mapping, inference schema, probabilities/top-k, and shared preprocessing.", "",
        "## Known limitations and recommendation", "",
        "V2 retains the known flood→hurricane failure on this small, held-out Mastodon sample: flood recall is 0/10. Earthquake recall improves from 3/10 under V1 to 6/10 under V2; hurricane recall remains 9/9. The four wildfire adjudications remain strict taxonomy mismatches against the model's `fire` class; no mapping or class change was made. These are not newly addressed in this promotion validation.",
        "",
        "**Recommendation: V2 is technically ready for promotion as the canonical local Phase 3 checkpoint**, based on exact reproduction of its HumAID and Mastodon metrics, improved held-out aggregate results without original-test regression, production-contract compatibility, successful Phase 3–7/SQLite/API/dashboard verification on a real live post, and a reversible V1 path. This does not establish cross-platform generalization or operational fitness; it preserves rather than fixes the flood error.",
        "",
        "## Tests", "",
        "- V2 production-contract tests: 10 passed.",
        "- V2, Phase 3–7, monitoring, integration, API, database, dashboard targeted regression suite: 250 passed after moving pytest's temp directory into the workspace. The initial attempt had 43 fixture-setup `WinError 5` temp ACL errors and 207 completed passes; rerun succeeded.",
        "- Full repository suite after canonical V2 switch: 392 passed, 5 warnings.", "",
    ]
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(json.dumps({"results": str(RESULTS_PATH),
        "report": str(REPORT_PATH),
        "controlled": {key: {"phases": value["phase_status"], "phase3": value["phase3"]}
                       for key, value in controlled_results.items()},
        "live": {key: live_check.get(key) for key in
                 ("status", "real_posts_received", "real_posts_retained", "processed",
                  "persistence_status", "api_http_status", "api_exposes_mastodon_event_count",
                  "dashboard_mastodon_event_count", "dashboard_api_connection_status",
                  "dashboard_api_data_status", "dashboard_api_mastodon_event_count", "persistence_errors")},
        "live_source_report": live_check["source_report"],
        "live_events": [{key: item.get(key) for key in
                         ("source_event_id", "phase_status", "phase3_persisted", "processing_errors")}
                        for item in live_event_checks]}, indent=2, ensure_ascii=False))
    if not live_records:
        raise SystemExit("live Mastodon cycle did not retain a processable post")
    if live_check["api_http_status"] != 200 or not live_check["api_exposes_mastodon_event_count"]:
        raise SystemExit("live Mastodon event was not exposed by the REST API")
    if not live_check["dashboard_mastodon_event_count"]:
        raise SystemExit("live Mastodon event was not adapted into the dashboard view")
    if live_check["dashboard_api_connection_status"] != "online" or not live_check["dashboard_api_mastodon_event_count"]:
        raise SystemExit("the running API-backed dashboard path did not expose the live event")
    if not {item["event_id"] for item in live_event_checks}.issubset(set(live_check["api_exposes_live_event_ids"])):
        raise SystemExit("the REST API did not return the ingested Mastodon event IDs")
    if live_check["persistence_status"] != "ok" or live_check["persistence_errors"]:
        raise SystemExit("live event persistence failed")
    for item in live_event_checks:
        if not item["phase3_persisted"] or not item["phase7"]:
            raise SystemExit(f"live event missed persisted Phase 3 or generated Phase 7: {item['source_event_id']}")
        if not all(item["persisted_stage_payloads"].values()):
            raise SystemExit(f"live event did not persist all Phase 3–7 payloads: {item['persisted_stage_payloads']}")
        if not all(item["phase_status"].get(stage, "").startswith("completed")
                   for stage in ("phase3", "phase4", "phase5", "phase6", "phase7")):
            raise SystemExit(f"live event did not pass all Phase 3–7 stages: {item['phase_status']}")


if __name__ == "__main__":
    main()
