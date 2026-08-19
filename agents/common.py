"""Shared client + test-logging helpers for the ResQueue test agents."""
import json
import os
import re
from datetime import datetime, timezone

import requests

BASE = os.environ.get("RESQUEUE_URL", "http://127.0.0.1:8765")
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "results")


class Client:
    """Thin wrapper over the ResQueue HTTP API for one actor (user or admin)."""

    def __init__(self):
        self.token = None

    def call(self, method, path, body=None):
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        r = requests.request(method, BASE + path, json=body, headers=headers, timeout=10)
        try:
            data = r.json()
        except ValueError:
            data = {"raw": r.text}
        return r.status_code, data

    # -- convenience flows -------------------------------------------------
    def register(self, email, password):
        return self.call("POST", "/api/register", {"email": email, "password": password})

    def fetch_code(self, email):
        """Read the verification code from the simulated email outbox."""
        status, mails = self.call("GET", f"/api/dev/outbox?email={email}")
        for mail in reversed(mails if isinstance(mails, list) else []):
            m = re.search(r"verification code is: (\d{6})", mail.get("body", ""))
            if m:
                return m.group(1)
        return None

    def verify(self, email, code):
        return self.call("POST", "/api/verify", {"email": email, "code": code})

    def login(self, email, password):
        status, data = self.call("POST", "/api/login", {"email": email, "password": password})
        if status == 200:
            self.token = data["token"]
        return status, data

    def signup_and_login(self, email, password):
        """Full happy-path onboarding used to provision actor accounts."""
        self.register(email, password)
        self.verify(email, self.fetch_code(email))
        return self.login(email, password)


class TestLog:
    def __init__(self, agent_name):
        self.agent = agent_name
        self.entries = []

    def record(self, feature, action, expected, observed, passed, notes=""):
        self.entries.append({
            "agent": self.agent,
            "feature": feature,
            "action": action,
            "expected": expected,
            "observed": observed,
            "pass": bool(passed),
            "notes": notes,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"[{self.agent}] {'PASS' if passed else 'FAIL'} - {action}")

    def check(self, feature, action, expected_desc, status, data, want_status,
              predicate=None, notes=""):
        """Assert on HTTP status (and optional response predicate), then log."""
        ok = status == want_status and (predicate is None or predicate(data))
        observed = f"HTTP {status}: {json.dumps(data, default=str)[:300]}"
        self.record(feature, action, expected_desc, observed, ok, notes)
        return ok

    def save(self, filename):
        os.makedirs(RESULTS_DIR, exist_ok=True)
        path = os.path.join(RESULTS_DIR, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=2)
        passed = sum(1 for e in self.entries if e["pass"])
        print(f"[{self.agent}] done: {passed}/{len(self.entries)} passed -> {path}")
