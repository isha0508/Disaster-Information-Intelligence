"""CLI for one-shot or deliberately paced public-feed ingestion."""

import argparse
import json
import time

from monitoring.live import LiveMonitoringSession
from monitoring.normalization import utc_now
from monitoring.real_sources import RealSourceConfig
from database import DatabaseRepository


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--poll", action="store_true", help="poll repeatedly at the configured interval")
    mode.add_argument("--once", action="store_true", help="perform one live fetch cycle (default)")
    parser.add_argument("--cycles", type=int, default=None, help="stop polling after N cycles")
    parser.add_argument("--disable-phase3", action="store_true", help="skip the existing Phase 3 classifier")
    parser.add_argument("--db-path", default=None, help="optional SQLite database path override")
    args = parser.parse_args(argv)
    config = RealSourceConfig.from_env()
    if args.cycles is not None and args.cycles < 1:
        parser.error("--cycles must be positive")
    if args.cycles is not None and not args.poll:
        parser.error("--cycles requires --poll")
    repository = DatabaseRepository(args.db_path)
    session = LiveMonitoringSession(config, run_phase3=not args.disable_phase3,
                                    repository=repository)
    cycle = 0
    try:
        while True:
            cycle += 1
            result = session.run_cycle()
            print(f"LIVE INGESTION CYCLE | cycle {cycle} | {result['retrieval_timestamp'] or utc_now()}")
            if not result["source_reports"]:
                print("sources: none configured")
            for name, report in result["source_reports"].items():
                print(f"{name}: status={report['status']} received={report.get('records_received', 0)} "
                      f"processed={report.get('selected_for_processing', 0)} "
                      f"new={report.get('new_records', 0)} duplicate={report.get('duplicate_records', 0)} "
                      f"updated={report.get('updated_records', 0)}")
                if report.get("error"):
                    print(f"  error={report['error']}")
            print(json.dumps(result["summary"], sort_keys=True))
            print(f"database_persistence={result['persistence_status']} "
                  f"monitoring_run_id={result['monitoring_run_id']} "
                  f"alerts_generated={sum(bool(item.get('alert_record')) for item in result['alerts'])} "
                  f"alerts_evaluated={len(result['alerts'])}")
            for event in result.get("events", []):
                for error in event.get("processing_errors", []):
                    print(f"  processing_error={error.get('phase', 'unknown')}: {error.get('message', 'processing failed')}")
            for failure in result.get("persistence_errors", []):
                print(f"  persistence_error={failure.get('error_type')}: {failure.get('message')}")
            if not args.poll or (args.cycles is not None and cycle >= args.cycles):
                return 0
            time.sleep(config.polling_interval_seconds)
    except KeyboardInterrupt:
        print("Live polling stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
