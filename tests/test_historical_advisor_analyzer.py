from __future__ import annotations

import json

from historical_ingestion.advisor_analyzer import HistoricalAdvisorAnalyzer


class FakeResponse:
    def __init__(self, payload: dict, *, request_id: str):
        self.output_text = json.dumps(payload)
        self.model = "gpt-5.6-sol"
        self._request_id = request_id


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return FakeResponse(
                {
                    "document": {
                        "document_type": "job_record",
                        "title": "Ajebo Fix Job",
                        "reference": "JOB-2026-002",
                        "job_reference": "JOB-2026-002",
                        "sow_reference": "SOW-2026-002",
                        "document_date": "2026-08-18",
                        "client_name": "Client",
                        "vehicle_description": "2014 Mercedes-Benz GL 450",
                        "vin": "4JG166TEST000001",
                        "plate_number": "JJJ926HX",
                    },
                    "advisor_narrative": (
                        "The document records a reported electrical symptom, "
                        "AIRMATIC findings, recommended work and commercial lines."
                    ),
                    "chronology": [],
                    "facts": [],
                    "ambiguities": [
                        "Completion of authorised work is not established by this source."
                    ],
                    "advisor_suggestions": [
                        "Confirm actual completed work from stronger completion evidence."
                    ],
                },
                request_id="req-understanding",
            )

        return FakeResponse(
            {
                "document": {
                    "document_type": "job_record",
                    "title": "Ajebo Fix Job",
                    "reference": "JOB-2026-002",
                    "job_reference": "JOB-2026-002",
                    "sow_reference": "SOW-2026-002",
                    "document_date": "2026-08-18",
                    "client_name": "Client",
                    "vehicle_description": "2014 Mercedes-Benz GL 450",
                    "vin": "4JG166TEST000001",
                    "plate_number": "JJJ926HX",
                },
                "rina_summary": "Advisor-grade summary.",
                "advisor_suggestions": ["Verify completion separately."],
                "candidates": [],
            },
            request_id="req-structured",
        )


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_advisor_analyzer_reads_pdf_before_structuring_candidates():
    client = FakeClient()
    analyzer = HistoricalAdvisorAnalyzer(
        client=client,
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )

    result = analyzer.analyze_pdf(
        pdf_payload=b"%PDF-1.4 fake test bytes",
        extracted_text="--- PAGE 1 ---\nJOB-2026-002\nElectrical concern",
        trusted_vehicle_context={
            "car_id": 1,
            "display_name": "Mercedes-Benz GL 450 2014",
            "vin": "4JG166TEST000001",
            "audience": "Ajebo Fix professional advisor",
        },
    )

    assert len(client.responses.calls) == 2

    first = client.responses.calls[0]
    assert first["model"] == "gpt-5.6-sol"
    assert first["reasoning"] == {"effort": "high"}
    assert first["store"] is False
    assert first["text"]["format"]["type"] == "json_schema"
    assert first["text"]["format"]["strict"] is True

    first_content = first["input"][0]["content"]
    assert any(item["type"] == "input_file" for item in first_content)
    assert any(
        item["type"] == "input_text"
        and "Ajebo Fix professional advisor" in item["text"]
        for item in first_content
    )

    second = client.responses.calls[1]
    assert second["text"]["format"]["name"] == "aura_historical_review_candidates"
    second_text = second["input"][0]["content"][0]["text"]
    assert "Document understanding from pass 1" in second_text
    assert "JOB-2026-002" in second_text

    assert result.understanding_request_id == "req-understanding"
    assert result.structured_request_id == "req-structured"
    assert result.model == "gpt-5.6-sol"
