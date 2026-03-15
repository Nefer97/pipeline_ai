"""
tests/test_api.py — Test di integrazione per i endpoint FastAPI di Appunti AI.

Copertura:
  - Endpoint statici (/, /schema.htm, /docs.htm)
  - /health, /jobs, /settings GET/POST
  - /job/{id} 404 su job inesistente
  - /run-pipeline con WAV minimale (genera job_id, verifica stato)

Esegui con:
    cd ~/appunti_ai
    python -m pytest tests/test_api.py -v

    # Solo test veloci (senza avvio pipeline reale):
    python -m pytest tests/test_api.py -v -m "not integration"
"""

import struct
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient
from server import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def minimal_wav(tmp_path_factory):
    """WAV valido da 100ms (44100Hz, mono, 16bit) — stdlib pura, no ffmpeg."""
    path = tmp_path_factory.mktemp("audio") / "silence.wav"
    n_frames = int(44100 * 0.1)
    with wave.open(str(path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(44100)
        wf.writeframes(struct.pack(f"<{n_frames}h", *([0] * n_frames)))
    return path


# ─────────────────────────────────────────────
# Endpoint statici
# ─────────────────────────────────────────────

class TestStaticPages:
    def test_root_serves_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Appunti AI" in r.text

    def test_schema_htm(self, client):
        r = client.get("/schema.htm")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_docs_htm(self, client):
        r = client.get("/docs.htm")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_unknown_route_404(self, client):
        r = client.get("/questa-pagina-non-esiste")
        assert r.status_code == 404


# ─────────────────────────────────────────────
# /health
# ─────────────────────────────────────────────

class TestHealth:
    def test_health_returns_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_health_has_required_keys(self, client):
        data = client.get("/health").json()
        for key in ("ffmpeg", "pdflatex", "whisper"):
            assert key in data, f"Campo mancante in /health: {key}"

    def test_health_values_are_bool(self, client):
        data = client.get("/health").json()
        for key in ("ffmpeg", "pdflatex", "whisper"):
            assert isinstance(data[key], bool), f"/health.{key} deve essere bool"


# ─────────────────────────────────────────────
# /jobs
# ─────────────────────────────────────────────

class TestJobs:
    def test_jobs_returns_200(self, client):
        r = client.get("/jobs")
        assert r.status_code == 200
        # /jobs torna un dict {job_id: job_data}
        assert isinstance(r.json(), dict)

    def test_nonexistent_job_404(self, client):
        r = client.get("/job/job-inesistente-xyz")
        assert r.status_code == 404

    def test_delete_nonexistent_job_404(self, client):
        r = client.delete("/job/job-inesistente-xyz")
        assert r.status_code == 404


# ─────────────────────────────────────────────
# /settings
# ─────────────────────────────────────────────

class TestSettings:
    def test_settings_get(self, client):
        r = client.get("/settings")
        assert r.status_code == 200
        data = r.json()
        # Almeno uno dei campi attesi deve essere presente
        assert any(k in data for k in ("ffmpeg_timeout", "pipeline_timeout", "api_key_set"))

    def test_settings_post_timeout(self, client):
        r = client.post("/settings", json={"ffmpeg_timeout": 3601})
        assert r.status_code == 200
        # Verifica che il valore sia stato salvato
        data = client.get("/settings").json()
        assert data.get("ffmpeg_timeout") == 3601

    def test_settings_post_invalid_concurrency(self, client):
        """max_concurrent_jobs fuori range (1–10) deve essere rifiutato o ignorato."""
        r = client.post("/settings", json={"max_concurrent_jobs": 999})
        # Deve rispondere senza crash (200 con clamp o 422 validazione)
        assert r.status_code in (200, 422)


# ─────────────────────────────────────────────
# /run-pipeline — integrazione leggera
# ─────────────────────────────────────────────

class TestRunPipeline:
    def test_run_pipeline_no_title_rejected(self, client):
        """Titolo vuoto deve essere rifiutato (400 o 422)."""
        r = client.post("/run-pipeline", data={"title": ""})
        assert r.status_code in (400, 422)

    @pytest.mark.integration
    def test_run_pipeline_minimal_wav(self, client, minimal_wav):
        """WAV minimale con --skip-ai --skip-ocr: verifica che il job parta."""
        with open(minimal_wav, "rb") as f:
            r = client.post(
                "/run-pipeline",
                data={
                    "title": "Test Integrazione API",
                    "skip_ai":   "true",
                    "skip_ocr":  "true",
                    "whisper_model": "tiny",
                },
                files={"files": ("silence.wav", f, "audio/wav")},
            )

        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        job_id = data["job_id"]

        # Il job deve essere registrato e avere uno stato valido
        r2 = client.get(f"/job/{job_id}")
        assert r2.status_code == 200
        status = r2.json().get("status")
        assert status in ("queued", "running", "done", "error")

    @pytest.mark.integration
    def test_title_with_latex_special_chars(self, client, minimal_wav):
        """Titolo con caratteri speciali LaTeX non deve rompere la pipeline."""
        with open(minimal_wav, "rb") as f:
            r = client.post(
                "/run-pipeline",
                data={
                    "title": "Analisi & Sintesi — 50% efficienza #1",
                    "skip_ai":  "true",
                    "skip_ocr": "true",
                    "whisper_model": "tiny",
                },
                files={"files": ("silence.wav", f, "audio/wav")},
            )
        assert r.status_code == 200
        assert "job_id" in r.json()
