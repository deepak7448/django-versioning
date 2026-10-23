#!/usr/bin/env python
import os
import sys
from pathlib import Path

if __name__ == "__main__":
    # Use the package from this repository checkout, even if it isn't installed
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "example_project.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
