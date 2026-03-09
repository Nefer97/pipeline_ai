"""
server.py — Backend FastAPI per Appunti AI
========================================
Avvia con:
    pip install fastapi uvicorn python-multipart aiofiles
    uvicorn server:app --reload --host 0.0.0.0 --port 8000
"""

import asyncio
import datetime
import os
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
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

# ── Job store — persiste su disco tra riavvii ──
jobs: dict[str, dict] = {}
JOBS_FILE = OUTPUT_DIR / "jobs.json"
_jobs_lock = threading.Lock()   # protezione accessi concorrenti da BackgroundTasks


def _save_jobs() -> None:
    """Serializza jobs su disco (senza stdout/stderr per tenere il file piccolo).
    Deve essere chiamata DENTRO un blocco `with _jobs_lock`."""
    import json
    try:
        slim = {jid: {k: v for k, v in j.items() if k not in ("stdout", "stderr")}
                for jid, j in jobs.items()}
        JOBS_FILE.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def _load_jobs() -> None:
    """Carica jobs da disco all'avvio. I job 'running' diventano 'error' (pipeline killed)."""
    import json
    if not JOBS_FILE.exists():
        return
    try:
        data = json.loads(JOBS_FILE.read_text(encoding="utf-8"))
        for jid, j in data.items():
            if j.get("status") == "running":
                j["status"] = "error"
                j["stderr"] = "Server riavviato durante l'esecuzione"
            j.setdefault("stdout", "")
            j.setdefault("stderr", "")
            jobs[jid] = j
    except Exception:
        pass


_load_jobs()

# ── Settings persistenti (API key, ecc.) ──────────────────────────────────────
import json as _json_mod

SETTINGS_FILE = Path(__file__).parent / "settings.json"


def _load_settings() -> None:
    """Carica settings.json all'avvio — imposta ANTHROPIC_API_KEY se salvata."""
    if not SETTINGS_FILE.exists():
        return
    try:
        s = _json_mod.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if key := s.get("api_key", "").strip():
            os.environ.setdefault("ANTHROPIC_API_KEY", key)
    except Exception:
        pass


def _save_settings(api_key: str) -> None:
    try:
        SETTINGS_FILE.write_text(
            _json_mod.dumps({"api_key": api_key}, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


_load_settings()


@app.post("/settings")
async def save_settings_endpoint(request: Request):
    """Salva/rimuove la API key di Claude. Persiste su settings.json."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body JSON non valido")
    key = body.get("api_key", "").strip()
    if key:
        os.environ["ANTHROPIC_API_KEY"] = key
    else:
        os.environ.pop("ANTHROPIC_API_KEY", None)
    _save_settings(key)
    return JSONResponse({"ok": True, "api_key": bool(key)})


# ── Cleanup automatico output vecchi ──────────────────────────────────────────
OUTPUT_TTL_DAYS = 7  # cancella output + ZIP più vecchi di N giorni


def _cleanup_old_outputs(max_age_days: int = OUTPUT_TTL_DAYS) -> int:
    """Cancella cartelle output e ZIP più vecchi di max_age_days.
    Ritorna il numero di job rimossi."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    with _jobs_lock:
        expired = [jid for jid, j in list(jobs.items())
                   if j.get("status") in ("done", "error")]
    for jid in expired:
        out_dir = OUTPUT_DIR / jid
        try:
            if out_dir.exists() and out_dir.stat().st_mtime < cutoff:
                shutil.rmtree(out_dir, ignore_errors=True)
                zip_p = OUTPUT_DIR / f"{jid}.zip"
                if zip_p.exists():
                    zip_p.unlink(missing_ok=True)
                shutil.rmtree(UPLOAD_DIR / jid, ignore_errors=True)
                with _jobs_lock:
                    jobs.pop(jid, None)
                    _save_jobs()
                removed += 1
        except Exception:
            pass
    return removed


@app.on_event("startup")
async def _on_startup():
    n = await asyncio.to_thread(_cleanup_old_outputs)
    if n:
        print(f"[cleanup] {n} output vecchi rimossi all'avvio (TTL={OUTPUT_TTL_DAYS}d)")

    async def _periodic_cleanup():
        while True:
            await asyncio.sleep(86400)  # ogni 24 ore
            n = await asyncio.to_thread(_cleanup_old_outputs)
            if n:
                print(f"[cleanup] {n} output vecchi rimossi (pulizia periodica)")

    asyncio.create_task(_periodic_cleanup())


# ── Validazione file upload ────────────────────────────────────────────────────
MAX_FILE_SIZE = 3 * 1024 * 1024 * 1024  # 3 GB — limite per singolo file

_SUPPORTED_EXTENSIONS = {
    ".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm", ".mkv", ".mov",
    ".pptx", ".pdf", ".docx", ".ppt", ".doc",
}

# (magic_prefix, set di estensioni compatibili)
_MAGIC_SIGNATURES: list[tuple[bytes, set[str]]] = [
    (b"%PDF",        {".pdf"}),
    (b"PK\x03\x04",  {".pptx", ".docx", ".ppt", ".doc"}),
    (b"ID3",          {".mp3"}),
    (b"\xff\xfb",     {".mp3"}),
    (b"\xff\xf3",     {".mp3"}),
    (b"\xff\xf2",     {".mp3"}),
    (b"RIFF",         {".wav"}),
    (b"OggS",         {".ogg", ".webm"}),
    (b"\x1aE\xdf\xa3", {".webm", ".mkv"}),
]


def _check_file_header(filename: str, header: bytes) -> str | None:
    """Ritorna stringa di warning se i magic bytes non corrispondono all'estensione, None se ok."""
    ext = Path(filename).suffix.lower()
    for magic, exts in _MAGIC_SIGNATURES:
        if header[:len(magic)] == magic:
            if ext not in exts:
                return f"{filename}: magic bytes suggeriscono {exts}, estensione dichiarata {ext!r}"
            return None  # match corretto → ok
    # MP4/M4A: 'ftyp' a offset 4
    if len(header) >= 8 and header[4:8] == b"ftyp":
        if ext not in {".mp4", ".m4a", ".m4v", ".mov"}:
            return f"{filename}: magic bytes suggeriscono video MP4, estensione dichiarata {ext!r}"
        return None
    # Magic non riconosciuto — non blocchiamo (formato legittimo non in lista)
    return None


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
                       continue_from: str = "",
                       continue_on_error: bool = False):
    """Eseguito in background thread da BackgroundTasks."""
    with _jobs_lock:
        if job_id not in jobs:
            return  # job eliminato prima che il thread partisse
        jobs[job_id]["status"] = "running"
        _save_jobs()

    # ── Continua un corso precedente: copia state.json e corso_context.json ──
    if continue_from:
        # Prima cerca in memoria (job ancora presente), poi su disco (server riavviato)
        with _jobs_lock:
            prev_out_str = jobs[continue_from]["output_dir"] if continue_from in jobs else None
        if prev_out_str:
            prev_out = Path(prev_out_str)
        else:
            # Fallback su disco: cerca ricorsivamente in outputs/{prev_job_id}/
            candidates = list((OUTPUT_DIR / continue_from).rglob("state.json"))
            prev_out = candidates[0].parent if candidates else None

        if prev_out:  # type: ignore[truthy-bool]
            for fname in ("state.json", "corso_context.json"):
                src = prev_out / fname
                if src.exists():
                    shutil.copy2(src, output_dir / fname)
                    print(f"[continue] copiato {fname} da {prev_out}")
        else:
            print(f"[continue] job {continue_from} non trovato in memoria né su disco — ignoro")

    # ── Copia file uploadati nel debug dir — per ispezione e riproducibilità ──
    # Vengono copiati PRIMA che la pipeline parta, così sono disponibili
    # anche se la pipeline fallisce o se uploads/ viene pulita in seguito.
    debug_uploads_dir = output_dir / "debug" / "uploads"
    debug_uploads_dir.mkdir(parents=True, exist_ok=True)
    with _jobs_lock:
        _job_files = jobs.get(job_id, {}).get("files", [])
    for f_name in _job_files:
        src = lesson_dir / f_name
        if src.exists():
            shutil.copy2(src, debug_uploads_dir / f_name)

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
        "python3", "-u", "pipeline.py",  # -u: stdout unbuffered → righe visibili in real-time
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
    if continue_on_error:
        cmd.append("--continue-on-error")

    log_path = output_dir / "pipeline.log"
    log_path.write_text("", encoding="utf-8")  # crea subito — SSE può iniziare a leggere

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge stderr in stdout — un unico stream ordinato
            text=True,
            cwd=Path(__file__).parent,
        )

        # Timer: killa il processo dopo 1 ora
        _timed_out = threading.Event()
        def _kill():
            _timed_out.set()
            process.kill()
        _timer = threading.Timer(3600, _kill)
        _timer.start()

        stdout_lines: list[str] = []
        try:
            with open(log_path, "a", encoding="utf-8") as _lf:
                for line in process.stdout:
                    stdout_lines.append(line)
                    _lf.write(line)
                    _lf.flush()
            process.wait()
        finally:
            _timer.cancel()

        if _timed_out.is_set():
            raise subprocess.TimeoutExpired(cmd, 3600)

        stdout_text = "".join(stdout_lines)
        returncode  = process.returncode

        with _jobs_lock:
            jobs[job_id]["stdout"]      = stdout_text
            # stderr: preview 3000 char + testo completo per debug
            jobs[job_id]["stderr"]      = "" if returncode == 0 else stdout_text[-3000:]
            jobs[job_id]["stderr_full"] = "" if returncode == 0 else stdout_text
            jobs[job_id]["returncode"]  = returncode

        if returncode == 0:
            # Tenta compilazione PDF con pdflatex (se disponibile)
            _pdflatex_available = bool(shutil.which("pdflatex"))
            if _pdflatex_available:
                main_tex = output_dir / "main.tex"
                if main_tex.exists():
                    for _ in range(2):
                        try:
                            subprocess.run(
                                ["pdflatex", "-interaction=nonstopmode", "main.tex"],
                                cwd=output_dir,
                                timeout=120,
                                capture_output=True,
                            )
                        except Exception:
                            break
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
            has_pdf = (output_dir / "main.pdf").exists()

            # Estrai errori LaTeX da main.log
            pdf_errors: list[str] = []
            if not _pdflatex_available:
                pdf_errors = ["pdflatex non installato — PDF non può essere generato.\n"
                              "Installa: sudo apt install texlive-latex-base texlive-latex-recommended "
                              "texlive-latex-extra texlive-lang-italian texlive-fonts-recommended"]
            elif not has_pdf:
                log_file = output_dir / "main.log"
                if log_file.exists():
                    try:
                        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                        for i, ln in enumerate(lines):
                            if ln.startswith("!"):
                                block = "\n".join(lines[i:i+3]).strip()
                                pdf_errors.append(block)
                            if len(pdf_errors) >= 10:
                                break
                    except Exception:
                        pass
                if not pdf_errors:
                    pdf_errors = ["pdflatex ha fallito senza errori espliciti — controlla main.log nell'archivio ZIP"]

            with _jobs_lock:
                jobs[job_id]["status"]     = "done"
                jobs[job_id]["zip_path"]   = str(zip_path)
                jobs[job_id]["has_pdf"]    = has_pdf
                jobs[job_id]["pdf_errors"] = pdf_errors
                _save_jobs()
        else:
            with _jobs_lock:
                jobs[job_id]["status"] = "error"
                _save_jobs()

    except subprocess.TimeoutExpired:
        try:
            log_path.open("a", encoding="utf-8").write("\n[TIMEOUT — pipeline fermata dopo 1 ora]\n")
        except Exception:
            pass
        with _jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["stderr"] = "Timeout: pipeline impiegato più di 1 ora"
            _save_jobs()
        shutil.rmtree(UPLOAD_DIR / job_id, ignore_errors=True)
    except Exception as e:
        try:
            log_path.open("a", encoding="utf-8").write(f"\n[ERRORE SERVER: {e}]\n")
        except Exception:
            pass
        with _jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["stderr"] = str(e)
            _save_jobs()
        shutil.rmtree(UPLOAD_DIR / job_id, ignore_errors=True)


@app.post("/run-pipeline")
async def run_pipeline(
    background_tasks: BackgroundTasks,
    title: str = Form(...),
    skip_ai: str = Form("false"),
    skip_ocr: str = Form("false"),
    no_context: str = Form("false"),
    continue_on_error: str = Form("false"),
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
    _skip_ai            = skip_ai.lower()           in ("true", "1", "yes")
    _skip_ocr           = skip_ocr.lower()          in ("true", "1", "yes")
    _no_context         = no_context.lower()         in ("true", "1", "yes")
    _continue_on_error  = continue_on_error.lower()  in ("true", "1", "yes")
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

    # Valida e salva file caricati
    # Path(f.filename).name rimuove componenti di directory (path traversal prevention)
    saved = []
    validation_errors: list[str] = []
    for f in files:
        safe_name = Path(f.filename).name   # strip directory component
        if not safe_name:
            continue
        ext = Path(safe_name).suffix.lower()
        if ext not in _SUPPORTED_EXTENSIONS:
            validation_errors.append(f"{safe_name}: estensione non supportata ({ext or 'nessuna'})")
            continue
        # Controlla dimensione (se il client ha inviato Content-Length per il file)
        if hasattr(f, "size") and f.size is not None and f.size > MAX_FILE_SIZE:
            validation_errors.append(
                f"{safe_name}: file troppo grande ({f.size / 1e9:.1f} GB, max {MAX_FILE_SIZE // 1e9:.0f} GB)"
            )
            continue
        # Leggi header (16 byte) per magic check, poi riavvolgi
        header = await f.read(16)
        if not header:
            validation_errors.append(f"{safe_name}: file vuoto")
            continue
        warn = _check_file_header(safe_name, header)
        if warn:
            print(f"[upload warning] {warn}")
        await f.seek(0)
        dest = lesson_dir / safe_name
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(safe_name)

    # Se tutti i file sono stati rifiutati E non ci sono URL Teams → blocca subito
    if validation_errors and not saved and not teams_url:
        shutil.rmtree(lesson_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="; ".join(validation_errors))

    _continue_from = continue_from.strip()

    # Inizializza job
    jobs[job_id] = {
        "status":        "queued",
        "title":         title,
        "created_at":    datetime.datetime.now().isoformat(timespec="seconds"),
        "files":         saved,
        "subject":       _subject,
        "whisper_model": whisper_model,
        "no_context":    _no_context,
        "start_from":    _start_from,
        "skip_ai":            _skip_ai,
        "skip_ocr":           _skip_ocr,
        "continue_on_error":  _continue_on_error,
        "output_name":        output_name,
        "output_dir":         str(output_dir),   # per leggere progress.json
        "teams_urls":         teams_url,
        "continue_from":      _continue_from,
        "stdout":             "",
        "stderr":             "",
        "returncode":         None,
        "zip_path":           None,
        "has_pdf":            False,
    }

    _save_jobs()

    # Lancia in background (non blocca la risposta HTTP)
    background_tasks.add_task(
        _run_pipeline_job,
        job_id, lesson_dir, output_dir,
        title, _skip_ai, _skip_ocr, whisper_model,
        _subject, _no_context, _start_from, teams_url or [],
        _continue_from, _continue_on_error
    )

    resp: dict = {
        "job_id":  job_id,
        "status":  "queued",
        "files":   saved,
        "message": f"Pipeline avviata. Fai polling su /job/{job_id}"
    }
    if validation_errors:
        resp["warnings"] = validation_errors  # file ignorati per estensione non supportata
    return JSONResponse(resp)


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
        "stdout":             job["stdout"],
        "stderr":             job["stderr"],
        "returncode":         job["returncode"],
        "has_pdf":            job.get("has_pdf", False),
        "pdf_errors":         job.get("pdf_errors", []),
        "download":           f"/download/{job_id}" if job["status"] == "done" else None,
        "preview":            f"/preview/{job_id}"  if job["status"] == "done" else None,
    }
    return JSONResponse(response)


@app.get("/download/{job_id}")
async def download_output(job_id: str):
    """Scarica lo ZIP con main.tex + lezione_NN.tex + images/.
    Funziona anche se il job è stato rimosso dalla memoria (job eliminato ma ZIP su disco)."""
    job   = jobs.get(job_id)
    title = job["title"] if job else job_id

    if job and job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job non completato (stato: {job['status']})")

    # Prova prima il path salvato nel job, poi il path convenzionale su disco
    zip_path = Path(job["zip_path"]) if job and job.get("zip_path") else OUTPUT_DIR / f"{job_id}.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail="ZIP non trovato")

    title_safe = "".join(c for c in title if c.isalnum() or c in "_- ").strip().replace(" ", "_")
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=f"appunti_{title_safe}.zip"
    )


@app.get("/preview/{job_id}")
async def preview_latex(job_id: str):
    """Restituisce il contenuto di main.tex per la preview nel browser.
    Funziona anche dopo che il job è stato rimosso dalla memoria."""
    job = jobs.get(job_id)

    if job and job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job non completato (stato: {job['status']})")

    # Ricostruisci il path se il job non è più in memoria
    if job:
        output_dir = Path(job["output_dir"])
        title      = job["title"]
        has_pdf    = job.get("has_pdf", False)
    else:
        # Cerca main.tex in OUTPUT_DIR/job_id/**/
        candidates = list((OUTPUT_DIR / job_id).rglob("main.tex")) if (OUTPUT_DIR / job_id).exists() else []
        if not candidates:
            raise HTTPException(status_code=404, detail="Job non trovato e nessun output su disco")
        output_dir = candidates[0].parent
        title      = job_id
        has_pdf    = (output_dir / "main.pdf").exists()

    main_tex = output_dir / "main.tex"
    if not main_tex.exists():
        raise HTTPException(status_code=404, detail="main.tex non trovato")
    return JSONResponse({
        "job_id":  job_id,
        "title":   title,
        "has_pdf": has_pdf,
        "content": main_tex.read_text(encoding="utf-8"),
    })


@app.get("/job/{job_id}/stream")
async def stream_job_log(job_id: str, request: Request):
    """
    Server-Sent Events: streamma pipeline.log in tempo reale.
    Il client apre un EventSource su questo endpoint per vedere il log live.
    """
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job non trovato")

    log_path = Path(jobs[job_id]["output_dir"]) / "pipeline.log"

    async def generate():
        offset = 0
        # Invia header keep-alive subito
        yield ": keep-alive\n\n"
        while True:
            if await request.is_disconnected():
                break

            # Leggi nuovo contenuto dal log
            if log_path.exists():
                try:
                    content = log_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                if len(content) > offset:
                    chunk = content[offset:]
                    offset = len(content)
                    for line in chunk.splitlines():
                        line = line.rstrip()
                        if line:
                            yield f"data: {line}\n\n"

            # Controlla se il job è finito
            status = jobs.get(job_id, {}).get("status", "error")
            if status in ("done", "error") and (not log_path.exists() or offset >= log_path.stat().st_size):
                yield "data: [FINE]\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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


@app.get("/health")
async def health():
    """Stato del sistema: API key, strumenti di sistema disponibili."""
    return JSONResponse({
        "api_key":  bool(os.environ.get("ANTHROPIC_API_KEY")),
        "ffmpeg":   bool(shutil.which("ffmpeg")),
        "pdflatex": bool(shutil.which("pdflatex")),
        "whisper":  True,  # se il server è avviato, whisper è installato nel venv
    })


@app.get("/jobs")
async def list_jobs():
    """Lista tutti i job con metadati completi (per history nel frontend)."""
    result = {}
    for jid, j in list(jobs.items()):
        zip_p = OUTPUT_DIR / f"{jid}.zip"
        result[jid] = {
            "status":     j["status"],
            "title":      j["title"],
            "files":      j.get("files", []),
            "created_at": j.get("created_at", ""),
            "has_pdf":    j.get("has_pdf", False),
            "has_zip":    zip_p.exists(),
        }
    return JSONResponse(result)


@app.delete("/job/{job_id}")
async def delete_job(job_id: str, full: bool = False):
    """
    Elimina job e file associati.
    - Default (full=false): rimuove solo uploads/ (file originali), mantiene output/ e zip.
    - full=true: rimuove tutto inclusa la cartella output con tex e immagini.
    """
    with _jobs_lock:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Job non trovato")
        del jobs[job_id]
        _save_jobs()
    # Rimuovi sempre: file caricati dall'utente (non servono più dopo la pipeline)
    shutil.rmtree(UPLOAD_DIR / job_id, ignore_errors=True)
    # Rimuovi zip e output solo se richiesto esplicitamente
    if full:
        zip_p = OUTPUT_DIR / f"{job_id}.zip"
        if zip_p.exists():
            zip_p.unlink()
        shutil.rmtree(OUTPUT_DIR / job_id, ignore_errors=True)
    return JSONResponse({"deleted": job_id, "output_kept": not full})