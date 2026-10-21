import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from server import StormShiftHandler


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StormShiftHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_health_and_frontend(self):
        health = json.load(urllib.request.urlopen(self.base + "/api/health"))
        self.assertEqual(health["status"], "ok")
        html = urllib.request.urlopen(self.base + "/").read().decode("utf-8")
        self.assertIn("StormShift", html)
        self.assertIn("/vendor/phaser.min.js", html)

    def test_negotiation_endpoint(self):
        request = urllib.request.Request(
            self.base + "/api/negotiate",
            data=json.dumps({"preference": "capacity"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        result = json.load(urllib.request.urlopen(request))
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(result["plan"]["gate_assignments"]["LV412"], "R1")


if __name__ == "__main__":
    unittest.main()
