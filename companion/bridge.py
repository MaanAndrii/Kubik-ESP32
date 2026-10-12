"""Optional LAN companion. The pygame thread owns all character mutations."""
import hmac
import json
import os
import queue
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class CompanionBridge:
    def __init__(self, host, port, token):
        if len(token) < 24:
            raise ValueError("KUBIK_BRIDGE_TOKEN must contain at least 24 characters")
        self.token = token
        self.commands = queue.Queue(maxsize=32)
        self.lock = threading.Lock()
        self.seen = deque(maxlen=128)
        self.state = {"version": 1, "ready": False}
        bridge = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def reply(self, status, value):
                payload = json.dumps(value, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def authorized(self):
                return hmac.compare_digest(self.headers.get("Authorization", ""),
                                           "Bearer " + bridge.token)
            def do_GET(self):
                if not self.authorized():
                    return self.reply(401, {"error": "unauthorized"})
                if self.path != "/v1/state":
                    return self.reply(404, {})
                with bridge.lock:
                    state = dict(bridge.state)
                self.reply(200, state)
            def do_POST(self):
                if not self.authorized():
                    return self.reply(401, {})
                if self.path != "/v1/touch":
                    return self.reply(404, {})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 1 <= length <= 256:
                        return self.reply(413, {})
                    value = json.loads(self.rfile.read(length))
                    event_id = value["id"]
                    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 64:
                        raise ValueError()
                    if value.get("action") != "touch":
                        raise ValueError()
                except (ValueError, KeyError, TypeError):
                    return self.reply(400, {})
                with bridge.lock:
                    if event_id in bridge.seen:
                        return self.reply(200, {"accepted": True})
                    try:
                        bridge.commands.put_nowait("touch")
                    except queue.Full:
                        return self.reply(503, {"accepted": False})
                    bridge.seen.append(event_id)
                self.reply(200, {"accepted": True})
            def setup(self):
                super().setup()
                self.connection.settimeout(2)
        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @classmethod
    def from_environment(cls):
        if os.getenv("KUBIK_BRIDGE_ENABLE") != "1":
            return None
        return cls(os.getenv("KUBIK_BRIDGE_HOST", "0.0.0.0"),
                   int(os.getenv("KUBIK_BRIDGE_PORT", "8765")),
                   os.getenv("KUBIK_BRIDGE_TOKEN", ""))

    def drain(self):
        result = []
        for _ in range(32):
            try:
                result.append(self.commands.get_nowait())
            except queue.Empty:
                break
        return result

    def publish(self, frame, mode, sleeping):
        # SceneDirector has already applied location, priority and compatibility.
        value = dict(version=1, ready=True, emotion=frame.emotion, mode=mode,
                     sleeping=bool(sleeping), location=frame.location,
                     held=frame.held_item or "", visibility=frame.held_visibility,
                     wearables=list(frame.wearables), contexts=sorted(frame.contexts),
                     timer_progress=frame.timer_progress)
        with self.lock:
            self.state = value

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
