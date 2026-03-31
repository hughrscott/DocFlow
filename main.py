import click
from pathlib import Path


@click.command()
@click.option(
    "--input", "input_pdf", required=True,
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
def main(input_pdf: Path, config_path: Path) -> None:
    """Mail Archiver — ingests a scanned PDF and files each document."""
    from src.ingestion.loader import load_pdf
    from src.ocr.analyzer import analyze_pages
    from src.clustering.clusterer import cluster_pages
    from src.classification.classifier import classify_candidates
    from src.filing.confidence_gate import gate_decisions
    from src.extraction.extractor import extract_documents
    from src.summary.generator import generate_summary
    from src.ingestion.archiver import archive_original
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
    archive_original(input_pdf, config)

    # Print review queue to console
    if review_queue:
        console.rule("[bold yellow]Review Required")
        for decision in review_queue:
            console.print(
                f"  [yellow]LOW CONFIDENCE ({decision.confidence:.2f})[/yellow] "
                f"{decision.candidate.institution or 'Unknown'} — "
                f"pages {decision.candidate.pages} — {decision.notes or 'no notes'}"
            )

    console.rule("[bold green]Done")


if __name__ == "__main__":
    main()
