"""Vstupní skript pro PyInstaller (absolutní import, ne relativní)."""

import sys

from speechscope_app.main import main

sys.exit(main())
