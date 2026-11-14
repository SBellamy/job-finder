from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from job_finder.lambda_handlers import api_handler


class CorsReleaseTests(unittest.TestCase):
    def test_preflight_allows_follow_board_without_initializing_storage(self):
        event = {"requestContext": {"http": {"method": "OPTIONS"}}, "rawPath": "/sources"}
        with patch("job_finder.lambda_handlers._repository", side_effect=AssertionError("storage should not initialize")):
            response = api_handler(event, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("POST", response["headers"]["Access-Control-Allow-Methods"].split(","))
        self.assertTrue(json.loads(response["body"])["ok"])

    def test_health_uses_deployed_origin(self):
        with patch("job_finder.lambda_handlers.load_allowed_origin", return_value="https://dashboard.example.com"):
            response = api_handler({"requestContext": {"http": {"method": "GET"}}, "rawPath": "/health"}, None)
        self.assertEqual(response["headers"]["Access-Control-Allow-Origin"], "https://dashboard.example.com")
