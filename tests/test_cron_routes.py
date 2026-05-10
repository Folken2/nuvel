"""HTTP API CRUD round-trip via FastAPI TestClient."""

from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._cron_helpers import CronAgent


class TestCronRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = CronAgent("cron-http")
        app = FastAPI()
        app.include_router(cls.agent.routes.router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.agent.cleanup()

    def test_full_crud_roundtrip(self):
        r = self.client.post("/cron/jobs", json={
            "name": "morning", "prompt": "summary", "schedule": "0 9 * * *",
            "delivery": "local",
        })
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()
        jid = job["id"]

        # list
        r = self.client.get("/cron/jobs")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(any(j["id"] == jid for j in r.json()["jobs"]))

        # get
        r = self.client.get(f"/cron/jobs/{jid}")
        self.assertEqual(r.status_code, 200)

        # patch
        r = self.client.patch(f"/cron/jobs/{jid}", json={"name": "renamed"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "renamed")

        # pause/resume
        self.assertEqual(self.client.post(f"/cron/jobs/{jid}/pause").json()["status"], "paused")
        self.assertEqual(self.client.post(f"/cron/jobs/{jid}/resume").json()["status"], "active")

        # run
        self.assertEqual(self.client.post(f"/cron/jobs/{jid}/run").status_code, 200)

        # delete
        r = self.client.delete(f"/cron/jobs/{jid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(f"/cron/jobs/{jid}").status_code, 404)

    def test_create_rejects_bad_schedule(self):
        r = self.client.post("/cron/jobs", json={
            "name": "bad", "prompt": "p", "schedule": "not a schedule",
        })
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
