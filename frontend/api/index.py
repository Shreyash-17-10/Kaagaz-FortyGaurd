import sys
import os

here = os.path.dirname(os.path.abspath(__file__))
src_dir = os.path.join(os.path.dirname(here), "src")
backend_dir = os.path.join(src_dir, "backend")

if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from backend.main import app
