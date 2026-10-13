"""
Main Entrypoint for Magpie Terminal CLI.

Usage:
    magpie --download "breaking bad"
    magpie "inception"
    magpie --balance
    magpie --device
"""

import os
import re
import sys
import argparse
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt
from rich.panel import Panel

from .device import get_device_summary
from .api import MagpieClient, InsufficientCoinsError, MagpieAPIError
from .downloader import (
    download_file,
    sanitize_filename,
    convert_existing_mkv_files,
)
from .ui import (
    print_banner,
    display_search_results,
    pick_search_result,
    pick_season,
    pick_episode,
    pick_resolution,
    _ArrowPrompt,
)

console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="magpie",
        description="Magpie CLI — High speed movie and TV show terminal downloader powered by aria2c.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-d", "--download",
        dest="download_query",
        metavar="QUERY",
        help="Search query to download (e.g. 'breaking bad' or 'interstellar')",
    )
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Optional positional search query if --download is omitted",
    )
    parser.add_argument(
        "-b", "--balance",
        action="store_true",
        help="Check current coin balance and wallet status",
    )
    parser.add_argument(
        "--device",
        action="store_true",
        help="View terminal hardware fingerprint details",
    )
    parser.add_argument(
        "-o", "--dir",
        dest="output_dir",
        default="downloads",
        help="Directory to save downloads (default: ./downloads)",
    )
    parser.add_argument(
        "--connections",
        type=int,
        default=16,
        help="Maximum parallel connections for aria2c (default: 16)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Maximum split workers for aria2c (default: 16)",
    )
    parser.add_argument(
        "--server",
        default=None,
        help="Custom Magpie Client Server URL",
    )
    return parser


def run_cli():
    parser = build_parser()
    args = parser.parse_args()

    # Initialize Client & Device Fingerprint
    try:
        client = MagpieClient(base_url=args.server)
        auth = client.ensure_authenticated()
    except Exception as e:
        console.print(f"[bold red]❌ Failed to initialize Magpie client:[/bold red] {e}")
        sys.exit(1)

    auth["device_id"] = client.device_id
    print_banner(auth, client.base_url)

    # Handle --device info flag
    if args.device:
        summary = get_device_summary()
        console.print(Panel(
            "\n".join([f"[cyan]{k:18}[/cyan]: {v}" for k, v in summary.items()]),
            title="[bold green]Terminal Device Hardware Fingerprint[/bold green]",
        ))
        return

    # Handle --balance flag
    if args.balance:
        bal = client.get_balance()
        console.print(
            f"[bold gold1]🪙 Current Balance:[/bold gold1] {bal.get('total', 0)} Coins "
            f"([green]{bal.get('bonus', 0)} bonus[/green], [cyan]{bal.get('paid', 0)} paid[/cyan], [magenta]{bal.get('pass', 0)} pass[/magenta])"
        )
        return

    # Scan downloads folder and auto-convert any existing MKV files to MP4
    output_dir = Path(args.output_dir).resolve()
    convert_existing_mkv_files(output_dir)

    # Determine query
    query = args.download_query or args.query
    if not query:
        query = _ArrowPrompt.ask("[bold green]Enter movie or TV show to download[/bold green]").strip()
        if not query:
            console.print("[yellow]No query provided. Exiting.[/yellow]")
            return

    # Step 1: Search
    console.print(f"\n[bold cyan]🔍 Searching Magpie for:[/bold cyan] [bold white]{query}[/bold white]...")
    try:
        results = client.search(query)
    except MagpieAPIError as e:
        console.print(f"[bold red]Search error:[/bold red] {e}")
        sys.exit(1)

    if not results:
        console.print(f"[yellow]No results found for '{query}'. Try another search query.[/yellow]")
        sys.exit(0)

    display_search_results(results)

    # Step 2: Pick item
    selected = pick_search_result(results)
    if not selected:
        console.print("[yellow]Canceled by user.[/yellow]")
        sys.exit(0)

    title = selected.get("title") or selected.get("name") or "Unknown"
    subject_id = str(selected.get("subjectId") or selected.get("id"))
    subject_type = int(selected.get("subjectType", 1))
    is_tv = subject_type == 2

    console.print(f"\n[green]Selected:[/green] [bold white]{title}[/bold white] ({'TV Show' if is_tv else 'Movie'})")

    season_num = 1
    episode_num = 1

    # Step 3: If TV show, pick Season and Episode
    if is_tv:
        # Step 2a: Seasons
        console.print("[dim]Fetching seasons...[/dim]")
        try:
            seasons = client.get_seasons(subject_id, title)
        except MagpieAPIError as e:
            console.print(f"[bold red]Failed to load seasons:[/bold red] {e}")
            sys.exit(1)

        if not seasons:
            console.print("[yellow]No seasons found for this TV show.[/yellow]")
            sys.exit(1)

        picked_s = pick_season(seasons, title=title)
        if picked_s is None:
            console.print("[yellow]Canceled by user.[/yellow]")
            sys.exit(0)
        season_num = picked_s

        # Step 2b: Episodes
        console.print(f"[dim]Fetching episodes for Season {season_num}...[/dim]")
        try:
            episodes = client.get_episodes(subject_id, title, season_num)
        except MagpieAPIError as e:
            console.print(f"[bold red]Failed to load episodes:[/bold red] {e}")
            sys.exit(1)

        if not episodes:
            console.print(f"[yellow]No episodes found for Season {season_num}.[/yellow]")
            sys.exit(1)

        picked_e = pick_episode(episodes, season_num, title=title)
        if picked_e is None:
            console.print("[yellow]Canceled by user.[/yellow]")
            sys.exit(0)
        episode_num = picked_e

    # Step 3 (Movie) or Step 4 (TV): Pick Resolution
    step_num = 4 if is_tv else 2
    console.print(f"[dim]Fetching stream resolutions...[/dim]")
    try:
        resolutions = client.get_resolutions(
            subject_id,
            subject_type,
            season=season_num,
            episode=episode_num,
        )
    except MagpieAPIError as e:
        console.print(f"[bold red]Failed to fetch resolutions:[/bold red] {e}")
        sys.exit(1)

    if not resolutions:
        console.print("[yellow]No stream resolutions currently available for this title.[/yellow]")
        sys.exit(1)

    res_choice = pick_resolution(resolutions, step_num=step_num, title=title)
    if not res_choice:
        console.print("[yellow]Canceled by user.[/yellow]")
        sys.exit(0)

    target_res = res_choice.get("resolution", 1080)
    info_hash = res_choice.get("infoHash", "")
    file_idx = res_choice.get("fileIdx", 0)

    # Step: Billing & Stream Resolution
    console.print(f"\n[bold cyan]── Billing & Authorization ──[/bold cyan]")
    current_bal = client.get_balance()
    console.print(f"[bold]Current Wallet Balance:[/bold] 🪙 {current_bal.get('total', 0)} Coins")
    console.print(f"[dim]Standard download cost: 2 coins (valid for 72 hours of unlimited re-downloads)[/dim]")

    # Continuation: an unfinished partial file for this exact title means we are resuming
    # a download that was already paid for - the server will not charge again.
    _safe = re.sub(r"[^\w-]", "", title.replace(" ", "-")) or "video"
    _name = f"{_safe}-s{season_num:02d}e{episode_num:02d}.mp4" if is_tv else f"{_safe}.mp4"
    _partial = Path(args.output_dir).resolve() / (sanitize_filename(_name) + ".download")
    is_resume = _partial.exists() and _partial.stat().st_size > 0
    if is_resume:
        console.print("[bold green]🔄 Unfinished download found - continuing. You will NOT be charged again.[/bold green]")

    console.print("[dim]Applying billing rules via client server...[/dim]")
    try:
        resolved = client.resolve_download(
            tmdb_id=subject_id,
            subject_type=subject_type,
            season=season_num,
            episode=episode_num,
            title=title,
            info_hash=info_hash,
            file_idx=file_idx,
            resolution=target_res,
            resume=is_resume,
        )
    except InsufficientCoinsError as e:
        console.print(Panel(
            f"[bold red]❌ {e}[/bold red]\n\n"
            f"You need 2 coins to download this title.\n"
            f"Current Balance: {e.balance.get('total', 0)} coins.\n"
            f"Top up via web client or mobile app to continue.",
            title="[bold red]Insufficient Coins[/bold red]",
            border_style="red",
        ))
        sys.exit(1)
    except MagpieAPIError as e:
        console.print(f"[bold red]Download resolution failed:[/bold red] {e}")
        sys.exit(1)

    new_bal = client.get_balance()
    console.print(f"[bold green]✔ Authorization & Billing Successful![/bold green] Remaining Balance: 🪙 [bold gold1]{new_bal.get('total', 0)} Coins[/bold gold1]")

    download_url = resolved["download_url"]
    suggested_filename = resolved.get("filename") or (
        f"{title}-s{season_num:02d}e{episode_num:02d}.mp4" if is_tv else f"{title}.mp4"
    )

    # Step: Download with aria2c
    output_dir = Path(args.output_dir).resolve()
    final_filename = sanitize_filename(suggested_filename)
    dest_path = output_dir / final_filename

    step_title = "STEP 5" if is_tv else "STEP 3"
    console.print(Panel(
        f"[bold white]Title    :[/bold white] {title}\n"
        f"[bold white]Target   :[/bold white] {'Season ' + str(season_num) + ' Episode ' + str(episode_num) if is_tv else 'Feature Movie'}\n"
        f"[bold white]Quality  :[/bold white] {res_choice.get('label') or str(target_res) + 'p'} MP4\n"
        f"[bold white]Save To  :[/bold white] [green]{dest_path}[/green]",
        title=f"[bold magenta]── {step_title}: High-Speed MP4 Download ──[/bold magenta]",
        border_style="cyan",
    ))

    result_path = download_file(
        url=download_url,
        output_dir=output_dir,
        suggested_filename=final_filename,
        max_connections=args.connections,
        workers=args.workers,
    )

    if result_path and result_path.exists():
        size_bytes = result_path.stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        console.print(Panel(
            f"[bold green]🎉 Video downloaded successfully![/bold green]\n\n"
            f"[bold white]Location:[/bold white] [cyan]{result_path}[/cyan]\n"
            f"[bold white]Size    :[/bold white] {size_mb:.2f} MB\n"
            f"[bold white]Format  :[/bold white] MP4 Video",
            title="[bold green]Download Complete[/bold green]",
            border_style="green",
        ))
    else:
        console.print("[bold red]❌ Download did not complete or target file was empty.[/bold red]")
        sys.exit(1)


def main():
    try:
        run_cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Operation aborted by user.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    main()
