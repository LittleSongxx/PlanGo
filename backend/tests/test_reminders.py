"""One offline API/database regression for real, explicit reminders."""

import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
from planora.persistence.database import Database
from yoyu.reminders import install_reminder_routes, now_ms, setup_reminders


def reminder_app(directory):
    runtime = SimpleNamespace(
        database=Database(f"sqlite+aiosqlite:///{directory}/reminders.sqlite", allow_fallback=False)
    )

    @asynccontextmanager
    async def lifespan(app):
        await setup_reminders(runtime)
        try:
            yield
        finally:
            await runtime.database.close()

    app = FastAPI(lifespan=lifespan)

    async def auth(authorization: str | None = Header(default=None)):
        if authorization != "Bearer reminders-test":
            raise HTTPException(401)

    install_reminder_routes(app, runtime, auth)
    return app


class ReminderCheck(unittest.TestCase):
    def test_validation_due_ack_and_restart(self):
        headers = {"Authorization": "Bearer reminders-test"}
        start = now_ms()
        future = datetime.fromtimestamp((start + 60000) / 1000, timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            with TestClient(reminder_app(directory), headers=headers) as client:
                self.assertEqual(
                    client.get("/api/v1/reminders", headers={"Authorization": "wrong"}).status_code,
                    401,
                )
                for body in [
                    {"text": " ", "at": future},
                    {"text": "提醒", "at": "2020-01-01T00:00:00Z"},
                    {"text": "提醒", "at": "2030-01-01T00:00:00"},
                    {"text": "提醒", "at": start + 60000},
                ]:
                    self.assertEqual(client.post("/api/v1/reminders", json=body).status_code, 422)
                created = client.post(
                    "/api/v1/reminders", json={"text": "  出门前确认预约  ", "at": future}
                )
                self.assertEqual(created.status_code, 201)
                reminder = created.json()["reminders"][0]
                self.assertEqual(reminder["text"], "出门前确认预约")
                self.assertEqual(reminder["at"], start + 60000)
                self.assertFalse(reminder["fired"])
                self.assertEqual(client.get("/api/v1/reminders/due").json()["reminders"], [])
                self.assertEqual(
                    client.post(f"/api/v1/reminders/{reminder['id']}/ack").status_code, 409
                )
                with patch("yoyu.reminders.now_ms", return_value=start + 120000):
                    due = client.get("/api/v1/reminders/due").json()["reminders"]
                    self.assertEqual([r["id"] for r in due], [reminder["id"]])
                    fired = client.post(f"/api/v1/reminders/{reminder['id']}/ack").json()
                    self.assertTrue(fired["reminders"][0]["fired"])
                    self.assertEqual(client.get("/api/v1/reminders/due").json()["reminders"], [])
                with patch("yoyu.reminders.now_ms", return_value=start + 180000):
                    repeated = client.post(f"/api/v1/reminders/{reminder['id']}/ack").json()
                    self.assertEqual(repeated["history"], fired["history"])
            with TestClient(reminder_app(directory), headers=headers) as restarted:
                self.assertEqual(
                    restarted.get("/api/v1/reminders").json()["history"], fired["history"]
                )
                self.assertEqual(
                    restarted.delete(f"/api/v1/reminders/{reminder['id']}").json()["reminders"], []
                )
                self.assertEqual(
                    restarted.delete(f"/api/v1/reminders/{reminder['id']}").status_code, 404
                )
