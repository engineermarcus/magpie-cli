"""
Rich terminal UI and interactive step-by-step wizard for Magpie CLI.

Presents clean, uncluttered tables focusing strictly on:
- Index ([1], [2], [3])
- Movie / TV Show Title (Year)
- Media Type (Movie vs TV Show)
- Thumbnail URL (for previewing on the web)

Allows picking items by number index (e.g. 1, 2) or by title name (e.g. 'breaking bad', 'el camino').
"""

import sys
import re
import difflib
import webbrowser
from typing import List, Dict, Any, Optional, Tuple
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt

console = Console()

try:
    from prompt_toolkit import prompt as pt_prompt
    from prompt_toolkit.completion import WordCompleter
    PROMPT_TOOLKIT_AVAILABLE = True
except ImportError:
    PROMPT_TOOLKIT_AVAILABLE = False


def _clean_str(text: str) -> str:
    """Normalize string for fuzzy/case-insensitive comparison."""
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _fuzzy_score(query: str, target: str) -> float:
    """Calculate multi-level similarity score between query and target title."""
    q = _clean_str(query)
    t = _clean_str(target)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    if t.startswith(q):
        return 0.95
    if q in t:
        return 0.85 + (len(q) / len(t)) * 0.1

    words = t.split()
    max_chunk = 0.0
    q_words_len = len(q.split())
    for i in range(len(words)):
        for j in range(i + 1, min(i + q_words_len + 2, len(words) + 1)):
            chunk = " ".join(words[i:j])
            ratio = difflib.SequenceMatcher(None, q, chunk).ratio()
            if ratio > max_chunk:
                max_chunk = ratio

    whole_ratio = difflib.SequenceMatcher(None, q, t).ratio()
    return max(max_chunk, whole_ratio)


class _ArrowPrompt(Prompt):
    """Rich prompt that ends with '>' instead of ':'."""
    prompt_suffix = " > "


def _ask_input(prompt_text: str, completions: Optional[List[str]] = None) -> str:
    """Prompt user using prompt_toolkit tab-completion if available, or rich Prompt."""
    prompt_text = re.sub(r":\s*((?:\[/[^\]]*\])?)\s*$", r"\1", prompt_text.rstrip())
    if PROMPT_TOOLKIT_AVAILABLE and sys.stdin.isatty() and completions:
        try:
            completer = WordCompleter(completions, ignore_case=True, match_middle=True)
            plain_prompt = re.sub(r"\[.*?\]", "", prompt_text)
            return pt_prompt(plain_prompt.rstrip() + " > ", completer=completer).strip()
        except Exception:
            pass
    return _ArrowPrompt.ask(prompt_text).strip()


def print_banner(auth_info: Dict[str, Any], server_url: str):
    """Print the Magpie CLI header with device fingerprint and billing status."""
    balance = auth_info.get("balance") or {}
    total_coins = balance.get("total", 0)
    bonus_coins = balance.get("bonus", 0)
    paid_coins = balance.get("paid", 0)
    user_id = auth_info.get("user_id") or "Unregistered"
    device_id = auth_info.get("device_id") or "Unknown"

    body = (
        f"[bold cyan]Device Fingerprint :[/bold cyan] [green]{device_id}[/green]\n"
        f"[bold cyan]User Account ID    :[/bold cyan] [yellow]{user_id}[/yellow]\n"
        f"[bold cyan]Wallet Balance     :[/bold cyan] [bold gold1]🪙 {total_coins} Coins[/bold gold1] "
        f"[dim]({bonus_coins} bonus, {paid_coins} paid)[/dim]\n"
        f"[bold cyan]Client Server      :[/bold cyan] [dim]{server_url}[/dim]"
    )
    if auth_info.get("registered"):
        body += "\n[bold green]🎉 New terminal device registered! +20 welcome bonus coins credited.[/bold green]"

    console.print(Panel(
        body,
        title="[bold magenta]🐦 MAGPIE CLI - High Speed Terminal Downloader[/bold magenta]",
        border_style="magenta",
    ))


def display_search_results(results: List[Dict[str, Any]]) -> None:
    """Display TMDB search results focusing purely on Index, Title, Type, and Thumbnail URL."""
    table = Table(
        title="[bold cyan]── Search Results ──[/bold cyan]",
        header_style="bold magenta",
        show_lines=True,
        expand=True,
    )
    table.add_column("Index", style="bold yellow", width=7, min_width=7, justify="center", no_wrap=True)
    table.add_column("Title (Year)", style="bold white", ratio=3)
    table.add_column("Type", width=10, min_width=9, justify="center", no_wrap=True)
    table.add_column("Thumbnail URL (Open on Web)", style="cyan", ratio=4, overflow="fold")

    for idx, item in enumerate(results, 1):
        stype = item.get("subjectType", 1)
        type_str = "[cyan]TV Show[/cyan]" if stype == 2 else "[magenta]Movie[/magenta]"
        title = item.get("title") or item.get("name") or "Unknown"
        year = str(item.get("year") or "")
        title_display = f"{title} ({year})" if year else title

        thumb = item.get("thumbnail") or item.get("backdrop") or "No thumbnail available"

        table.add_row(
            f"[{idx}]",
            f"[bold white]{title_display}[/bold white]",
            type_str,
            thumb,
        )

    console.print(table)
    console.print("[dim]Tip: Pick by INDEX (e.g. '1', '2') or type the FILM NAME (e.g. 'el camino' or 'breaking bad')[/dim]")
    console.print("[dim]     Preview thumbnail in browser via 'open <title or #>' (e.g. 'open 1' or 'open el camino')[/dim]\n")


def match_item_by_name_or_index(raw_input: str, results: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
    """Creative matching supporting index, exact name, substring, and fuzzy matching."""
    text = raw_input.strip()
    if not text:
        return None, "empty"

    # Number index match (e.g. "1", "#1", "[1]")
    num_clean = re.sub(r"[^\d]", "", text)
    if num_clean.isdigit():
        idx = int(num_clean) - 1
        if 0 <= idx < len(results):
            return results[idx], "index"

    clean_query = _clean_str(text)

    # Exact title match (case-insensitive)
    for it in results:
        title = it.get("title") or it.get("name") or ""
        if _clean_str(title) == clean_query:
            return it, "exact_name"

    # Title starts with query
    prefix_matches = []
    for it in results:
        title = it.get("title") or it.get("name") or ""
        if _clean_str(title).startswith(clean_query):
            prefix_matches.append(it)
    if len(prefix_matches) == 1:
        return prefix_matches[0], "prefix_name"

    # Substring match
    substr_matches = []
    for it in results:
        title = it.get("title") or it.get("name") or ""
        if clean_query in _clean_str(title):
            substr_matches.append(it)
    if len(substr_matches) == 1:
        return substr_matches[0], "substring_name"
    elif len(substr_matches) > 1:
        best = min(substr_matches, key=lambda x: len(x.get("title") or x.get("name") or ""))
        return best, "substring_name"

    # Fuzzy typo-tolerant scoring (ratio >= 0.70)
    scored = []
    for it in results:
        title = it.get("title") or it.get("name") or ""
        score = _fuzzy_score(text, title)
        if score >= 0.70:
            scored.append((score, it))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1], "fuzzy_name"

    return None, "no_match"


def pick_search_result(results: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Prompt user to pick a title from search results by number index or film name."""
    completions = [str(i) for i in range(1, len(results) + 1)]
    for r in results:
        t = r.get("title") or r.get("name")
        if t:
            completions.append(t)

    while True:
        choice = _ask_input(
            f"[bold green]Select film by index or name: [/bold green]",
            completions=completions,
        ).strip()

        if not choice:
            continue

        if choice.lower() in ("q", "quit", "exit"):
            return None

        open_match = re.match(r"^(?:o|open|poster)\s+(.+)$", choice, re.IGNORECASE)
        if open_match:
            target = open_match.group(1).strip()
            item, reason = match_item_by_name_or_index(target, results)
            if item:
                thumb_url = item.get("thumbnail") or item.get("backdrop")
                title = item.get("title") or item.get("name")
                if thumb_url and thumb_url.startswith("http"):
                    console.print(f"[cyan]Opening thumbnail for '{title}' in browser: {thumb_url}[/cyan]")
                    try:
                        webbrowser.open(thumb_url)
                    except Exception as e:
                        console.print(f"[red]Could not launch browser: {e}[/red]")
                else:
                    console.print(f"[yellow]No thumbnail URL available for '{title}'.[/yellow]")
            else:
                console.print(f"[red]Could not find film matching '{target}'.[/red]")
            continue

        matched, reason = match_item_by_name_or_index(choice, results)
        if matched:
            title = matched.get("title") or matched.get("name")
            if reason != "index":
                console.print(f"[green]✔ Matched by title:[/green] [bold white]{title}[/bold white]")
            return matched

        console.print(
            f"[red]Could not find '{choice}'. Enter the index number (1-{len(results)}) or type the film name (e.g. 'breaking bad' or 'el camino').[/red]"
        )


def pick_season(seasons: List[Dict[str, Any]], title: str = "") -> Optional[int]:
    """Prompt user to select a TV season for the selected show."""
    header = f"── STEP 2: Pick Season for '{title}' ──" if title else "── STEP 2: Pick Season ──"
    table = Table(
        title=f"[bold cyan]{header}[/bold cyan]",
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("Index", style="bold yellow", width=7, min_width=7, justify="center", no_wrap=True)
    table.add_column("Season", style="bold white", width=16)
    table.add_column("Episodes", style="cyan", width=18)

    completions = []
    for idx, s in enumerate(seasons, 1):
        num = s.get("number", idx)
        order = s.get("episodeOrder")
        ep_count = f"{order} episodes" if order else "Episodes available"
        table.add_row(f"[{idx}]", f"Season {num}", ep_count)
        completions.extend([str(idx), str(num), f"Season {num}", f"S{num}"])

    console.print(table)
    console.print("[dim]Tip: Enter index (e.g. '1'), 'season 1', 's1', or 'latest'[/dim]\n")

    while True:
        choice = _ask_input(
            f"[bold green]Select Season (1-{len(seasons)}): [/bold green]",
            completions=completions,
        ).strip().lower()

        if choice in ("q", "quit", "exit"):
            return None

        if choice in ("latest", "last"):
            return seasons[-1].get("number", len(seasons))
        if choice in ("first", "pilot", "beginning"):
            return seasons[0].get("number", 1)

        num_match = re.search(r"\d+", choice)
        if num_match:
            val = int(num_match.group())
            for s in seasons:
                if s.get("number") == val:
                    return val
            if 1 <= val <= len(seasons):
                return seasons[val - 1].get("number", val)

        console.print(f"[red]Invalid season. Choose between 1 and {len(seasons)} (e.g. '1' or 's1').[/red]")


def pick_episode(episodes: List[int], season_num: int, title: str = "") -> Optional[int]:
    """Prompt user to select an episode for the selected show and season."""
    prefix = f"'{title}' - " if title else ""
    header = f"── STEP 3: {prefix}Season {season_num} - Pick Episode ──"
    table = Table(
        title=f"[bold cyan]{header}[/bold cyan]",
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("Index", style="bold yellow", width=7, min_width=7, justify="center", no_wrap=True)
    table.add_column("Episode", style="bold white", width=22)

    completions = []
    for idx, ep in enumerate(episodes, 1):
        table.add_row(f"[{idx}]", f"Episode {ep}")
        completions.extend([str(idx), str(ep), f"Episode {ep}", f"E{ep}"])

    console.print(table)
    console.print("[dim]Tip: Enter index (e.g. '1'), 'ep 1', 'pilot', or 'finale'[/dim]\n")

    while True:
        choice = _ask_input(
            f"[bold green]Select Episode (1-{len(episodes)}): [/bold green]",
            completions=completions,
        ).strip().lower()

        if choice in ("q", "quit", "exit"):
            return None

        if choice in ("first", "pilot", "start"):
            return episodes[0]
        if choice in ("last", "finale", "end"):
            return episodes[-1]

        num_match = re.search(r"\d+", choice)
        if num_match:
            val = int(num_match.group())
            if val in episodes:
                return val
            if 1 <= val <= len(episodes):
                return episodes[val - 1]

        console.print(f"[red]Invalid episode. Choose between 1 and {len(episodes)}.[/red]")


def pick_resolution(resolutions: List[Dict[str, Any]], step_num: int = 4, title: str = "") -> Optional[Dict[str, Any]]:
    """Prompt user to select a resolution for the chosen film or episode."""
    header = f"── STEP {step_num}: Pick Resolution for '{title}' ──" if title else f"── STEP {step_num}: Pick Resolution & Quality ──"
    table = Table(
        title=f"[bold cyan]{header}[/bold cyan]",
        header_style="bold magenta",
        show_lines=True,
    )
    table.add_column("Index", style="bold yellow", width=7, min_width=5, justify="center", no_wrap=True)
    table.add_column("Resolution", style="bold white", justify="center", no_wrap=True)
    table.add_column("Size", style="cyan", justify="center", no_wrap=True)

    completions = ["best", "highest", "lowest", "max"]
    for idx, r in enumerate(resolutions, 1):
        label = r.get("label") or f"{r.get('resolution')}p"
        size = r.get("size_str") or (f"{r.get('size_mb')} MB" if r.get("size_mb") else "Variable")
        is_rec = "1080" in label
        label_display = f"{label} ★" if is_rec else label
        table.add_row(f"[{idx}]", label_display, size)
        completions.extend([str(idx), label.lower(), f"{r.get('resolution')}p", str(r.get("resolution"))])

    console.print(table)
    console.print("[dim]Tip: Enter index (e.g. '1'), quality (e.g. '1080p', '4k', '720p'), or 'best'[/dim]\n")

    while True:
        choice = _ask_input(
            f"[bold green]Select Resolution (1-{len(resolutions)}): [/bold green]",
            completions=completions,
        ).strip().lower()

        if choice in ("q", "quit", "exit"):
            return None

        if choice in ("best", "highest", "max", "top"):
            return resolutions[0]
        if choice in ("lowest", "small", "min"):
            return resolutions[-1]

        clean_choice = choice.replace("p", "").strip()
        for r in resolutions:
            label_clean = (r.get("label") or "").lower().replace("p", "").strip()
            res_num = str(r.get("resolution", ""))
            if choice == label_clean or clean_choice == res_num or choice == (r.get("label") or "").lower():
                return r

        num_clean = re.sub(r"[^\d]", "", choice)
        if num_clean.isdigit():
            idx = int(num_clean) - 1
            if 0 <= idx < len(resolutions):
                return resolutions[idx]

        console.print(f"[red]Invalid resolution. Choose an index (1-{len(resolutions)}) or quality (e.g. '1080p', '4k').[/red]")
