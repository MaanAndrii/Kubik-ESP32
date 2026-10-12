import json
import urllib.request
import urllib.error
import unittest
from types import SimpleNamespace
from companion.bridge import CompanionBridge

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.token = "test-token-with-more-than-24-characters"
        self.bridge = CompanionBridge("127.0.0.1", 0, self.token)
        self.url = "http://127.0.0.1:%d" % self.bridge.server.server_port

    def tearDown(self):
        self.bridge.close()

    def request(self, path, data=None, token=None):
        req = urllib.request.Request(self.url + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": "Bearer " + (token or self.token)})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.load(response)

    def test_authorization_and_initial_state(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/state", token="wrong")
        self.assertEqual(caught.exception.code, 401)
        self.assertFalse(self.request("/v1/state")["ready"])

    def test_resolved_frame_is_preserved(self):
        frame = SimpleNamespace(emotion="SLEEPING", location="HOME",
            held_item=None, held_visibility=0, wearables=("nightcap", "sleep_mask"),
            contexts=frozenset({"SLEEP_WEAR"}), timer_progress=0)
        self.bridge.publish(frame, "SLEEP", True)
        state = self.request("/v1/state")
        self.assertTrue(state["sleeping"])
        self.assertEqual(state["wearables"], ["nightcap", "sleep_mask"])
        self.assertEqual(state["held"], "")

    def test_touch_is_queued_once_and_requires_valid_action(self):
        event = {"id": "boot-1", "action": "touch"}
        self.request("/v1/touch", event)
        self.request("/v1/touch", event)
        self.assertEqual(self.bridge.drain(), ["touch"])
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/touch", {"id": "boot-2", "action": "wake"})
        self.assertEqual(caught.exception.code, 400)

    def test_backpressure_and_retry(self):
        for i in range(32):
            self.request("/v1/touch", {"id": str(i), "action": "touch"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.request("/v1/touch", {"id": "overflow", "action": "touch"})
        self.assertEqual(caught.exception.code, 503)
        self.bridge.drain()
        self.request("/v1/touch", {"id": "overflow", "action": "touch"})
        self.assertEqual(self.bridge.drain(), ["touch"])

if __name__ == "__main__":
    unittest.main()
