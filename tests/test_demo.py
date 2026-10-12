import json
import os
import subprocess
import sys
import unittest
import urllib.request
from companion.bridge import CompanionBridge
from companion.demo import DemoModel, SCENARIOS

class DemoTests(unittest.TestCase):
    def test_sleep_never_wakes_on_touch(self):
        model = DemoModel("sleep")
        self.assertFalse(model.touch(10))
        scene, mode, sleeping = model.snapshot(10)
        self.assertTrue(sleeping)
        self.assertEqual(mode, "SLEEP")
        self.assertEqual(scene.emotion, "SLEEPING")
        self.assertIsNone(scene.held_item)
        self.assertEqual(model.accepted_touches, 0)

    def test_touch_reaction_expires_and_scenario_change_clears_it(self):
        model = DemoModel()
        self.assertTrue(model.touch(10))
        self.assertEqual(model.snapshot(11)[0].emotion, "HAPPY")
        self.assertEqual(model.snapshot(12)[0].emotion, "CALM")
        model.touch(20)
        model.select("work")
        self.assertEqual(model.snapshot(20)[0].emotion, "FOCUSED")
        self.assertEqual(model.snapshot(20)[0].held_item, "laptop")
        self.assertEqual(model.accepted_touches, 2)

    def test_every_fixture_roundtrips_over_actual_http(self):
        token = "demo-test-token-with-at-least-24-characters"
        bridge = CompanionBridge("127.0.0.1", 0, token)
        try:
            base = "http://127.0.0.1:%d" % bridge.server.server_port
            for name in SCENARIOS:
                model = DemoModel(name)
                bridge.publish(*model.snapshot(10))
                request = urllib.request.Request(base + "/v1/state",
                    headers={"Authorization": "Bearer " + token})
                with urllib.request.urlopen(request, timeout=3) as response:
                    state = json.load(response)
                self.assertTrue(state["ready"])
                self.assertEqual(state["version"], 1)
                self.assertEqual(state["location"], model.snapshot(10)[0].location)
                self.assertEqual(state["sleeping"], name == "sleep")
                touch = urllib.request.Request(base + "/v1/touch",
                    data=json.dumps({"id": name, "action": "touch"}).encode(),
                    headers={"Authorization": "Bearer " + token})
                with urllib.request.urlopen(touch, timeout=3) as response:
                    self.assertTrue(json.load(response)["accepted"])
                for _ in bridge.drain():
                    model.touch(10)
                bridge.publish(*model.snapshot(11))
                with urllib.request.urlopen(request, timeout=3) as response:
                    state = json.load(response)
                self.assertEqual(state["emotion"],
                                 "SLEEPING" if name == "sleep" else "HAPPY")
        finally:
            bridge.close()

    def test_cli_starts_and_stops_cleanly(self):
        env = dict(os.environ, KUBIK_BRIDGE_TOKEN="demo-test-token-at-least-24-characters")
        result = subprocess.run(
            [sys.executable, "-m", "companion.demo", "--port", "0",
             "--duration", "0.2", "--cycle", "0.05"],
            capture_output=True, text=True, timeout=10, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Demo server:", result.stdout)
        self.assertIn("Demo stopped.", result.stdout)

    def test_cli_rejects_invalid_duration(self):
        result = subprocess.run(
            [sys.executable, "-m", "companion.demo", "--duration", "nan"],
            capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("finite and positive", result.stderr)
