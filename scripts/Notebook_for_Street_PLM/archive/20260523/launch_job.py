"""
launch_job.py
=============
Submit street_plm_job.py as a Lightning AI Job on an L4 GPU (24 GB).

Usage (run this locally or from the Studio terminal):
    python launch_job.py                     # submit and tail logs
    python launch_job.py --machine l4        # default
    python launch_job.py --machine a100      # upgrade to A100 80 GB
    python launch_job.py --dry-run           # print config without submitting

Teamspace : pipeline-automation-project
Studio    : teammate-2-pipeline-devbox
"""

import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from lightning_sdk import Job, Machine, Studio

# ---------------------------------------------------------------------------
# Load credentials from .env (same directory as this script)
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent / ".env")

# ---------------------------------------------------------------------------
# Lightning AI project coordinates
# ---------------------------------------------------------------------------
TEAMSPACE = "pipeline-automation-project"
STUDIO    = "teammate-2-pipeline-devbox"

# ---------------------------------------------------------------------------
# Machine options
# ---------------------------------------------------------------------------
_MACHINE_MAP = {
    "l4"      : Machine.L4,
    "l4x2"    : Machine.L4_X_2,
    "a100"    : Machine.A100_80GB,
    "a10g"    : Machine.L40S,
    "t4"      : Machine.T4,
}

# ---------------------------------------------------------------------------
# Secrets to forward from the local environment into the job
# ---------------------------------------------------------------------------
_SECRET_KEYS = [
    "GOOGLE_STREETVIEW_API_KEY",
    "HF_TOKEN",
    "GCP_PROJECT_ID",
]


def _parse_args():
    p = argparse.ArgumentParser(description="Submit StreetPLM job to Lightning AI")
    p.add_argument(
        "--machine",
        default="l4",
        choices=list(_MACHINE_MAP),
        help="GPU machine type (default: l4)",
    )
    p.add_argument(
        "--job-name",
        default="street-plm-eixample",
        help="Job name shown in the Lightning AI dashboard",
    )
    p.add_argument(
        "--output-dir",
        default="/teamspace/studios/this_studio/StreetPLM",
        help="Output directory inside the job container",
    )
    p.add_argument(
        "--trial",
        action="store_true",
        help="Run in trial mode (one location, no results written)",
    )
    p.add_argument(
        "--max-runtime",
        type=int,
        default=12 * 3600,   # 12 h — 2248 images at ~15 s/image ≈ 7 h on L4
        help="Max job runtime in seconds (default: 43200 = 12 h)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the job config without actually submitting",
    )
    return p.parse_args()


def main():
    args = _parse_args()

    machine = _MACHINE_MAP[args.machine]

    # Build the command
    cmd_parts = ["python street_plm_job.py", f"--output-dir {args.output_dir}"]
    if args.trial:
        cmd_parts.append("--trial")
    command = " ".join(cmd_parts)

    # Collect env vars to forward (secrets must be set in local env or .env)
    env = {}
    missing = []
    for key in _SECRET_KEYS:
        val = os.environ.get(key, "")
        if val:
            env[key] = val
        else:
            missing.append(key)

    if missing:
        print(f"WARNING: the following secrets are not set and will NOT be forwarded:")
        for k in missing:
            print(f"  - {k}")
        print("Set them in .env or as environment variables before submitting.\n")

    env["OUTPUT_DIR"] = args.output_dir

    if args.dry_run:
        print("=== Dry run — job would be submitted with: ===")
        print(f"  Teamspace : {TEAMSPACE}")
        print(f"  Studio    : {STUDIO}")
        print(f"  Job name  : {args.job_name}")
        print(f"  Machine   : {args.machine}  ({machine})")
        print(f"  Command   : {command}")
        print(f"  Max runtime: {args.max_runtime // 3600} h ({args.max_runtime} s)")
        print(f"  Env keys  : {list(env.keys())}")
        return

    # Connect to the studio that owns the environment
    print(f"Connecting to studio '{STUDIO}' in teamspace '{TEAMSPACE}' ...")
    studio = Studio(STUDIO, teamspace=TEAMSPACE)
    studio.install_plugin("jobs")
    jobs = studio.installed_plugins["jobs"]

    print(f"Submitting job '{args.job_name}' on {args.machine.upper()} ...")
    job = jobs.run(
        command     = command,
        name        = args.job_name,
        machine     = machine,
        env         = env,
        max_runtime = args.max_runtime,
    )

    print(f"\nJob submitted!")
    print(f"  Name    : {job.name}")
    print(f"  Machine : {args.machine.upper()}")
    print(f"  Status  : {job.status}")
    print(f"\nTailing logs (Ctrl+C to detach — job keeps running)...")

    try:
        while True:
            status = job.status
            if status in ("succeeded", "failed", "stopped"):
                print(f"\nJob finished with status: {status}")
                break
            time.sleep(10)
    except KeyboardInterrupt:
        print(f"\nDetached from log tail. Job is still running.")
        print(f"Check status at: https://lightning.ai/{TEAMSPACE}/jobs/{job.name}")


if __name__ == "__main__":
    main()
