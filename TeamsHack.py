#!/usr/bin/env python3
import json
import subprocess
import sys
import re
import os

# === PATHS ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(SCRIPT_DIR, "downloads")
LEZIONI_DIR = os.path.join(DOWNLOADS_DIR, "lezioni")
REGISTRAZIONI_DIR = os.path.join(DOWNLOADS_DIR, "registrazioni")
CONTATORI_FILE     = os.path.join(DOWNLOADS_DIR, "contatori.json")   # nuovo formato
_CONTATORI_FILE_TXT = os.path.join(DOWNLOADS_DIR, "contatori.txt")   # legacy


def init_dirs():
    """Crea le cartelle necessarie se non esistono"""
    os.makedirs(LEZIONI_DIR, exist_ok=True)
    os.makedirs(REGISTRAZIONI_DIR, exist_ok=True)


def leggi_contatori():
    """
    Legge i contatori da contatori.json.
    Se il JSON non esiste ma esiste il vecchio .txt, lo migra automaticamente.
    """
    default = {"lezioni": 0, "registrazioni": 0}

    # Formato nuovo — JSON
    if os.path.exists(CONTATORI_FILE):
        try:
            with open(CONTATORI_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            contatori = {
                "lezioni":        int(data.get("lezioni", 0)),
                "registrazioni":  int(data.get("registrazioni", 0)),
            }
            return contatori
        except Exception:
            pass  # file corrotto → ricostruisce dai valori default

    # Migrazione dal vecchio formato .txt
    if os.path.exists(_CONTATORI_FILE_TXT):
        contatori = dict(default)
        try:
            with open(_CONTATORI_FILE_TXT, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        k, _, v = line.partition("=")
                        if k in contatori:
                            try:
                                contatori[k] = int(v)
                            except ValueError:
                                pass
            salva_contatori(contatori)   # salva subito in JSON
            print(f"✓ Contatori migrati da contatori.txt → contatori.json")
        except Exception:
            pass
        return contatori

    return dict(default)


def salva_contatori(contatori):
    """Salva i contatori in contatori.json (atomico: scrive su tmp poi rinomina)."""
    tmp = CONTATORI_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(contatori, f, indent=2)
        os.replace(tmp, CONTATORI_FILE)
    except Exception as e:
        print(f"✗ Errore salvataggio contatori: {e}")


def clean_url(url: str) -> str:
    """Rimuove solo il parametro altTranscode=1, preservando gli altri parametri."""
    if not url or not url.strip():
        return ""
    url = url.strip()
    # Rimuove &altTranscode=1 (o ?altTranscode=1) senza tagliare i parametri successivi
    url = re.sub(r'[&?]altTranscode=1(?=&|$)', '', url)
    # Ripulisce eventuali && residui o ? rimasto senza valore
    url = re.sub(r'&&+', '&', url)
    url = re.sub(r'\?&', '?', url)
    return url


def is_valid_teams_url(url: str) -> bool:
    """
    Verifica che l'URL sia un videomanifest Teams valido.
    Accetta URL da teams.microsoft.com, *.sharepoint.com, *.svc.ms.
    """
    if not url or not url.startswith("http"):
        return False
    _domains = re.compile(
        r'https?://[^/]*(teams\.microsoft\.com|sharepoint\.com|svc\.ms|'
        r'microsoftstream\.com|api\.teams\.skype\.com)',
        re.IGNORECASE,
    )
    return bool(_domains.match(url))


def _run_ffmpeg(cmd, timeout=None):
    """Esegue un comando ffmpeg, gestisce FileNotFoundError se non installato."""
    try:
        return subprocess.run(cmd, timeout=timeout)
    except FileNotFoundError:
        print("✗ ffmpeg non trovato — installa ffmpeg e riprova.")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("✗ Timeout ffmpeg superato.")
        sys.exit(1)


def download_standard(url, output_file):
    """Download normale con ffmpeg - copia stream, audio mono"""
    cmd = ['ffmpeg', '-i', url, '-c:v', 'copy', '-ac', '1', '-c:a', 'aac', output_file]
    return _run_ffmpeg(cmd, timeout=7200)


def download_vaapi(url, output_file):
    """Download con accelerazione hardware VAAPI e re-encoding"""
    cmd = [
        'ffmpeg',
        '-hwaccel', 'vaapi',
        '-hwaccel_device', '/dev/dri/renderD128',
        '-hwaccel_output_format', 'vaapi',
        '-i', url,
        '-c:v', 'hevc_vaapi', '-qp', '28',
        '-ac', '1', '-c:a', 'aac', '-b:a', '48k',
        output_file
    ]
    return _run_ffmpeg(cmd, timeout=7200)


def estrai_mp3(video_file, mp3_file):
    """Estrae audio mono mp3 dal video"""
    cmd = ['ffmpeg', '-i', video_file, '-vn', '-ac', '1',
           '-codec:a', 'libmp3lame', '-qscale:a', '4', mp3_file]
    result = _run_ffmpeg(cmd, timeout=3600)
    return result.returncode == 0


def main():
    init_dirs()
    contatori = leggi_contatori()

    print("=== Downloader Video con ffmpeg ===\n")

    # Scelta tipo
    print("Tipo di contenuto:")
    print("1. Lezione")
    print("2. Registrazione")
    print("0. Esci\n")
    tipo_scelta = input("Scegli tipo (0-2): ").strip()

    if tipo_scelta == '0':
        print("Uscita.")
        sys.exit(0)

    if tipo_scelta not in ['1', '2']:
        print("Scelta non valida!")
        sys.exit(1)

    tipo = "lezioni" if tipo_scelta == '1' else "registrazioni"
    tipo_label = "Lezione" if tipo_scelta == '1' else "Registrazione"
    output_dir = LEZIONI_DIR if tipo_scelta == '1' else REGISTRAZIONI_DIR

    # Numero prossimo
    prossimo_num = contatori[tipo] + 1
    nome_base = f"{tipo_label}_{prossimo_num:02d}"
    print(f"\nSarà salvato come: {nome_base}")

    # Modalità download
    print("\nModalità download:")
    print("1. Standard (copia stream)")
    print("2. VAAPI (re-encode HEVC, HW acceleration)\n")
    modalita = input("Scegli modalità (1-2): ").strip()

    if modalita not in ['1', '2']:
        print("Scelta non valida!")
        sys.exit(1)

    # URL
    print("\nIncolla il videomanifest URL:")
    url = input().strip()

    if not url:
        print("URL non valido!")
        sys.exit(1)

    url_pulito = clean_url(url)
    if not url_pulito:
        print("✗ URL vuoto dopo pulizia — riprova.")
        sys.exit(1)

    if not is_valid_teams_url(url_pulito):
        print(f"⚠ Attenzione: l'URL non sembra un videomanifest Teams.")
        print(f"  Dominio atteso: teams.microsoft.com / sharepoint.com / svc.ms")
        conferma = input("Continuare comunque? (s/N) ").strip().lower()
        if conferma != 's':
            sys.exit(1)
    elif url != url_pulito:
        print("✓ URL pulito (rimosso altTranscode)")

    # File paths
    video_file = os.path.join(output_dir, f"{nome_base}.mkv")
    mp3_file = os.path.join(output_dir, f"{nome_base}.mp3")

    print(f"\nAvvio download in modalità {'standard' if modalita == '1' else 'VAAPI'}...")
    print(f"Output video: {video_file}\n")

    # Download
    if modalita == '1':
        result = download_standard(url_pulito, video_file)
    else:
        result = download_vaapi(url_pulito, video_file)

    if result.returncode != 0:
        print(f"\n✗ Errore durante il download (exit code: {result.returncode})")
        sys.exit(result.returncode)

    print(f"\n✓ Download completato: {video_file}")

    # Estrai MP3
    print(f"\nEstrazione audio MP3 mono...")
    if estrai_mp3(video_file, mp3_file):
        print(f"✓ Audio estratto: {mp3_file}")
        # Incrementa contatore solo se tutto è andato a buon fine
        contatori[tipo] = prossimo_num
        salva_contatori(contatori)
        print(f"\n✓ Contatore aggiornato: {tipo_label} #{prossimo_num:02d}")
    else:
        print(f"✗ Errore estrazione audio (il video è salvato ma il contatore non viene incrementato)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInterrotto dall'utente.")
        sys.exit(130)
