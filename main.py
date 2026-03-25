from __future__ import annotations

from pathlib import Path

from autohhkek.services.env_loader import load_project_dotenv

load_project_dotenv(Path(__file__).resolve().parent)

from autohhkek.app.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
