"""Permite ``python -m src.cli`` como alternativa al script ``nous``."""

from src.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
