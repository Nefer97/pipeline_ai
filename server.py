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
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Appunti AI API")

# CORS: permetti richieste dal frontend (qualsiasi origine in dev)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve assets statici (audio, css, ecc.)
_ASSETS_DIR = Path(__file__).parent / "assets"
if _ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("outputs")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Job store in memoria (per semplicità) ──
# In produzione: usa Redis o un DB
jobs: dict[str, dict] = {}


def _clean_teams_url(url: str) -> str:
    """Rimuove &altTranscode=1 e parametri successivi (come TeamsHack.py)."""
    import re
    m = re.search(r'&altTranscode=1', url)
    return url[:m.start()] if m else url


def _run_pipeline_job(job_id: str, lesson_dir: Path, output_dir: Path,
                       title: str, skip_ai: bool, skip_ocr: bool,
                       whisper_model: str, subject: Optional[str],
                       no_context: bool, start_from: Optional[int],
                       teams_urls: list[str] | None = None,
                       continue_from: str = ""):
    """Eseguito in background thread da BackgroundTasks."""
    if job_id not in jobs:
        return  # job eliminato prima che il thread partisse
    jobs[job_id]["status"] = "running"

    # ── Continua un corso precedente: copia state.json e corso_context.json ──
    if continue_from:
        # Prima cerca in memoria (job ancora presente), poi su disco (server riavviato)
        if continue_from in jobs:
            prev_out = Path(jobs[continue_from]["output_dir"])
        else:
            # Fallback su disco: cerca ricorsivamente in outputs/{prev_job_id}/
            candidates = list((OUTPUT_DIR / continue_from).rglob("state.json"))
            prev_out = candidates[0].parent if candidates else None

        if prev_out:
            for fname in ("state.json", "corso_context.json"):
                src = prev_out / fname
                if src.exists():
                    shutil.copy2(src, output_dir / fname)
                    print(f"[continue] copiato {fname} da {prev_out}")
        else:
            print(f"[continue] job {continue_from} non trovato in memoria né su disco — ignoro")

    # ── Download audio da URL Teams (videomanifest) ──────────────────
    if teams_urls:
        for i, raw_url in enumerate(teams_urls):
            url = _clean_teams_url(raw_url.strip())
            if not url:
                continue
            mp3_out = lesson_dir / f"teams_{i+1:02d}.mp3"
            print(f"[TeamsHack] Download audio {i+1}/{len(teams_urls)}: {url[:80]}…")
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", url,
                     "-vn", "-ac", "1", "-codec:a", "libmp3lame", "-qscale:a", "4",
                     str(mp3_out)],
                    timeout=7200,
                    check=False,
                )
            except Exception as e:
                print(f"[TeamsHack] Errore download: {e}")

    cmd = [
        "python3", "pipeline.py",
        str(lesson_dir),
        "--title", title,
        "--output", str(output_dir),
        "--whisper-model", whisper_model,
    ]
    # --start-from solo se l'utente lo ha specificato esplicitamente (> 0)
    # altrimenti la pipeline usa state.json per la numerazione automatica
    if start_from is not None and start_from > 0:
        cmd.extend(["--start-from", str(start_from)])
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
                        try:
                            # Mantieni la cartella output_name come root dello ZIP
                            zf.write(f, Path(output_dir.name) / f.relative_to(output_dir))
                        except (FileNotFoundError, OSError):
                            pass  # file rimosso tra rglob e write
            jobs[job_id]["status"]   = "done"
            jobs[job_id]["zip_path"] = str(zip_path)
        else:
            jobs[job_id]["status"] = "error"

    except subprocess.TimeoutExpired:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["stderr"] = "Timeout: pipeline impiegato più di 1 ora"
        shutil.rmtree(UPLOAD_DIR / job_id, ignore_errors=True)
    except Exception as e:
        jobs[job_id]["status"] = "error"
        jobs[job_id]["stderr"] = str(e)
        shutil.rmtree(UPLOAD_DIR / job_id, ignore_errors=True)


@app.post("/run-pipeline")
async def run_pipeline(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    skip_ai: str = Form("false"),
    skip_ocr: str = Form("false"),
    no_context: str = Form("false"),
    whisper_model: str = Form("base"),
    output: str = Form("./output"),
    start_from: str = Form("0"),
    subject: Optional[str] = Form(None),
    continue_from: str = Form(""),
    teams_url: list[str] = Form([]),
    files: list[UploadFile] = File([]),
):
    """
    Avvia la pipeline in background e restituisce subito un job_id.
    Il client fa polling su GET /job/{job_id} per sapere lo stato.
    """
    # Normalizza bool (FormData manda stringhe)
    _skip_ai    = skip_ai.lower()    in ("true", "1", "yes")
    _skip_ocr   = skip_ocr.lower()   in ("true", "1", "yes")
    _no_context = no_context.lower()  in ("true", "1", "yes")
    # 0 o stringa non numerica → auto (usa state.json nella cartella output)
    _start_from = int(start_from) if start_from.isdigit() and int(start_from) > 0 else None
    _VALID_SUBJECTS = {"ingegneria","matematica","fisica","medicina","economia","giurisprudenza","generico"}
    _subject    = subject.strip().lower() if subject and subject.strip() not in ("", "auto") else None
    if _subject and _subject not in _VALID_SUBJECTS:
        _subject = None

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

    _continue_from = continue_from.strip()

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
        "output_dir":    str(output_dir),   # per leggere progress.json
        "teams_urls":    teams_url,
        "continue_from": _continue_from,
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
        _subject, _no_context, _start_from, teams_url or [],
        _continue_from
    )

    return JSONResponse({
        "job_id":  job_id,
        "status":  "queued",
        "files":   saved,
        "message": f"Pipeline avviata. Fai polling su /job/{job_id}"
    })


@app.get("/job/{job_id}")
async def get_job(job_id: str):
    """Restituisce lo stato attuale del job, inclusi progress/current_step/detail."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job non trovato")

    job = jobs[job_id]

    # Leggi progress.json scritto dalla pipeline (aggiornato in real-time)
    progress     = 100 if job["status"] == "done" else 0
    current_step = "Completato" if job["status"] == "done" else \
                   "In coda"    if job["status"] == "queued" else \
                   "Errore"     if job["status"] == "error"  else ""
    detail       = ""

    if job["status"] in ("running", "done"):
        try:
            import json as _json
            prog_path = Path(job["output_dir"]) / "progress.json"
            if prog_path.exists():
                p = _json.loads(prog_path.read_text(encoding="utf-8"))
                progress     = p.get("progress",     progress)
                current_step = p.get("current_step", current_step)
                detail       = p.get("detail",       "")
        except Exception:
            pass

    response = {
        "job_id":        job_id,
        "status":        job["status"],
        "progress":      progress,
        "current_step":  current_step,
        "detail":        detail,
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


@app.get("/")
async def serve_index():
    """Serve il frontend (index.htm) — accessibile da qualsiasi browser in rete."""
    index_path = Path(__file__).parent / "index.htm"
    return FileResponse(str(index_path), media_type="text/html")

@app.get("/schema.htm")
async def serve_schema():
    """Serve il frontend (schema.htm) — accessibile da qualsiasi browser in rete."""
    index_path = Path(__file__).parent / "schema.htm"
    return FileResponse(str(index_path), media_type="text/html")


@app.get("/jobs")
async def list_jobs():
    """Lista tutti i job (utile per debug)."""
    return JSONResponse({
        jid: {"status": j["status"], "title": j["title"], "files": j["files"]}
        for jid, j in list(jobs.items())  # snapshot per evitare RuntimeError su dict cambiato
    })


@app.delete("/job/{job_id}")
async def delete_job(job_id: str, full: bool = False):
    """
    Elimina job e file associati.
    - Default (full=false): rimuove solo uploads/ (file originali), mantiene output/ e zip.
    - full=true: rimuove tutto inclusa la cartella output con tex e immagini.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job non trovato")
    # Rimuovi sempre: file caricati dall'utente (non servono più dopo la pipeline)
    shutil.rmtree(UPLOAD_DIR / job_id, ignore_errors=True)
    # Rimuovi zip e output solo se richiesto esplicitamente
    if full:
        zip_p = OUTPUT_DIR / f"{job_id}.zip"
        if zip_p.exists():
            zip_p.unlink()
        shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
    del jobs[job_id]
    return JSONResponse({"deleted": job_id, "output_kept": not full})