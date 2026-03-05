#!/usr/bin/env python3
import subprocess
import sys
import re
import os

# === PATHS ===
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(SCRIPT_DIR, "downloads")
LEZIONI_DIR = os.path.join(DOWNLOADS_DIR, "lezioni")
REGISTRAZIONI_DIR = os.path.join(DOWNLOADS_DIR, "registrazioni")
CONTATORI_FILE = os.path.join(DOWNLOADS_DIR, "contatori.txt")


def init_dirs():
    """Crea le cartelle necessarie se non esistono"""
    os.makedirs(LEZIONI_DIR, exist_ok=True)
    os.makedirs(REGISTRAZIONI_DIR, exist_ok=True)


def leggi_contatori():
    """Legge i contatori dal file txt"""
    contatori = {"lezioni": 0, "registrazioni": 0}
    if os.path.exists(CONTATORI_FILE):
        with open(CONTATORI_FILE, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("lezioni="):
                    try:
                        contatori["lezioni"] = int(line.split("=")[1])
                    except ValueError:
                        pass
                elif line.startswith("registrazioni="):
                    try:
                        contatori["registrazioni"] = int(line.split("=")[1])
                    except ValueError:
                        pass
    return contatori


def salva_contatori(contatori):
    """Salva i contatori nel file txt"""
    with open(CONTATORI_FILE, "w") as f:
        f.write(f"lezioni={contatori['lezioni']}\n")
        f.write(f"registrazioni={contatori['registrazioni']}\n")


def clean_url(url):
    """Rimuove &altTranscode=1 e tutto quello che segue"""
    match = re.search(r'&altTranscode=1', url)
    if match:
        return url[:match.start()]
    return url


def download_standard(url, output_file):
    """Download normale con ffmpeg - copia stream, audio mono"""
    cmd = [
        'ffmpeg',
        '-i', url,
        '-c:v', 'copy',
        '-ac', '1',
        '-c:a', 'aac',
        output_file
    ]
    return subprocess.run(cmd)


def download_vaapi(url, output_file):
    """Download con accelerazione hardware VAAPI e re-encoding"""
    cmd = [
        'ffmpeg',
        '-hwaccel', 'vaapi',
        '-hwaccel_device', '/dev/dri/renderD128',
        '-hwaccel_output_format', 'vaapi',
        '-i', url,
        '-c:v', 'hevc_vaapi',
        '-qp', '28',
        '-ac', '1',
        '-c:a', 'aac',
        '-b:a', '48k',
        output_file
    ]
    return subprocess.run(cmd)


def estrai_mp3(video_file, mp3_file):
    """Estrae audio mono mp3 dal video"""
    cmd = [
        'ffmpeg',
        '-i', video_file,
        '-vn',
        '-ac', '1',
        '-codec:a', 'libmp3lame',
        '-qscale:a', '4',
        mp3_file
    ]
    result = subprocess.run(cmd)
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
    if url != url_pulito:
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
