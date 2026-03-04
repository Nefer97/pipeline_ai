"""
server.py — Backend FastAPI per Appunti AI
========================================
Avvia con:
    pip install fastapi uvicorn python-multipart aiofiles
    uvicorn server:app --reload --host 0.0.0.0 --port 8000
"""

import os
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Appunti AI API")

# CORS: permetti richieste dal frontend (qualsiasi origine in dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Job store in memoria (per semplicità) ──
# In produzione: usa Redis o un DB
jobs: dict[str, dict] = {}


def _run_pipeline_job(job_id: str, lesson_dir: Path, output_dir: Path,
                       title: str, skip_ai: bool, skip_ocr: bool,
                       whisper_model: str, subject: Optional[str],
                       no_context: bool, start_from: int):
    """Eseguito in background thread da BackgroundTasks."""
    jobs[job_id]["status"] = "running"
    cmd = [
        "python3", "pipeline.py",
        str(lesson_dir),
        "--title", title,
        "--output", str(output_dir),
        "--whisper-model", whisper_model,
        "--start-from", str(start_from),
    ]
    if skip_ai:
        cmd.append("--skip-ai")
    if skip_ocr:
        cmd.append("--skip-ocr")
    if no_context:
        cmd.append("--no-context")
    if subject:
        cmd.extend(["--subject", subject])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # max 1 ora
            cwd=Path(__file__).parent,  # cwd = cartella del progetto
        )
        jobs[job_id]["stdout"]     = result.stdout
        jobs[job_id]["stderr"]     = result.stderr
        jobs[job_id]["returncode"] = result.returncode

        if result.returncode == 0:
            # Crea ZIP dell'output per il download
            zip_path = OUTPUT_DIR / f"{job_id}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in output_dir.rglob("*"):
                    if f.is_file():
                        # Mantieni la cartella output_name come root dello ZIP
                        zf.write(f, Path(output_dir.name) / f.relative_to(output_dir))  
            jobs[job_id]["status"]   = "done"
            jobs[job_id]["zip_path"] = str(zip_path)
        else:
            jobs[job_id]["status"] = "error"

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["stderr"] = "Timeout: pipeline impiegato più di 1 ora"
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["stderr"] = str(e)


@app.post("/run-pipeline")
async def run_pipeline(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    skip_ai: str = Form("false"),
    skip_ocr: str = Form("false"),
    no_context: str = Form("false"),
    whisper_model: str = Form("base"),
    output: str = Form("./output"),
    start_from: str = Form("1"),
    subject: Optional[str] = Form(None),
    files: list[UploadFile] = File(...),
):
    """
    Avvia la pipeline in background e restituisce subito un job_id.
    Il client fa polling su GET /job/{job_id} per sapere lo stato.
    """
    # Normalizza bool (FormData manda stringhe)
    _skip_ai    = skip_ai.lower()    in ("true", "1", "yes")
    _skip_ocr   = skip_ocr.lower()   in ("true", "1", "yes")
    _no_context = no_context.lower()  in ("true", "1", "yes")
    _start_from = max(1, int(start_from)) if start_from.isdigit() else 1
    _subject    = subject.strip() if subject and subject.strip() not in ("", "auto") else None

    # Crea job
    job_id     = str(uuid.uuid4())[:8]
    lesson_dir = UPLOAD_DIR / job_id
    # Usa il nome scelto dall'utente come sottocartella nell'output
    output_name = "".join(c for c in output if c.isalnum() or c in "_-. /").strip().strip("./").replace("/","_") or "output"
    output_dir  = OUTPUT_DIR / job_id / output_name
    lesson_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Salva file caricati
    saved = []
    for f in files:
        dest = lesson_dir / f.filename
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(f.filename)

    # Inizializza job
    jobs[job_id] = {
        "status":        "queued",
        "title":         title,
        "files":         saved,
        "subject":       _subject,
        "whisper_model": whisper_model,
        "no_context":    _no_context,
        "start_from":    _start_from,
        "skip_ai":       _skip_ai,
        "skip_ocr":      _skip_ocr,
        "output_name":   output_name,
        "stdout":        "",
        "stderr":        "",
        "returncode":    None,
        "zip_path":      None,
    }

    # Lancia in background (non blocca la risposta HTTP)
    background_tasks.add_task(
        _run_pipeline_job,
        job_id, lesson_dir, output_dir,
        title, _skip_ai, _skip_ocr, whisper_model,
        _subject, _no_context, _start_from
    )

    return JSONResponse({
        "job_id":  job_id,
        "status":  "queued",
        "files":   saved,
        "message": f"Pipeline avviata. Fai polling su /job/{job_id}"
    })


@app.get("/job/{job_id}")
async def get_job(job_id: str):
    """Restituisce lo stato attuale del job."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job non trovato")

    job = jobs[job_id]
    response = {
        "job_id":        job_id,
        "status":        job["status"],
        "title":         job["title"],
        "files":         job["files"],
        "subject":       job.get("subject"),
        "whisper_model": job.get("whisper_model"),
        "no_context":    job.get("no_context"),
        "start_from":    job.get("start_from"),
        "skip_ai":       job.get("skip_ai"),
        "skip_ocr":      job.get("skip_ocr"),
        "output_name":   job.get("output_name"),
        "stdout":        job["stdout"],
        "stderr":        job["stderr"],
        "returncode":    job["returncode"],
        "download":      f"/download/{job_id}" if job["status"] == "done" else None,
    }
    return JSONResponse(response)


@app.get("/download/{job_id}")
async def download_output(job_id: str):
    """Scarica lo ZIP con main.tex + lezione_NN.tex + images/."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job non trovato")

    job = jobs[job_id]
    if job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job non completato (stato: {job['status']})")

    zip_path = Path(job["zip_path"])
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="ZIP non trovato")

    title_safe = "".join(c for c in job["title"] if c.isalnum() or c in "_- ").strip().replace(" ", "_")
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"appunti_{title_safe}.zip"
    )


@app.get("/jobs")
async def list_jobs():
    """Lista tutti i job (utile per debug)."""
    return JSONResponse({
        jid: {"status": j["status"], "title": j["title"], "files": j["files"]}
        for jid, j in jobs.items()
    })


@app.delete("/job/{job_id}")
async def delete_job(job_id: str):
    """Elimina job e file associati."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job non trovato")
    # Rimuovi file
    for d in [UPLOAD_DIR / job_id, OUTPUT_DIR / job_id]:
        shutil.rmtree(d, ignore_errors=True)
    zip_p = OUTPUT_DIR / f"{job_id}.zip"
    if zip_p.exists():
        zip_p.unlink()
    del jobs[job_id]
    return JSONResponse({"deleted": job_id})