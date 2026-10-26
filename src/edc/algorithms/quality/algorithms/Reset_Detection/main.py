import argparse
from pathlib import Path

from src.config import load_config
from src.detector import scan_episodes


def main():
    parser = argparse.ArgumentParser(description="Home position detection entry point")
    parser.add_argument(
        "--data-path", "-d",
        type=str,
        default=None,
        help="Path to the root data directory (overrides config file)",
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default="configs/default.yaml",
        help="Path to config YAML file",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=str,
        default=None,
        help="Path to output root directory (overrides config file)",
    )

    parser.add_argument(
        "--robot", "-r",
        type=str,
        default=None,
        help="Robot name (overrides config file, e.g. yuanli, quanta_x1)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Randomly sample N episodes for validation (default: all)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.data_path is not None:
        cfg.data_raw_path = args.data_path
    if args.robot is not None:
        cfg.robot_name = args.robot

    output_dir = args.output_dir or cfg.output_dir

    dataset_name = Path(cfg.data_raw_path).name + "_detection_output"

    results = scan_episodes(
        cfg.data_raw_path, cfg,
        output_dir=output_dir,
        dataset_name=dataset_name,
    )

    for assessment_type in ("Initialization", "Reset"):
        score_key = "first_frame_score" if assessment_type == "Initialization" else "last_frame_score"
        passed_key = "first_frame_passed" if assessment_type == "Initialization" else "last_frame_passed"
        load_error = sum(1 for r in results if r.get("status") == "load_error")
        success = sum(1 for r in results if r.get("status") != "load_error" and r.get(passed_key, True))
        failed = len(results) - success - load_error
        avg_score = sum(r.get(score_key, 0) for r in results) / len(results) if results else 0
        print(f"[{assessment_type}] Total: {len(results)} | PASS: {success} | FAIL: {failed} | LOAD_ERROR: {load_error} | Avg Score: {avg_score:.1f}")
    out_dir = Path(output_dir) / dataset_name
    print(f"Output -> {out_dir}")


if __name__ == "__main__":
    main()
