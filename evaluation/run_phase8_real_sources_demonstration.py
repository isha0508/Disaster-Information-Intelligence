"""One-shot LIVE request demonstration for USGS, GDACS, and configured RSS."""

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Phase 3 uses only the local model/runtime; never download artifacts as a side effect.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from monitoring.live import LiveMonitoringSession
from monitoring.normalization import utc_now
from monitoring.real_sources import RealSourceConfig


OUTPUT = ROOT / "evaluation" / "phase8_real_sources_results.json"


def main():
    config = RealSourceConfig.from_env()
    session = LiveMonitoringSession(config)
    cycle = session.run_cycle()  # real HTTP requests; never substitute synthetic records
    artifact = {"run_mode": "live", "label": "LIVE DATA — external public feeds",
                "synthetic_fallback": False,
                "retrieval_timestamp": cycle.get("retrieval_timestamp") or utc_now(),
                "source_configuration": {"usgs_enabled": config.usgs_enabled,
                                         "gdacs_enabled": config.gdacs_enabled,
                                         "news_enabled": config.news_enabled,
                                         "rss_feed_count": len(config.rss_feeds),
                                         "max_events_per_source": config.max_events_per_source,
                                         "polling_interval_seconds": config.polling_interval_seconds,
                                         "timeout_seconds": config.timeout_seconds},
                "sources": cycle["source_reports"], "events": cycle["events"],
                "alerts": cycle["alerts"], "summary": cycle["summary"],
                "limitations": [
                    "Live retrieval confirms feed access only; it does not validate source accuracy or operational fitness.",
                    "USGS and GDACS source facts remain source facts and do not replace Phase 5 intelligence scores.",
                    "Phase 3–7 failures are retained on each processed event; no downstream result is fabricated.",
                    "Phase 9 uses its existing rule-based evaluator and local mock notification behavior.",
                    "RSS keyword filtering can exclude a relevant article with no configured keyword; inspect feed reports and tune the list.",
                ]}
    OUTPUT.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print("PHASE 8 REAL-SOURCE INGESTION DEMONSTRATION - LIVE DATA")
    print(f"Retrieved at: {artifact['retrieval_timestamp']}")
    for name, report in artifact["sources"].items():
        print(f"{name}: status={report['status']} received={report.get('records_received', 0)} "
              f"valid={report.get('valid_records', 0)} processed={report.get('selected_for_processing', 0)} "
              f"NEW={report.get('new_records', 0)} DUPLICATE={report.get('duplicate_records', 0)} "
              f"UPDATED={report.get('updated_records', 0)}")
        if report.get("error"):
            print(f"  error: {report['error']}")
        for feed in report.get("feeds", []):
            print(f"  feed={feed.get('feed_url')} status={feed['status']} "
                  f"received={feed.get('records_received', 0)}")
            if feed.get("error"):
                print(f"    error: {feed['error']}")
    print(f"Events retrieved and selected for Phase 3-7 processing: {len(artifact['events'])}")
    print(f"Alerts evaluated by Phase 9: {len(artifact['alerts'])}")
    print(f"JSON artifact: {OUTPUT}")
    if all(item["status"] in {"error", "not_configured"} for item in artifact["sources"].values()):
        print("No live source returned data. This artifact records the failures/configuration as-is; no demo data was substituted.")
    return artifact


if __name__ == "__main__":
    main()
