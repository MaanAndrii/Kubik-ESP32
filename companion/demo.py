"""Development-only state producer; never substitutes for DeskOne's rules."""
import argparse
import math
import os
import time
from dataclasses import dataclass, replace
from companion.bridge import CompanionBridge

@dataclass(frozen=True)
class DemoFrame:
    emotion: str
    location: str
    held_item: str | None
    wearables: tuple[str, ...] = ()
    contexts: frozenset[str] = frozenset()
    held_visibility: float = 1.0
    timer_progress: float = 0.0

# These are fixed protocol fixtures, not a second implementation of the resolver.
SCENARIOS = {
    "awake": ("ACTIVE", False, DemoFrame("CALM", "HOME", "cup",
                                       contexts=frozenset({"CUP"}))),
    "work": ("WORK", False, DemoFrame("FOCUSED", "OFFICE", "laptop",
                         ("reading_glasses",), frozenset({"WORKING"}))),
    "rain": ("ACTIVE", False, DemoFrame("CALM", "STREET", "umbrella",
                                       contexts=frozenset({"RAIN"}))),
    "sleep": ("SLEEP", True, DemoFrame("SLEEPING", "HOME", None,
                  ("nightcap", "sleep_mask"), frozenset({"SLEEP_WEAR"}), 0.0)),
}

class DemoModel:
    def __init__(self, scenario="awake"):
        if scenario not in SCENARIOS:
            raise ValueError("Unknown scenario")
        self.scenario = scenario
        self.reaction_until = 0.0
        self.accepted_touches = 0

    def select(self, scenario):
        if scenario not in SCENARIOS:
            raise ValueError("Unknown scenario")
        self.scenario = scenario
        self.reaction_until = 0.0

    def touch(self, now):
        if SCENARIOS[self.scenario][1]:
            return False
        self.accepted_touches += 1
        self.reaction_until = now + 2.0
        return True

    def snapshot(self, now):
        mode, sleeping, scene = SCENARIOS[self.scenario]
        if not sleeping and now < self.reaction_until:
            scene = replace(scene, emotion="HAPPY")
        return scene, mode, sleeping

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1",
                        help="Use 0.0.0.0 for a board on the trusted LAN")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--scenario", choices=SCENARIOS, default="awake")
    parser.add_argument("--cycle", type=float, default=0,
                        help="Seconds per scenario; 0 disables cycling")
    parser.add_argument("--duration", type=float,
                        help="Stop after this many seconds (for smoke tests)")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("Port must be 0..65535")
    if not math.isfinite(args.cycle) or args.cycle < 0:
        parser.error("Cycle must be finite and nonnegative")
    if args.duration is not None and (
            not math.isfinite(args.duration) or args.duration <= 0):
        parser.error("Duration must be finite and positive")
    try:
        bridge = CompanionBridge(args.host, args.port,
                                 os.getenv("KUBIK_BRIDGE_TOKEN", ""))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    model = DemoModel(args.scenario)
    scenarios = tuple(SCENARIOS)
    start_index = scenarios.index(args.scenario)
    started = time.monotonic()
    print("Demo server: http://%s:%d" %
          (args.host, bridge.server.server_port), flush=True)
    print("Development fixtures only; DeskOne rules are not simulated.", flush=True)
    try:
        while args.duration is None or time.monotonic() - started < args.duration:
            now = time.monotonic()
            if args.cycle:
                scenario = scenarios[(start_index + int((now - started) / args.cycle))
                                     % len(scenarios)]
                if scenario != model.scenario:
                    model.select(scenario)
                    print("Scenario: " + scenario, flush=True)
            for _ in bridge.drain():
                accepted = model.touch(now)
                print("Touch: " + ("accepted" if accepted else "ignored during sleep"),
                      flush=True)
            bridge.publish(*model.snapshot(now))
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        bridge.close()
        print("Demo stopped.", flush=True)

if __name__ == "__main__":
    main()
