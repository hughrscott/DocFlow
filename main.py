"""Thin shim for backward compatibility. Use `docflow` command instead."""
from docflow.cli import cli

if __name__ == "__main__":
    cli()
