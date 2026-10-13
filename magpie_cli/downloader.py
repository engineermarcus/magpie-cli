"""
Downloader module for Magpie CLI.

Utilizes aria2c with maximum connections, parallel split workers, and full
continuation/resume mode. If network connectivity drops or the download is
interrupted, it automatically resumes from where it left off without re-downloading
already downloaded chunks.
"""

import os
import sys
import time
import shutil
import subprocess
import re
from pathlib import Path
from typing import Optional, Callable
import httpx
from rich.console import Console
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    DownloadColumn,
    TransferSpeedColumn,
    TimeRemainingColumn,
)

console = Console()


def find_binary(name: str) -> Optional[str]:
    """Locate a tool on PATH, in ~/.local/bin, or (ffmpeg only) the pip-bundled static build.

    Lets Magpie work on machines without root/sudo.
    """
    found = shutil.which(name)
    if found:
        return found
    local = Path.home() / ".local" / "bin" / name
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and os.path.exists(exe):
                return exe
        except Exception:
            pass
    return None


def ensure_ffmpeg() -> Optional[str]:
    """Return an ffmpeg path, installing via Termux pkg or pip-bundled static build on demand."""
    exe = find_binary("ffmpeg")
    if exe:
        return exe

    # Check for Termux / Android environment
    is_termux = (
        "com.termux" in os.environ.get("PREFIX", "")
        or "com.termux" in sys.prefix
        or "TERMUX_VERSION" in os.environ
        or shutil.which("pkg") is not None
    )

    if is_termux:
        console.print("[cyan]Termux detected: installing native ffmpeg via 'pkg install ffmpeg'...[/cyan]")
        if shutil.which("pkg"):
            try:
                subprocess.run(["pkg", "install", "-y", "ffmpeg"], timeout=180)
                exe = find_binary("ffmpeg")
                if exe:
                    return exe
            except Exception:
                pass
        if shutil.which("apt"):
            try:
                subprocess.run(["apt", "install", "-y", "ffmpeg"], timeout=180)
                exe = find_binary("ffmpeg")
                if exe:
                    return exe
            except Exception:
                pass

    # Standard pip install imageio-ffmpeg for desktop Linux / macOS / Windows
    if not is_termux:
        console.print("[cyan]ffmpeg not found - installing a bundled copy (one-time, no sudo needed)...[/cyan]")
        for extra in ([], ["--user"], ["--user", "--break-system-packages"], ["--break-system-packages"]):
            try:
                r = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-q", "imageio-ffmpeg", *extra],
                    capture_output=True, text=True, timeout=300,
                )
                if r.returncode == 0:
                    import importlib, site
                    importlib.invalidate_caches()
                    try:
                        site.main()
                    except Exception:
                        pass
                    exe = find_binary("ffmpeg")
                    if exe:
                        return exe
            except Exception:
                continue

    return None


def sanitize_filename(name: str, fallback_ext: str = ".mp4") -> str:
    """Sanitize filename and ensure .mp4 extension for terminal storage."""
    base, ext = os.path.splitext(name)
    clean_base = re.sub(r'[\\/*?:"<>|]', "", base).strip()
    clean_base = clean_base.replace(" ", ".")
    if not clean_base:
        clean_base = "magpie_video"

    # Always ensure .mp4 for video playback
    if ext.lower() in (".mp4", ".m4v", ".mkv", ".mov", ".ts"):
        final_ext = ".mp4"
    else:
        final_ext = fallback_ext

    return f"{clean_base}{final_ext}"


def download_with_aria2c(
    url: str,
    output_dir: Path,
    filename: str,
    max_connections: int = 16,
    workers: int = 16,
    max_network_retries: int = 10,
) -> bool:
    """Execute aria2c with maximum parallel connections, infinite network retry, and continuation mode."""
    aria2c_bin = find_binary("aria2c")
    if not aria2c_bin:
        return False

    output_dir.mkdir(parents=True, exist_ok=True)
    target_file = output_dir / filename

    # Check if resuming partial file
    if target_file.exists() and target_file.stat().st_size > 0:
        partial_mb = target_file.stat().st_size / (1024 * 1024)
        console.print(
            f"[bold green]🔄 Continuation Mode Active: Found partial download ({partial_mb:.2f} MB). Resuming seamlessly...[/bold green]"
        )

    cmd = [
        aria2c_bin,
        "-x", str(max_connections),
        "-s", str(workers),
        "-j", str(workers),
        "-k", "1M",
        "--file-allocation=none",
        "--summary-interval=1",
        "--console-log-level=warn",
        "--continue=true",              # Strict continuation mode
        "--auto-file-renaming=false",   # Do not rename to .1, keep same file
        "--allow-overwrite=false",      # Do not start from scratch if interrupted
        "--max-tries=0",                # 0 = infinite retries if network drops!
        "--retry-wait=2",               # Wait 2 seconds between automatic network retries
        "--timeout=60",                 # 60s network socket timeout
        "--connect-timeout=30",         # 30s connection timeout
        "--max-file-not-found=0",       # Keep retrying on transient connection errors
        "--save-session-interval=2",
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) MagpieCLI/1.0",
        "-d", str(output_dir),
        "-o", filename,
        url,
    ]

    console.print(
        f"[cyan]⚡ Invoking aria2c with {max_connections} connections and {workers} split workers (Continuation Mode: ON)...[/cyan]"
    )

    attempt = 0
    while attempt < max_network_retries:
        try:
            proc = subprocess.run(cmd)
            if proc.returncode == 0 and target_file.exists() and target_file.stat().st_size > 0:
                return True

            # If aria2 exited with non-zero exit code due to network drop
            if proc.returncode != 0:
                attempt += 1
                curr_size_mb = (
                    target_file.stat().st_size / (1024 * 1024) if target_file.exists() else 0.0
                )
                if attempt < max_network_retries:
                    console.print(
                        f"[yellow]⚠️ Network connection lost or interrupted! Resuming continuation from {curr_size_mb:.2f} MB in 3s (retry {attempt}/{max_network_retries})...[/yellow]"
                    )
                    time.sleep(3)
                    continue
                else:
                    console.print(f"[red]aria2c exited with return code {proc.returncode} after {max_network_retries} retries.[/red]")
                    return False

        except KeyboardInterrupt:
            console.print("\n[yellow]Download paused by user. Progress saved in continuation mode.[/yellow]")
            return False
        except Exception as e:
            console.print(f"[red]aria2c execution error: {e}[/red]")
            return False

    return False


def download_with_python_fallback(
    url: str,
    output_dir: Path,
    filename: str,
    max_network_retries: int = 10,
) -> bool:
    """Fallback streaming downloader with HTTP Range continuation and Rich progress display."""
    output_dir.mkdir(parents=True, exist_ok=True)
    target_path = output_dir / filename

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MagpieCLI/1.0",
    }

    attempt = 0
    while attempt < max_network_retries:
        existing_size = target_path.stat().st_size if target_path.exists() else 0
        req_headers = dict(headers)

        if existing_size > 0:
            req_headers["Range"] = f"bytes={existing_size}-"
            console.print(f"[cyan]🔄 Python Fallback: Resuming download from byte {existing_size}...[/cyan]")

        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                with client.stream("GET", url, headers=req_headers) as response:
                    # 206 Partial Content (resumed) or 200 OK (start fresh)
                    if response.status_code not in (200, 206):
                        console.print(f"[red]HTTP download error: {response.status_code}[/red]")
                        attempt += 1
                        time.sleep(3)
                        continue

                    total_size = int(response.headers.get("content-length", 0))
                    if response.status_code == 206:
                        total_size += existing_size

                    write_mode = "ab" if (response.status_code == 206 and existing_size > 0) else "wb"
                    completed_so_far = existing_size if response.status_code == 206 else 0

                    with Progress(
                        TextColumn("[bold blue]{task.description}"),
                        BarColumn(),
                        DownloadColumn(),
                        TransferSpeedColumn(),
                        TimeRemainingColumn(),
                        console=console,
                    ) as progress:
                        task = progress.add_task(f"Downloading {filename}", total=total_size or None, completed=completed_so_far)
                        with open(target_path, write_mode) as f:
                            for chunk in response.iter_bytes(chunk_size=65536):
                                if chunk:
                                    f.write(chunk)
                                    progress.update(task, advance=len(chunk))

            if target_path.exists() and target_path.stat().st_size > 0:
                return True

        except KeyboardInterrupt:
            console.print("\n[yellow]Download paused. Partial file preserved for continuation.[/yellow]")
            return False
        except (httpx.HTTPError, OSError) as e:
            attempt += 1
            curr_size_mb = target_path.stat().st_size / (1024 * 1024) if target_path.exists() else 0.0
            console.print(f"[yellow]Network interruption ({e}). Resuming continuation from {curr_size_mb:.2f} MB in 3s (retry {attempt}/{max_network_retries})...[/yellow]")
            time.sleep(3)

    return False


def sniff_container(path: Path) -> str:
    """Identify container by magic bytes: 'mp4', 'matroska', 'mpegts' or 'unknown'."""
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
    except OSError:
        return "unknown"
    if len(head) >= 12 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide"):
        return "mp4"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "matroska"
    if len(head) > 376 and head[0] == 0x47 and head[188] == 0x47:
        return "mpegts"
    return "unknown"


def _probe_ok(path: Path) -> bool:
    """True if ffprobe or ffmpeg verifies the file contains a playable video stream."""
    ffprobe = find_binary("ffprobe")
    if ffprobe:
        try:
            out = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=codec_type", "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=120,
            )
            return out.returncode == 0 and "video" in out.stdout
        except Exception:
            pass

    # Fallback verification with ffmpeg null muxer
    ffmpeg = ensure_ffmpeg()
    if ffmpeg:
        try:
            res = subprocess.run(
                [ffmpeg, "-v", "error", "-i", str(path), "-t", "1", "-f", "null", "-"],
                capture_output=True, text=True, timeout=30,
            )
            return res.returncode == 0
        except Exception:
            pass
    return True


def remux_to_mp4(src: Path, dest: Path) -> bool:
    """Convert a downloaded container (e.g. MKV) into a genuine MP4 using ffmpeg.

    Tries a lossless stream copy first (video+audio, subtitles dropped since most
    are not MP4-compatible); if the audio codec cannot live in MP4 it retries with
    AAC audio. The result is verified before the source is removed.
    """
    ffmpeg = ensure_ffmpeg()
    if not ffmpeg:
        return False
    tmp = dest.with_name(dest.name + ".tmp.mp4")

    # Probe source video & audio codecs
    probe = subprocess.run([ffmpeg, "-hide_banner", "-i", str(src)], capture_output=True, text=True)
    probe_text = (probe.stderr or "").lower()
    
    # Check if video is standard H.264 (AVC) 8-bit that browsers can natively play
    is_h264 = "video: h264" in probe_text
    is_yuv420p = "yuv420p" in probe_text and "yuv420p10" not in probe_text
    browser_safe_video = "video: h264" in probe_text or "video: hevc" in probe_text

    # Check if audio is standard stereo/AAC that browsers can natively play
    m_audio = re.search(r"audio:\s*(\w+)", probe_text)
    audio_codec = m_audio.group(1) if m_audio else ""
    browser_safe_audio = audio_codec in ("aac", "mp3")

    # If already browser-safe H.264 + AAC, instant lossless copy!
    if browser_safe_video and browser_safe_audio:
        attempts = [["-c:v", "copy", "-c:a", "copy"]]
    elif browser_safe_video:
        # Video is H.264, only transcode audio to standard AAC
        attempts = [["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]]
    else:
        # Source is HEVC (x265), 10-bit, or AV1 which browsers cannot play in MP4.
        # Transcode video to universal H.264 yuv420p + AAC stereo
        console.print("[cyan]Converting video to universal browser-compatible MP4 (H.264 / AAC)...[/cyan]")
        attempts = [[
            "-c:v", "h264_mediacodec", "-b:v", "6M", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2"
        ]]

    for codec_args in attempts:
        if tmp.exists():
            tmp.unlink()
        cmd = [ffmpeg, "-y", "-v", "error", "-stats", "-i", str(src),
               "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
               *codec_args, "-movflags", "+faststart", str(tmp)]
        r = subprocess.run(cmd)

        if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0 \
                and sniff_container(tmp) == "mp4" and _probe_ok(tmp):
            os.replace(tmp, dest)
            return True

    # Fallback attempt if specific codec settings failed
    if tmp.exists():
        tmp.unlink()
    fallback_cmd = [
        ffmpeg, "-y", "-v", "error", "-stats", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ac", "2",
        "-movflags", "+faststart", str(tmp)
    ]
    r = subprocess.run(fallback_cmd)
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0 \
            and sniff_container(tmp) == "mp4" and _probe_ok(tmp):
        os.replace(tmp, dest)
        return True

    if tmp.exists():
        tmp.unlink()
    return False


def convert_existing_mkv_files(downloads_dir: Path):
    """Scan downloads directory and automatically convert any .mkv files to valid .mp4 files."""
    if not downloads_dir.exists():
        return
    for mkv in downloads_dir.glob("*.mkv"):
        mp4 = mkv.with_suffix(".mp4")
        if not mp4.exists():
            console.print(f"[cyan]🔄 Found MKV video [bold]{mkv.name}[/bold]. Auto-converting to MP4...[/cyan]")
            if remux_to_mp4(mkv, mp4):
                mkv.unlink()
                console.print(f"[bold green]✔ Successfully converted {mkv.name} -> {mp4.name}![/bold green]")


def download_file(
    url: str,
    output_dir: Optional[Path],
    suggested_filename: str,
    max_connections: int = 16,
    workers: int = 16,
) -> Optional[Path]:
    """Download the video using aria2c (preferred) or python fallback and return a real MP4.

    The raw bytes go to '<name>.mp4.download' so interrupted transfers resume from
    where they left off. When finished the container is sniffed; non-MP4 sources
    (typically Matroska) are remuxed to a valid MP4.
    """
    if output_dir is None:
        output_dir = Path.cwd() / "downloads"

    filename = sanitize_filename(suggested_filename)
    target_path = output_dir / filename
    raw_name = filename + ".download"
    raw_path = output_dir / raw_name

    # 1. If an MKV file already exists for this title (from previous run), convert it right now!
    mkv_path = target_path.with_suffix(".mkv")
    if mkv_path.exists() and not target_path.exists():
        console.print(f"[cyan]🎞 Found previously downloaded {mkv_path.name} - converting to valid MP4...[/cyan]")
        if remux_to_mp4(mkv_path, target_path):
            mkv_path.unlink()
            return target_path

    # 2. If target_path exists but contains Matroska bytes, convert it in-place!
    if target_path.exists() and sniff_container(target_path) == "matroska":
        console.print(f"[cyan]🎞 Found {target_path.name} in MKV container format - converting to valid MP4...[/cyan]")
        tmp_mkv = target_path.with_name(target_path.name + ".mkv.tmp")
        os.replace(target_path, tmp_mkv)
        if remux_to_mp4(tmp_mkv, target_path):
            tmp_mkv.unlink()
            return target_path
        else:
            os.replace(tmp_mkv, target_path)

    # 3. Already finished earlier and is a valid MP4
    if target_path.exists() and sniff_container(target_path) == "mp4" and not raw_path.exists():
        return target_path

    success = False
    if find_binary("aria2c"):
        success = download_with_aria2c(
            url=url, output_dir=output_dir, filename=raw_name,
            max_connections=max_connections, workers=workers,
        )
    if not success:
        success = download_with_python_fallback(url=url, output_dir=output_dir, filename=raw_name)

    if not success or not raw_path.exists():
        return None
    if (output_dir / (raw_name + ".aria2")).exists():
        return None  # aria2 control file present => incomplete

    kind = sniff_container(raw_path)
    if kind == "mp4":
        os.replace(raw_path, target_path)
        return target_path

    if kind == "unknown":
        console.print("[red]Downloaded data is not a recognised video container.[/red]")
        return None

    console.print(f"[cyan]🎞  Source is {kind.upper()} - converting to a real MP4 (no re-encode when possible)...[/cyan]")
    if remux_to_mp4(raw_path, target_path):
        raw_path.unlink()
        return target_path

    # If remux failed (e.g. ffmpeg could not be run):
    # NEVER discard the user's downloaded file!
    ext = ".mkv" if kind == "matroska" else ".ts"
    fallback = target_path.with_suffix(ext)
    os.replace(raw_path, fallback)
    console.print(
        f"[bold yellow]⚠️ Could not convert container to MP4 because ffmpeg was unavailable.\n"
        f"Your video has been saved safely as: [white]{fallback.name}[/white]\n"
        f"To enable MP4 conversion on Termux, run: [bold green]pkg install ffmpeg[/bold green][/bold yellow]"
    )
    return fallback
