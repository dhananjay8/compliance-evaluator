"""Full API e2e — requires the running stack (app + ES + Ollama).

Run with: COMPLIANCE_IT=1 BASE_URL=http://localhost:8090 pytest tests/e2e -q
Prereqs: rules seeded, fixture PDFs generated (make fixtures).
"""

import os
import time
from pathlib import Path

import httpx
import pytest

from tests.conftest import requires_services

pytestmark = [requires_services, pytest.mark.e2e]

BASE = os.environ.get("BASE_URL", "http://localhost:8090")
FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "policies"


@pytest.mark.asyncio
async def test_full_assessment_flow():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        health = (await client.get("/health")).json()
        assert health["status"] == "ok"

        # create
        resp = await client.post("/api/v1/assessments",
                                 json={"name": "e2e", "framework": "SOC2"})
        assert resp.status_code == 201
        aid = resp.json()["assessment_id"]

        # upload
        files = [
            ("files", (p.name, p.read_bytes(), "application/pdf"))
            for p in sorted(FIXTURES.glob("*.pdf"))
        ]
        assert files, "run `make fixtures` first"
        resp = await client.post(f"/api/v1/assessments/{aid}/documents", files=files)
        assert resp.status_code == 200
        assert len(resp.json()["uploaded"]) == len(files)

        # evaluate a small rule subset for speed
        resp = await client.post(
            f"/api/v1/assessments/{aid}/evaluate",
            json={"rule_ids": ["SOC2-CC6.1-004", "SOC2-CC9.1-001", "SOC2-CC7.3-002"]},
        )
        assert resp.status_code == 202

        # poll
        for _ in range(120):
            status = (await client.get(f"/api/v1/assessments/{aid}")).json()["status"]
            if status in ("COMPLETED", "PARTIAL", "FAILED"):
                break
            time.sleep(10)
        assert status in ("COMPLETED", "PARTIAL"), f"status={status}"

        results = (await client.get(f"/api/v1/assessments/{aid}/results")).json()
        by_rule = {r["rule_id"]: r for r in results["results"]}
        assert by_rule["SOC2-CC6.1-004"]["verdict"] == "COMPLIANT"
        assert by_rule["SOC2-CC6.1-004"]["citations"], "COMPLIANT requires citations"
        assert by_rule["SOC2-CC9.1-001"]["verdict"] == "NOT_COMPLIANT"
        assert by_rule["SOC2-CC9.1-001"]["gap_analysis"] is not None
