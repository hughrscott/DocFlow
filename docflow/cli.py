"""DocFlow CLI — the main entry point for the docflow command."""
import os
import signal
import subprocess
import sys
import shutil
import time
from pathlib import Path

import click


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------

def _resolve_config(explicit: Path | None = None) -> Path:
    """Find the config file using a priority chain."""
    # 1. Explicit --config flag
    if explicit and explicit.exists():
        return explicit

    # 2. DOCFLOW_CONFIG env var
    env = os.environ.get("DOCFLOW_CONFIG")
    if env and Path(env).exists():
        return Path(env)

    # 3. ~/.docflow/config.yaml
    home_config = Path.home() / ".docflow" / "config.yaml"
    if home_config.exists():
        return home_config

    # 4. ./config/user_config.yaml (dev mode)
    local = Path("config/user_config.yaml")
    if local.exists():
        return local

    # 5. Default config (shipped with package)
    pkg_default = Path(__file__).parent.parent / "config" / "default_config.yaml"
    if pkg_default.exists():
        return pkg_default

    # Last resort
    return Path("config/default_config.yaml")


def _docflow_dir() -> Path:
    """Return ~/.docflow/, creating if needed."""
    d = Path.home() / ".docflow"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_config(config_path: Path) -> dict:
    """Load and return the YAML config."""
    import yaml
    if not config_path.exists():
        click.echo(f"Config not found: {config_path}", err=True)
        raise SystemExit(1)
    with open(config_path) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group(invoke_without_command=True)
@click.pass_context
@click.option(
    "--input", "input_pdf",
    type=click.Path(exists=True, path_type=Path),
    help="Path to the scanned PDF to process.",
)
@click.option(
    "--config", "config_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Path to user config YAML.",
)
def cli(ctx, input_pdf: Path | None, config_path: Path | None) -> None:
    """DocFlow — The Digital Archivist. Ingests scanned PDFs and files each document."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = _resolve_config(config_path)

    if ctx.invoked_subcommand is not None:
        return

    if input_pdf is None:
        click.echo(ctx.get_help())
        return

    _run_pipeline(input_pdf, ctx.obj["config_path"])


# ---------------------------------------------------------------------------
# start / stop / status / logs
# ---------------------------------------------------------------------------

_DEFAULT_PORT = int(os.environ.get("DOCFLOW_PORT", "8765"))


@cli.command()
@click.pass_context
@click.option("--port", default=_DEFAULT_PORT, type=int, help="Server port.")
def start(ctx, port: int) -> None:
    """Start DocFlow as a background service."""
    pidfile = _docflow_dir() / "docflow.pid"
    logdir = _docflow_dir() / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    # Check if already running
    if pidfile.exists():
        pid = int(pidfile.read_text().strip())
        try:
            os.kill(pid, 0)
            click.echo(f"DocFlow is already running (PID {pid})")
            click.echo(f"  http://localhost:{port}")
            return
        except OSError:
            pidfile.unlink()

    config_path = ctx.obj["config_path"]
    click.echo("Starting DocFlow...")

    proc = subprocess.Popen(
        [sys.executable, "-m", "docflow.cli", "--config", str(config_path), "ui", "--port", str(port)],
        stdout=open(logdir / "docflow.log", "a"),
        stderr=open(logdir / "docflow.err", "a"),
        start_new_session=True,
    )
    pidfile.write_text(str(proc.pid))

    time.sleep(2)
    if proc.poll() is None:
        click.echo(f"DocFlow is running (PID {proc.pid})")
        click.echo(f"  http://localhost:{port}")
    else:
        pidfile.unlink(missing_ok=True)
        click.echo("Failed to start. Check logs:")
        click.echo(f"  {logdir / 'docflow.err'}")
        raise SystemExit(1)


@cli.command()
def stop() -> None:
    """Stop the DocFlow background service."""
    pidfile = _docflow_dir() / "docflow.pid"
    if not pidfile.exists():
        click.echo("DocFlow is not running")
        return

    pid = int(pidfile.read_text().strip())
    click.echo(f"Stopping DocFlow (PID {pid})...")

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass

    for _ in range(20):
        try:
            os.kill(pid, 0)
            time.sleep(0.5)
        except OSError:
            break

    pidfile.unlink(missing_ok=True)
    click.echo("Stopped")


@cli.command()
def status() -> None:
    """Check if DocFlow is running."""
    pidfile = _docflow_dir() / "docflow.pid"
    if pidfile.exists():
        pid = int(pidfile.read_text().strip())
        try:
            os.kill(pid, 0)
            click.echo(f"DocFlow is running (PID {pid})")
            click.echo(f"  http://localhost:{_DEFAULT_PORT}")
            return
        except OSError:
            pidfile.unlink()
    click.echo("DocFlow is not running")


@cli.command()
def logs() -> None:
    """Tail the DocFlow log files."""
    logdir = _docflow_dir() / "logs"
    logfile = logdir / "docflow.log"
    errfile = logdir / "docflow.err"
    files = [str(f) for f in (logfile, errfile) if f.exists()]
    if not files:
        click.echo("No log files found")
        return
    os.execvp("tail", ["tail", "-f"] + files)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
def check(ctx) -> None:
    """Verify system dependencies are installed and working."""
    from rich.console import Console
    console = Console()
    console.rule("[bold blue]DocFlow System Check")
    all_ok = True

    # Python version
    v = sys.version_info
    if v >= (3, 11):
        console.print(f"  [green]PASS[/green]  Python {v.major}.{v.minor}.{v.micro}")
    else:
        console.print(f"  [red]FAIL[/red]  Python {v.major}.{v.minor} (need 3.11+)")
        all_ok = False

    # Tesseract
    if shutil.which("tesseract"):
        console.print("  [green]PASS[/green]  Tesseract OCR")
    else:
        console.print("  [red]FAIL[/red]  Tesseract not found — brew install tesseract")
        all_ok = False

    # Poppler (pdftoppm)
    if shutil.which("pdftoppm"):
        console.print("  [green]PASS[/green]  Poppler (pdftoppm)")
    else:
        console.print("  [red]FAIL[/red]  Poppler not found — brew install poppler")
        all_ok = False

    # Config
    config_path = ctx.obj["config_path"]
    if config_path.exists():
        console.print(f"  [green]PASS[/green]  Config: {config_path}")
    else:
        console.print(f"  [red]FAIL[/red]  Config not found: {config_path}")
        console.print("         Run: docflow init")
        all_ok = False

    # LLM connectivity
    if config_path.exists():
        config = _load_config(config_path)
        provider = config.get("llm_provider", "openrouter")
        api_key = config.get("llm_api_key")
        if not api_key:
            from dotenv import load_dotenv
            load_dotenv()
            api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if provider == "ollama":
            console.print("  [green]PASS[/green]  LLM provider: Ollama (local)")
        elif api_key:
            console.print(f"  [green]PASS[/green]  LLM provider: {provider} (API key set)")
        else:
            console.print(f"  [red]FAIL[/red]  No API key for {provider} — set in .env or settings UI")
            all_ok = False

    console.print()
    if all_ok:
        console.print("[green bold]All checks passed[/green bold]")
    else:
        console.print("[red bold]Some checks failed — see above[/red bold]")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
def init() -> None:
    """Set up DocFlow for first use."""
    from rich.console import Console
    console = Console()
    console.rule("[bold blue]DocFlow Setup")

    docflow_dir = _docflow_dir()
    config_dest = docflow_dir / "config.yaml"

    if config_dest.exists():
        console.print(f"Config already exists: {config_dest}")
        if not click.confirm("Overwrite?", default=False):
            return

    # Copy default config
    default = Path(__file__).parent.parent / "config" / "default_config.yaml"
    if not default.exists():
        console.print("[red]Default config not found. Is DocFlow installed correctly?[/red]")
        return

    shutil.copy2(str(default), str(config_dest))
    console.print(f"  Config created: {config_dest}")

    # Archive root
    archive = click.prompt("Archive root directory", default="~/DocFlowExample/archive")
    archive_path = Path(os.path.expanduser(archive))
    archive_path.mkdir(parents=True, exist_ok=True)
    console.print(f"  Archive root: {archive_path}")

    # LLM provider
    provider = click.prompt(
        "LLM provider",
        type=click.Choice(["ollama", "openrouter", "openai", "groq"]),
        default="ollama",
    )

    # API key
    api_key = ""
    if provider != "ollama":
        api_key = click.prompt(f"API key for {provider}", hide_input=True, default="")

    # Update config
    import yaml
    with open(config_dest) as f:
        config = yaml.safe_load(f)
    config["archive_root"] = archive
    config["llm_provider"] = provider
    if api_key:
        config["llm_api_key"] = api_key
    with open(config_dest, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    console.print(f"\n[green]Setup complete![/green]")
    console.print(f"  Config: {config_dest}")
    console.print(f"  Start:  docflow start")
    console.print(f"  Check:  docflow check")


# ---------------------------------------------------------------------------
# install-service / uninstall-service (macOS LaunchAgent)
# ---------------------------------------------------------------------------

_PLIST_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.docflow.ui</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>-m</string>
        <string>docflow.cli</string>
        <string>--config</string>
        <string>{config}</string>
        <string>ui</string>
        <string>--port</string>
        <string>{port}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{workdir}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>StandardOutPath</key>
    <string>{logdir}/docflow.log</string>
    <key>StandardErrorPath</key>
    <string>{logdir}/docflow.err</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""


@cli.command("install-service")
@click.pass_context
@click.option("--port", default=_DEFAULT_PORT, type=int)
def install_service(ctx, port: int) -> None:
    """Install DocFlow as a macOS LaunchAgent (auto-starts on login)."""
    if sys.platform != "darwin":
        click.echo("LaunchAgent is macOS only. Use systemd on Linux.")
        return

    config_path = ctx.obj["config_path"]
    logdir = _docflow_dir() / "logs"
    logdir.mkdir(parents=True, exist_ok=True)

    plist_content = _PLIST_TEMPLATE.format(
        python=sys.executable,
        config=str(config_path.resolve()),
        port=str(port),
        workdir=str(Path(__file__).parent.parent.resolve()),
        logdir=str(logdir),
    )

    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.docflow.ui.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)

    # Unload existing
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/com.docflow.ui"], capture_output=True)
    time.sleep(1)

    plist_path.write_text(plist_content)
    click.echo(f"Installed: {plist_path}")

    subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(plist_path)], capture_output=True)
    subprocess.run(["launchctl", "kickstart", "-k", "-p", f"gui/{uid}/com.docflow.ui"], capture_output=True)

    time.sleep(3)
    click.echo(f"DocFlow LaunchAgent installed. http://localhost:{port}")


@cli.command("uninstall-service")
def uninstall_service() -> None:
    """Remove the DocFlow macOS LaunchAgent."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.docflow.ui.plist"
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/com.docflow.ui"], capture_output=True)
    if plist_path.exists():
        plist_path.unlink()
    click.echo("LaunchAgent removed")


# ---------------------------------------------------------------------------
# ui (foreground server)
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
@click.option("--host", default="127.0.0.1", help="Server host.")
@click.option("--port", default=_DEFAULT_PORT, type=int, help="Server port.")
def ui(ctx, host: str, port: int) -> None:
    """Launch the full DocFlow web UI (foreground)."""
    import uvicorn
    from docflow.web.app import app, configure

    config_path = ctx.obj["config_path"]
    config = _load_config(config_path)
    configure(config, config_path=config_path)
    click.echo(f"\n  DocFlow UI: http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port, log_level="info")


@cli.command()
@click.pass_context
@click.option("--host", default="127.0.0.1", help="Server host.")
@click.option("--port", default=_DEFAULT_PORT, type=int, help="Server port.")
def review(ctx, host: str, port: int) -> None:
    """Launch the review queue web UI (legacy)."""
    import uvicorn
    from docflow.review.server import app, configure

    config_path = ctx.obj["config_path"]
    config = _load_config(config_path)
    configure(config)
    click.echo(f"Review queue: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


# ---------------------------------------------------------------------------
# learn / scan-archive / validate / batch / watch
# ---------------------------------------------------------------------------

@cli.command("learn")
@click.pass_context
def learn(ctx) -> None:
    """Review corrections and show suggested new filing rules."""
    from rich.console import Console
    from docflow.config.learner import suggest_rules

    console = Console()
    config = _load_config(ctx.obj["config_path"])
    suggestions = suggest_rules(config)

    if not suggestions:
        console.print("[dim]No rule suggestions yet. Corrections from the review queue "
                      "will appear here once patterns emerge.[/dim]")
        return

    console.rule("[bold blue]Suggested Filing Rules")
    for s in suggestions:
        console.print(f"\n  [bold]{s['id']}[/bold] (based on {s['based_on']})")
        console.print(f"    match: {s['match']}")
        console.print(f"    file_to: {s['file_to']}")
        console.print(f"    template: {s['filename_template']}")

    console.print(f"\n[dim]To add these rules, copy them into your config YAML.[/dim]")


@cli.command("scan-archive")
@click.pass_context
@click.argument("archive_path", type=click.Path(exists=True, file_okay=False, path_type=Path))
def scan_archive(ctx, archive_path: Path) -> None:
    """Scan an existing archive and infer filing rules."""
    import yaml
    from rich.console import Console
    from docflow.config.scanner import scan_existing_archive

    console = Console()
    console.rule("[bold blue]Archive Scanner")
    console.print(f"Scanning: {archive_path}\n")

    result = scan_existing_archive(archive_path)
    stats = result["stats"]

    console.print(f"  PDFs found: {stats['total_pdfs']}")
    console.print(f"  Directories: {stats['total_directories']}")
    console.print(f"  Institutions detected: {', '.join(stats['institutions_found']) or 'none'}")
    console.print(f"  Rules inferred: {stats['rules_inferred']}")

    if result["inferred_rules"]:
        console.print("\n[bold]Inferred Filing Rules:[/bold]")
        for rule in result["inferred_rules"]:
            conf = rule.pop("confidence", 0)
            count = rule.pop("sample_count", 0)
            console.print(
                f"\n  [bold]{rule['id']}[/bold] "
                f"({count} files, {conf:.0%} confidence)"
            )
            console.print(f"    match: {rule['match']}")
            console.print(f"    file_to: {rule['file_to']}")
            console.print(f"    template: {rule['filename_template']}")

    if result["inferred_entities"]:
        console.print("\n[bold]Inferred Entities:[/bold]")
        for entity in result["inferred_entities"]:
            console.print(f"  - {entity['name']} (type: {entity['type']}, dir: {entity['directory']})")

    config_path = ctx.obj["config_path"]
    draft_path = config_path.parent / "inferred_config.yaml"
    draft = {
        "archive_root": str(archive_path),
        "filing_rules": result["inferred_rules"],
        "entities": result["inferred_entities"],
    }
    with open(draft_path, "w") as f:
        yaml.dump(draft, f, default_flow_style=False, sort_keys=False)
    console.print(f"\n[green]Draft config written to: {draft_path}[/green]")


@cli.command("validate")
@click.pass_context
def validate(ctx) -> None:
    """Validate the configuration file."""
    from rich.console import Console
    from docflow.config.validator import validate_config

    console = Console()
    config_path = ctx.obj["config_path"]
    console.rule("[bold blue]Config Validation")
    console.print(f"Config: {config_path}\n")

    results = validate_config(config_path)
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])

    for r in results:
        icon = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        console.print(f"  {icon}  {r['message']}")

    console.print()
    if failed:
        console.print(f"[red bold]{failed} check(s) failed[/red bold]")
    else:
        console.print(f"[green bold]All {passed} checks passed[/green bold]")


@cli.command("batch")
@click.pass_context
@click.argument("input_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def batch(ctx, input_dir: Path) -> None:
    """Process all PDFs in a directory."""
    from rich.console import Console
    console = Console()
    config_path = ctx.obj["config_path"]
    pdfs = sorted(input_dir.glob("*.pdf"))

    if not pdfs:
        console.print(f"[yellow]No PDFs found in {input_dir}[/yellow]")
        return

    console.rule(f"[bold blue]Batch processing: {len(pdfs)} PDFs")
    succeeded, failed = 0, 0

    for i, pdf in enumerate(pdfs, 1):
        console.print(f"\n[bold]({i}/{len(pdfs)}) {pdf.name}[/bold]")
        try:
            _run_pipeline(pdf, config_path)
            succeeded += 1
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")
            failed += 1

    console.rule("[bold green]Batch complete")
    console.print(f"  Processed: {succeeded}  |  Failed: {failed}")


@cli.command()
@click.pass_context
@click.option("--interval", default=60, type=int, help="Poll interval in seconds.")
def watch(ctx, interval: int) -> None:
    """Watch the scan folder and process new PDFs automatically."""
    import time
    from rich.console import Console

    console = Console()
    config_path = ctx.obj["config_path"]
    config = _load_config(config_path)

    watch_dir = Path(os.path.expanduser(
        config.get("scan_watch_folder", "~/DocFlowExample/inbox")
    ))
    docflow_state_dir = _docflow_dir()
    processed_file = docflow_state_dir / "processed.txt"
    processed: set[str] = set()

    old_processed = watch_dir / ".processed"
    if old_processed.exists():
        processed = set(old_processed.read_text().splitlines())
        processed_file.write_text("\n".join(sorted(processed)))
        old_processed.unlink()
    elif processed_file.exists():
        processed = set(processed_file.read_text().splitlines())

    console.print(f"[bold]Watching:[/bold] {watch_dir}")
    console.print(f"[bold]Interval:[/bold] {interval}s")
    console.print("Press Ctrl+C to stop.\n")

    try:
        while True:
            if watch_dir.exists():
                for pdf in sorted(watch_dir.glob("*.pdf")):
                    if pdf.name in processed:
                        continue
                    console.rule(f"[bold blue]New scan: {pdf.name}")
                    try:
                        _run_pipeline(pdf, config_path)
                        processed.add(pdf.name)
                        processed_file.write_text("\n".join(sorted(processed)))
                    except Exception as exc:
                        console.print(f"[red]Error processing {pdf.name}: {exc}[/red]")
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[bold]Watch stopped.[/bold]")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _run_pipeline(input_pdf: Path, config_path: Path) -> None:
    """Execute the full mail archiver pipeline."""
    from docflow.ingestion.loader import load_pdf
    from docflow.ocr.analyzer import analyze_pages
    from docflow.clustering.clusterer import cluster_pages
    from docflow.classification.classifier import classify_candidates
    from docflow.filing.confidence_gate import gate_decisions
    from docflow.extraction.extractor import extract_documents
    from docflow.summary.generator import generate_summary
    from docflow.ingestion.archiver import archive_original
    from docflow.review.queue import save_review_queue
    from rich.console import Console

    console = Console()
    config = _load_config(config_path)

    console.rule("[bold blue]Mail Archiver")
    console.print(f"Input:  {input_pdf}")
    console.print(f"Config: {config_path}")

    # 0. Build dedup index on first run
    from docflow.filing.dedup import is_empty, build_initial_index
    if is_empty():
        console.print("\n[bold]Building duplicate index (first run)...[/bold]")
        count = build_initial_index(config.get("archive_root", "~/DocFlowExample/archive"))
        console.print(f"   Indexed {count} existing files")

    # 1. Ingestion
    console.print("\n[bold]1. Ingesting PDF...[/bold]")
    page_images = load_pdf(input_pdf)
    console.print(f"   {len(page_images)} pages loaded")

    # 2. OCR
    console.print("\n[bold]2. OCR + signal extraction...[/bold]")
    page_records = analyze_pages(page_images)

    # One gateway per job: placeholders are consistent within this scan only.
    from docflow.llm.gateway import PSEUDONYMIZATION_WARNING, CloudPromptGateway
    gateway = CloudPromptGateway(config)
    if gateway.local_only:
        console.print("[dim]Local-only mode: no model calls will be made.[/dim]")
    else:
        console.print(f"[dim]{PSEUDONYMIZATION_WARNING}[/dim]")

    # 3. Clustering
    console.print("\n[bold]3. Clustering pages into documents...[/bold]")
    candidates = cluster_pages(page_records, config, gateway=gateway)
    console.print(f"   {len(candidates)} document candidates identified")

    # 4. Classification
    console.print("\n[bold]4. Classifying and routing...[/bold]")
    decisions = classify_candidates(candidates, config, gateway=gateway)

    # 5. Confidence gate
    console.print("\n[bold]5. Confidence gate...[/bold]")
    auto_file, review_queue = gate_decisions(decisions, config)
    console.print(f"   Auto-file: {len(auto_file)}  |  Review queue: {len(review_queue)}")

    # 6. Extraction
    console.print("\n[bold]6. Extracting and filing documents...[/bold]")
    extract_documents(input_pdf, auto_file, config)

    # 7. Summary
    console.print("\n[bold]7. Generating summary...[/bold]")
    generate_summary(auto_file, review_queue, config)

    # 8. Archive original
    console.print("\n[bold]8. Archiving original scan...[/bold]")
    archived_path = archive_original(input_pdf, config)

    if review_queue:
        save_review_queue(review_queue, archived_path, config)
        console.rule("[bold yellow]Review Required")
        for decision in review_queue:
            console.print(
                f"  [yellow]LOW CONFIDENCE ({decision.confidence:.2f})[/yellow] "
                f"{decision.candidate.institution or 'Unknown'} — "
                f"pages {decision.candidate.pages} — {decision.notes or 'no notes'}"
            )
        console.print(
            f"\n  [bold]{len(review_queue)} item(s) need review.[/bold]\n"
            f"  Run: [cyan]docflow ui[/cyan]\n"
            f"  Or open: [cyan]http://127.0.0.1:{_DEFAULT_PORT}[/cyan]"
        )

    console.rule("[bold green]Done")


# ---------------------------------------------------------------------------
# Allow running as module: python -m docflow.cli
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
