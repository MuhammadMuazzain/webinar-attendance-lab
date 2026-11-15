from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.pipeline import AttendancePipeline


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Zoom meeting/webinar participants into local logs")
    parser.add_argument("meeting_id", help="Zoom meeting or webinar ID")
    parser.add_argument(
        "--mode",
        default="auto",
        choices=["auto", "live", "past_meeting", "past_webinar"],
    )
    args = parser.parse_args()
    result = await AttendancePipeline().sync_meeting(args.meeting_id, mode=args.mode)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
