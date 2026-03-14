"""
server.py — Backend FastAPI per Appunti AI
========================================
Avvia con:
    pip install fastapi uvicorn python-multipart aiofiles
    uvicorn server:app --reload --host 0.0.0.0 --port 8000
"""

import asyncio
import datetime
import logging
import logging.handlers
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

# ── Logging strutturato su file + console ──────────────────────────────────────
_LOGS_DIR = Path("logs")
_LOGS_DIR.mkdir(exist_ok=True)

_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_file_handler = logging.handlers.RotatingFileHandler(
    _LOGS_DIR / "server.log",
    maxBytes=5 * 1024 * 1024,   # 5 MB per file
    backupCount=3,               # mantieni 3 file → max 15 MB totali
    encoding="utf-8",
)
_file_handler.setFormatter(_log_formatter)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_log_formatter)

log = logging.getLogger("appunti_ai")
log.setLevel(logging.DEBUG)
log.addHandler(_file_handler)
log.addHandler(_console_handler)
# Silenzia i log verbosi di uvicorn/fastapi che non ci interessano
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

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


_JOBS_MEMORY_LIMIT = 200  # max job in memoria; oltre questo, i più vecchi done/error vengono scartati


def _trim_jobs_memory() -> None:
    """
    Mantiene al massimo _JOBS_MEMORY_LIMIT job in memoria.
    Scarta i più vecchi tra quelli terminati (done/error), mai quelli running/queued.
    Deve essere chiamata DENTRO un blocco `with _jobs_lock`.
    """
    if len(jobs) <= _JOBS_MEMORY_LIMIT:
        return
    evictable = sorted(
        [(jid, j) for jid, j in jobs.items()
         if j.get("status") in ("done", "error")],
        key=lambda x: x[1].get("created_at", ""),
    )
    to_remove = len(jobs) - _JOBS_MEMORY_LIMIT
    for jid, _ in evictable[:to_remove]:
        jobs.pop(jid, None)


def _save_jobs() -> None:
    """Serializza jobs su disco (senza stdout/stderr per tenere il file piccolo).
    Deve essere chiamata DENTRO un blocco `with _jobs_lock`."""
    import json
    _trim_jobs_memory()
    try:
        slim = {jid: {k: v for k, v in j.items() if k not in ("stdout", "stderr")}
                for jid, j in jobs.items()}
        JOBS_FILE.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        log.warning("_save_jobs: impossibile scrivere %s — %s", JOBS_FILE, e)


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
        log.info("Job caricati da disco: %d", len(jobs))
    except Exception as e:
        log.error("_load_jobs: impossibile leggere %s — %s", JOBS_FILE, e)


_load_jobs()

# ── Settings persistenti (API key, ecc.) ──────────────────────────────────────
import json as _json_mod

SETTINGS_FILE = Path(__file__).parent / "settings.json"


_SETTINGS: dict = {}  # settings in memoria, caricati all'avvio


def _load_settings() -> None:
    """Carica settings.json all'avvio — imposta variabili d'ambiente e _SETTINGS."""
    global _SETTINGS
    if not SETTINGS_FILE.exists():
        return
    try:
        s = _json_mod.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        _SETTINGS = s
        if key := s.get("api_key", "").strip():
            os.environ.setdefault("ANTHROPIC_API_KEY", key)
        # Warning sicurezza: settings.json leggibile da altri utenti di sistema
        try:
            mode = SETTINGS_FILE.stat().st_mode & 0o777
            if mode & 0o044:  # group-read o world-read
                log.warning(
                    "SICUREZZA: %s è leggibile da altri utenti (permessi %o). "
                    "Esegui: chmod 600 %s",
                    SETTINGS_FILE, mode, SETTINGS_FILE,
                )
        except Exception:
            pass
    except Exception as e:
        log.error("_load_settings: impossibile leggere %s — %s", SETTINGS_FILE, e)


def _save_settings(data: dict) -> None:
    try:
        SETTINGS_FILE.write_text(
            _json_mod.dumps(data, indent=2), encoding="utf-8"
        )
        # Imposta permessi restrittivi (solo proprietario) se contiene API key
        if data.get("api_key"):
            try:
                SETTINGS_FILE.chmod(0o600)
            except Exception:
                pass
    except Exception as e:
        log.error("_save_settings: impossibile scrivere %s — %s", SETTINGS_FILE, e)


_load_settings()


@app.post("/settings")
async def save_settings_endpoint(request: Request):
    """Salva API key e/o configurazione (ttl_days). Persiste su settings.json."""
    global OUTPUT_TTL_DAYS
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body JSON non valido")

    # API key
    key = body.get("api_key", "").strip()
    if "api_key" in body:
        if key:
            os.environ["ANTHROPIC_API_KEY"] = key
        else:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        _SETTINGS["api_key"] = key

    # TTL giorni (1–365)
    if "ttl_days" in body:
        try:
            ttl = int(body["ttl_days"])
            if 1 <= ttl <= 365:
                OUTPUT_TTL_DAYS = ttl
                _SETTINGS["ttl_days"] = ttl
        except (ValueError, TypeError):
            pass

    # Timeout ffmpeg download Teams in secondi (300–86400, default 7200)
    if "ffmpeg_timeout" in body:
        try:
            ft = int(body["ffmpeg_timeout"])
            if 300 <= ft <= 86400:
                _SETTINGS["ffmpeg_timeout"] = ft
        except (ValueError, TypeError):
            pass

    # Timeout intera pipeline in secondi (300–86400, default 3600)
    if "pipeline_timeout" in body:
        try:
            pt = int(body["pipeline_timeout"])
            if 300 <= pt <= 86400:
                _SETTINGS["pipeline_timeout"] = pt
        except (ValueError, TypeError):
            pass

    # Job concorrenti massimi (1–10, default 2)
    if "max_concurrent_jobs" in body:
        try:
            mj = int(body["max_concurrent_jobs"])
            if 1 <= mj <= 10:
                _SETTINGS["max_concurrent_jobs"] = mj
        except (ValueError, TypeError):
            pass

    _save_settings(_SETTINGS)
    return JSONResponse({"ok": True, "api_key": bool(key or _SETTINGS.get("api_key")),
                         "ttl_days": OUTPUT_TTL_DAYS,
                         "ffmpeg_timeout": int(_SETTINGS.get("ffmpeg_timeout", 7200)),
                         "pipeline_timeout": int(_SETTINGS.get("pipeline_timeout", 3600)),
                         "max_concurrent_jobs": int(_SETTINGS.get("max_concurrent_jobs", 2))})


@app.get("/settings")
async def get_settings_endpoint():
    """Restituisce la configurazione corrente (senza esporre la key vera)."""
    return JSONResponse({
        "api_key": bool(_SETTINGS.get("api_key", "").strip()),
        "ttl_days": OUTPUT_TTL_DAYS,
        "ffmpeg_timeout": int(_SETTINGS.get("ffmpeg_timeout", 7200)),
        "pipeline_timeout": int(_SETTINGS.get("pipeline_timeout", 3600)),
        "max_concurrent_jobs": int(_SETTINGS.get("max_concurrent_jobs", 2)),
    })


# ── Cleanup automatico output vecchi ──────────────────────────────────────────
OUTPUT_TTL_DAYS = int(_SETTINGS.get("ttl_days", 7))  # configurabile da /settings


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


def _recover_orphan_jobs() -> int:
    """
    Scansiona outputs/ e reinserisce in jobs{} i job che hanno un output su disco
    ma non sono presenti in memoria (es. dopo perdita di jobs.json).
    """
    recovered = 0
    for out_dir in OUTPUT_DIR.iterdir():
        if not out_dir.is_dir():
            continue
        jid = out_dir.name
        with _jobs_lock:
            if jid in jobs:
                continue
        # Prova a leggere state.json per titolo e data
        state_files = list(out_dir.rglob("state.json"))
        title = jid
        created_at = ""
        if state_files:
            try:
                import json as _j
                state = _j.loads(state_files[0].read_text(encoding="utf-8"))
                title = state.get("course_title") or jid
                lessons = state.get("lessons", [])
                if lessons:
                    created_at = lessons[-1].get("processed_at", "")
            except Exception:
                pass
        if not created_at:
            try:
                import datetime as _dt
                created_at = _dt.datetime.fromtimestamp(
                    out_dir.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                pass
        zip_exists = (OUTPUT_DIR / f"{jid}.zip").exists()
        # Trova la sottocartella di output (es. outputs/{jid}/pe3/)
        output_subdirs = [d for d in out_dir.iterdir() if d.is_dir() and d.name != "debug"]
        output_dir_str = str(output_subdirs[0]) if output_subdirs else str(out_dir)
        has_pdf = any(out_dir.rglob("main.pdf"))
        with _jobs_lock:
            jobs[jid] = {
                "status":     "done",
                "title":      title,
                "files":      [],
                "created_at": created_at,
                "has_pdf":    has_pdf,
                "output_dir": output_dir_str,
                "zip_path":   str(OUTPUT_DIR / f"{jid}.zip") if zip_exists else None,
                "stdout":     "",
                "stderr":     "[recuperato da disco dopo perdita jobs.json]",
            }
            recovered += 1
    if recovered:
        with _jobs_lock:
            _save_jobs()
    return recovered


@app.on_event("startup")
async def _on_startup():
    r = await asyncio.to_thread(_recover_orphan_jobs)
    if r:
        print(f"[recovery] {r} job recuperati da disco (jobs.json era incompleto)")
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
    # Audio/video
    ".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".webm", ".mkv", ".mov",
    # Documenti strutturati
    ".pptx", ".pdf", ".docx", ".ppt", ".doc",
    # Testo (trascrizioni, note, RTF)
    ".txt", ".md", ".rtf",
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
    (b"{\\rtf",       {".rtf", ".txt"}),  # RTF salvato come .txt
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
    log.info("Job avviato: %s | title=%r | files=%s", job_id, title, len(jobs.get(job_id, {}).get("files", [])))

    # ── Continua un corso precedente: copia state.json e corso_context.json ──
    if continue_from:
        # Prima cerca in memoria (job ancora presente), poi su disco (server riavviato)
        with _jobs_lock:
            prev_out_str = jobs[continue_from].get("output_dir") if continue_from in jobs else None
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
                    log.info("[continue] copiato %s da %s", fname, prev_out)
            # Copia tutti i file .tex delle lezioni/run precedenti
            for tex in prev_out.glob("lezione_*.tex"):
                shutil.copy2(tex, output_dir / tex.name)
                log.info("[continue] copiato %s da %s", tex.name, prev_out)
            for tex in prev_out.glob("run_*.tex"):
                shutil.copy2(tex, output_dir / tex.name)
                log.info("[continue] copiato %s da %s", tex.name, prev_out)
            # Copia la cartella images/ (immagini lezioni precedenti)
            prev_images = prev_out / "images"
            new_images  = output_dir / "images"
            if prev_images.is_dir():
                new_images.mkdir(exist_ok=True)
                for img in prev_images.iterdir():
                    if img.is_file():
                        dest = new_images / img.name
                        if not dest.exists():  # non sovrascrivere se già copiata
                            shutil.copy2(img, dest)
                log.info("[continue] copiata images/ da %s (%d file)", prev_out, len(list(prev_images.iterdir())))
        else:
            log.warning("[continue] job %s non trovato in memoria né su disco — ignoro", continue_from)

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
        _ffmpeg_timeout = int(_SETTINGS.get("ffmpeg_timeout", 7200))
        for i, raw_url in enumerate(teams_urls):
            url = _clean_teams_url(raw_url.strip())
            if not url:
                continue
            mp3_out = lesson_dir / f"teams_{i+1:02d}.mp3"
            log.info("[TeamsHack] Download audio %d/%d: %s…", i+1, len(teams_urls), url[:80])
            for attempt in range(1, 4):  # max 3 tentativi
                try:
                    r = subprocess.run(
                        ["ffmpeg", "-y", "-i", url,
                         "-vn", "-ac", "1", "-codec:a", "libmp3lame", "-qscale:a", "4",
                         str(mp3_out)],
                        timeout=_ffmpeg_timeout,
                        check=False,
                    )
                    if r.returncode == 0:
                        log.info("[TeamsHack] Download completato: %s", mp3_out.name)
                        break
                    log.warning("[TeamsHack] Tentativo %d/3 fallito (returncode=%d)", attempt, r.returncode)
                except subprocess.TimeoutExpired:
                    log.warning("[TeamsHack] Timeout (%ds) al tentativo %d/3 — abbandono", _ffmpeg_timeout, attempt)
                    break  # timeout → inutile riprovare
                except FileNotFoundError:
                    log.error("[TeamsHack] ffmpeg non trovato — installa ffmpeg")
                    break
                except Exception as e:
                    log.warning("[TeamsHack] Errore tentativo %d/3: %s", attempt, e)

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

        # Timer: killa il processo dopo il timeout configurato (default 1 ora)
        _pipeline_timeout = int(_SETTINGS.get("pipeline_timeout", 3600))
        _timed_out = threading.Event()
        def _kill():
            _timed_out.set()
            process.kill()
        _timer = threading.Timer(_pipeline_timeout, _kill)
        _timer.start()

        # Buffer circolare: teniamo solo le ultime 500 righe in RAM
        # Il log completo è su disco (log_path) — nessun memory leak
        _MAX_LOG_LINES = 500
        stdout_tail: list[str] = []
        try:
            with open(log_path, "a", encoding="utf-8") as _lf:
                for line in process.stdout:
                    _lf.write(line)
                    _lf.flush()
                    stdout_tail.append(line)
                    if len(stdout_tail) > _MAX_LOG_LINES:
                        stdout_tail.pop(0)
            process.wait()
        finally:
            _timer.cancel()

        if _timed_out.is_set():
            raise subprocess.TimeoutExpired(cmd, _pipeline_timeout)

        # In memoria: ultime 500 righe (max ~50KB) per UI; log completo su disco
        stdout_text = "".join(stdout_tail)
        returncode  = process.returncode

        with _jobs_lock:
            jobs[job_id]["stdout"]     = stdout_text
            jobs[job_id]["stderr"]     = "" if returncode == 0 else stdout_text
            jobs[job_id]["log_path"]   = str(log_path)   # percorso log completo su disco
            jobs[job_id]["returncode"] = returncode

        if returncode == 0:
            # Tenta compilazione PDF con pdflatex (se disponibile)
            _pdflatex_available = bool(shutil.which("pdflatex"))
            if _pdflatex_available:
                main_tex = output_dir / "main.tex"
                if main_tex.exists():
                    # Passo 0: draftmode — rileva errori senza generare PDF (veloce)
                    # Se ci sono errori fatali, i 2 pass successivi possono comunque
                    # produrre un PDF parziale con nonstopmode.
                    try:
                        subprocess.run(
                            ["pdflatex", "-interaction=nonstopmode",
                             "-draftmode", "main.tex"],
                            cwd=output_dir,
                            timeout=60,
                            capture_output=True,
                        )
                    except Exception:
                        pass
                    # Passi 1-2: compilazione reale (doppia per TOC/riferimenti)
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
            log.info("Job completato: %s | has_pdf=%s | pdf_errors=%d",
                     job_id, has_pdf, len(pdf_errors))
            # File originali non più necessari dopo completamento
            shutil.rmtree(UPLOAD_DIR / job_id, ignore_errors=True)
        else:
            with _jobs_lock:
                jobs[job_id]["status"] = "error"
                _save_jobs()
            log.error("Job fallito: %s | returncode=%s", job_id, returncode)
            shutil.rmtree(UPLOAD_DIR / job_id, ignore_errors=True)

    except subprocess.TimeoutExpired:
        try:
            log_path.open("a", encoding="utf-8").write("\n[TIMEOUT — pipeline fermata dopo 1 ora]\n")
        except Exception:
            pass
        with _jobs_lock:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["stderr"] = f"Timeout: pipeline impiegata più di {_pipeline_timeout//60} minuti"
            _save_jobs()
        log.error("Job timeout: %s dopo %d minuti", job_id, _pipeline_timeout // 60)
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
        log.exception("Job eccezione non gestita: %s", job_id)
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

    # Rate limiting: controlla job in esecuzione
    _max_concurrent = int(_SETTINGS.get("max_concurrent_jobs", 2))
    with _jobs_lock:
        _running_count = sum(1 for j in jobs.values() if j.get("status") == "running")
    if _running_count >= _max_concurrent:
        raise HTTPException(
            status_code=429,
            detail=f"Troppi job in esecuzione ({_running_count}/{_max_concurrent}). "
                   f"Attendi che uno finisca prima di avviarne un altro."
        )

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


@app.get("/pdf/{job_id}")
async def download_pdf(job_id: str):
    """Scarica il main.pdf compilato da pdflatex."""
    job   = jobs.get(job_id)
    title = job["title"] if job else job_id

    stored_dir = job.get("output_dir") if job else None
    if stored_dir and Path(stored_dir).exists():
        output_dir = Path(stored_dir)
    else:
        candidates = list((OUTPUT_DIR / job_id).rglob("main.pdf")) if (OUTPUT_DIR / job_id).exists() else []
        if not candidates:
            raise HTTPException(status_code=404, detail="main.pdf non trovato su disco")
        output_dir = candidates[0].parent

    pdf_path = output_dir / "main.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="main.pdf non trovato")

    return FileResponse(
        path=pdf_path,
        media_type="application/pdf",
    )


@app.post("/save/{job_id}")
async def save_tex_file(job_id: str, request: Request):
    """Salva un file .tex senza ricompilare."""
    body = {}
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
    except Exception:
        pass

    content  = body.get("content")
    filename = body.get("file", "main.tex")
    if "/" in filename or "\\" in filename or not filename.endswith(".tex"):
        filename = "main.tex"
    if content is None:
        raise HTTPException(status_code=400, detail="content mancante")

    job = jobs.get(job_id)
    stored_dir = job.get("output_dir") if job else None
    if stored_dir and Path(stored_dir).exists():
        output_dir = Path(stored_dir)
    else:
        candidates = list((OUTPUT_DIR / job_id).rglob("main.tex")) if (OUTPUT_DIR / job_id).exists() else []
        if not candidates:
            raise HTTPException(status_code=404, detail="output non trovato su disco")
        output_dir = candidates[0].parent

    (output_dir / filename).write_text(content, encoding="utf-8")
    return JSONResponse({"saved": filename})


@app.post("/recompile/{job_id}")
async def recompile_latex(job_id: str, request: Request):
    """Salva main.tex (se body.content fornito) e ricompila con pdflatex."""
    body = {}
    try:
        if request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
    except Exception:
        pass

    job = jobs.get(job_id)
    stored_dir = job.get("output_dir") if job else None
    if stored_dir and Path(stored_dir).exists():
        output_dir = Path(stored_dir)
    else:
        candidates = list((OUTPUT_DIR / job_id).rglob("main.tex")) if (OUTPUT_DIR / job_id).exists() else []
        if not candidates:
            raise HTTPException(status_code=404, detail="output non trovato su disco")
        output_dir = candidates[0].parent

    content  = body.get("content")
    filename = body.get("file", "main.tex")
    if "/" in filename or "\\" in filename or not filename.endswith(".tex"):
        filename = "main.tex"
    if content is not None:
        (output_dir / filename).write_text(content, encoding="utf-8")

    if not shutil.which("pdflatex"):
        return JSONResponse({"has_pdf": False, "pdf_errors": ["pdflatex non installato"]})

    for _ in range(2):
        try:
            subprocess.run(
                ["pdflatex", "-interaction=nonstopmode", "main.tex"],
                cwd=output_dir, timeout=120, capture_output=True,
            )
        except Exception:
            break

    has_pdf = (output_dir / "main.pdf").exists()
    pdf_errors: list[str] = []
    if not has_pdf:
        log_file = output_dir / "main.log"
        if log_file.exists():
            try:
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                for i, ln in enumerate(lines):
                    if ln.startswith("!"):
                        pdf_errors.append("\n".join(lines[i:i+3]).strip())
                    if len(pdf_errors) >= 10:
                        break
            except Exception:
                pass
        if not pdf_errors:
            pdf_errors = ["pdflatex ha fallito senza errori espliciti"]

    if job_id in jobs:
        with _jobs_lock:
            jobs[job_id]["has_pdf"] = has_pdf
            jobs[job_id]["pdf_errors"] = pdf_errors
            _save_jobs()

    return JSONResponse({"has_pdf": has_pdf, "pdf_errors": pdf_errors})


@app.get("/preview/{job_id}")
async def preview_latex(job_id: str):
    """Restituisce il contenuto di main.tex per la preview nel browser.
    Funziona anche dopo che il job è stato rimosso dalla memoria."""
    job = jobs.get(job_id)

    if job and job["status"] != "done":
        raise HTTPException(status_code=400, detail=f"Job non completato (stato: {job['status']})")

    # Ricostruisci il path — usa output_dir dal job se disponibile, altrimenti rglob
    title   = job["title"] if job else job_id
    has_pdf = job.get("has_pdf", False) if job else False

    stored_dir = job.get("output_dir") if job else None
    if stored_dir and Path(stored_dir).exists():
        output_dir = Path(stored_dir)
    else:
        candidates = list((OUTPUT_DIR / job_id).rglob("main.tex")) if (OUTPUT_DIR / job_id).exists() else []
        if not candidates:
            raise HTTPException(status_code=404, detail="main.tex non trovato su disco")
        output_dir = candidates[0].parent
        has_pdf    = (output_dir / "main.pdf").exists()

    main_tex = output_dir / "main.tex"
    if not main_tex.exists():
        raise HTTPException(status_code=404, detail="main.tex non trovato")
    tex_files = sorted(
        [f.name for f in output_dir.glob("*.tex")],
        key=lambda n: (n != "main.tex", n)   # main.tex sempre primo
    )
    return JSONResponse({
        "job_id":    job_id,
        "title":     title,
        "has_pdf":   has_pdf,
        "content":   main_tex.read_text(encoding="utf-8"),
        "tex_files": tex_files,
    })


@app.get("/tex/{job_id}/{filename}")
async def read_tex_file(job_id: str, filename: str):
    """Restituisce il contenuto di un qualsiasi .tex nell'output del job."""
    if not filename.endswith(".tex") or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Nome file non valido")

    job = jobs.get(job_id)
    stored_dir = job.get("output_dir") if job else None
    if stored_dir and Path(stored_dir).exists():
        output_dir = Path(stored_dir)
    else:
        candidates = list((OUTPUT_DIR / job_id).rglob("main.tex")) if (OUTPUT_DIR / job_id).exists() else []
        if not candidates:
            raise HTTPException(status_code=404, detail="output non trovato su disco")
        output_dir = candidates[0].parent

    tex_path = output_dir / filename
    if not tex_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} non trovato")
    return JSONResponse({"filename": filename, "content": tex_path.read_text(encoding="utf-8")})


@app.get("/prompt/{job_id}")
async def read_prompt_files(job_id: str):
    """Restituisce i file prompt_lezione_NN.txt dalla cartella debug/ del job."""
    job = jobs.get(job_id)
    stored_dir = job.get("output_dir") if job else None
    if stored_dir and Path(stored_dir).exists():
        output_dir = Path(stored_dir)
    else:
        candidates = list((OUTPUT_DIR / job_id).rglob("main.tex")) if (OUTPUT_DIR / job_id).exists() else []
        if not candidates:
            raise HTTPException(status_code=404, detail="output non trovato su disco")
        output_dir = candidates[0].parent

    debug_dir = output_dir / "debug"
    if not debug_dir.exists():
        raise HTTPException(status_code=404, detail="cartella debug non trovata")

    prompts = sorted(debug_dir.glob("prompt_lezione_*.txt"))
    if not prompts:
        raise HTTPException(status_code=404, detail="nessun file prompt trovato")

    return JSONResponse([
        {"filename": p.name, "content": p.read_text(encoding="utf-8")}
        for p in prompts
    ])


@app.get("/images/{job_id}")
async def list_images(job_id: str):
    """Restituisce la lista delle immagini nella cartella images/ del job."""
    job = jobs.get(job_id)
    stored_dir = job.get("output_dir") if job else None
    if stored_dir and Path(stored_dir).exists():
        output_dir = Path(stored_dir)
    else:
        candidates = list((OUTPUT_DIR / job_id).rglob("main.tex")) if (OUTPUT_DIR / job_id).exists() else []
        if not candidates:
            raise HTTPException(status_code=404, detail="output non trovato su disco")
        output_dir = candidates[0].parent

    images_dir = output_dir / "images"
    if not images_dir.exists():
        return JSONResponse({"images": []})

    exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
    files = sorted(f.name for f in images_dir.iterdir() if f.suffix.lower() in exts)
    return JSONResponse({"images": files})


@app.get("/image/{job_id}/{filename}")
async def serve_image(job_id: str, filename: str):
    """Serve un singolo file immagine dalla cartella images/ del job."""
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Nome file non valido")

    job = jobs.get(job_id)
    stored_dir = job.get("output_dir") if job else None
    if stored_dir and Path(stored_dir).exists():
        output_dir = Path(stored_dir)
    else:
        candidates = list((OUTPUT_DIR / job_id).rglob("main.tex")) if (OUTPUT_DIR / job_id).exists() else []
        if not candidates:
            raise HTTPException(status_code=404, detail="output non trovato su disco")
        output_dir = candidates[0].parent

    img_path = output_dir / "images" / filename
    if not img_path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} non trovato")
    return FileResponse(path=img_path)


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

@app.get("/docs.htm")
async def serve_docs():
    """Serve la documentazione (docs.htm)."""
    index_path = Path(__file__).parent / "docs.htm"
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