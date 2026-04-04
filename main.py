import click
from pathlib import Path


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
    default=Path(__file__).parent / "config" / "user_config.yaml",
    show_default=True,
    help="Path to user config YAML.",
)
def cli(ctx, input_pdf: Path | None, config_path: Path) -> None:
    """Mail Archiver — ingests scanned PDFs and files each document."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path

    if ctx.invoked_subcommand is not None:
        return

    if input_pdf is None:
        click.echo(ctx.get_help())
        return

    _run_pipeline(input_pdf, config_path)


@cli.command()
@click.pass_context
@click.option("--host", default="127.0.0.1", help="Server host.")
@click.option("--port", default=8765, type=int, help="Server port.")
def review(ctx, host: str, port: int) -> None:
    """Launch the review queue web UI."""
    import yaml
    import uvicorn
    from src.review.server import app, configure

    config_path = ctx.obj["config_path"]
    if not config_path.exists():
        config_path = Path(__file__).parent / "config" / "default_config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    configure(config)
    click.echo(f"Review queue: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="warning")


@cli.command()
@click.pass_context
@click.option("--interval", default=60, type=int, help="Poll interval in seconds.")
def watch(ctx, interval: int) -> None:
    """Watch the scan folder and process new PDFs automatically."""
    import time
    import yaml
    from rich.console import Console

    console = Console()
    config_path = ctx.obj["config_path"]
    if not config_path.exists():
        config_path = Path(__file__).parent / "config" / "default_config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    import os
    watch_dir = Path(os.path.expanduser(
        config.get("scan_watch_folder", "~/ElectronicFiles/ToBeOrganized")
    ))
    processed_file = watch_dir / ".processed"
    processed: set[str] = set()
    if processed_file.exists():
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


def _run_pipeline(input_pdf: Path, config_path: Path) -> None:
    """Execute the full mail archiver pipeline."""
    from src.ingestion.loader import load_pdf
    from src.ocr.analyzer import analyze_pages
    from src.clustering.clusterer import cluster_pages
    from src.classification.classifier import classify_candidates
    from src.filing.confidence_gate import gate_decisions
    from src.extraction.extractor import extract_documents
    from src.summary.generator import generate_summary
    from src.ingestion.archiver import archive_original
    from src.review.queue import save_review_queue
    import yaml
    from rich.console import Console

    console = Console()

    # Load config
    if not config_path.exists():
        config_path = Path(__file__).parent / "config" / "default_config.yaml"
    with open(config_path) as f:
        config = yaml.safe_load(f)

    console.rule("[bold blue]Mail Archiver")
    console.print(f"Input:  {input_pdf}")
    console.print(f"Config: {config_path}")

    # 1. Ingestion
    console.print("\n[bold]1. Ingesting PDF...[/bold]")
    page_images = load_pdf(input_pdf)
    console.print(f"   {len(page_images)} pages loaded")

    # 2. OCR + signal extraction
    console.print("\n[bold]2. OCR + signal extraction...[/bold]")
    page_records = analyze_pages(page_images)

    # 3. Clustering
    console.print("\n[bold]3. Clustering pages into documents...[/bold]")
    candidates = cluster_pages(page_records, config)
    console.print(f"   {len(candidates)} document candidates identified")

    # 4. Classification + routing
    console.print("\n[bold]4. Classifying and routing...[/bold]")
    decisions = classify_candidates(candidates, config)

    # 5. Confidence gate
    console.print("\n[bold]5. Confidence gate...[/bold]")
    auto_file, review_queue = gate_decisions(decisions, config)
    console.print(f"   Auto-file: {len(auto_file)}  |  Review queue: {len(review_queue)}")

    # 6. Extraction + filing
    console.print("\n[bold]6. Extracting and filing documents...[/bold]")
    extract_documents(input_pdf, auto_file, config)

    # 7. Summary
    console.print("\n[bold]7. Generating summary...[/bold]")
    generate_summary(auto_file, review_queue, config)

    # 8. Archive original
    console.print("\n[bold]8. Archiving original scan...[/bold]")
    archived_path = archive_original(input_pdf, config)

    # Save review queue with the archived path so the review server can find the PDF
    if review_queue:
        queue_path = save_review_queue(review_queue, archived_path, config)
        console.rule("[bold yellow]Review Required")
        for decision in review_queue:
            console.print(
                f"  [yellow]LOW CONFIDENCE ({decision.confidence:.2f})[/yellow] "
                f"{decision.candidate.institution or 'Unknown'} — "
                f"pages {decision.candidate.pages} — {decision.notes or 'no notes'}"
            )
        console.print(
            f"\n  [bold]{len(review_queue)} item(s) need review.[/bold]\n"
            f"  Run: [cyan]python main.py review --config {config_path}[/cyan]\n"
            f"  Or open: [cyan]http://127.0.0.1:8765[/cyan]"
        )

    console.rule("[bold green]Done")


if __name__ == "__main__":
    cli()
