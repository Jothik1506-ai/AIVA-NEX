import os
import sys
from pathlib import Path

# Make `import main` / `import ner` work when pytest runs from the repo root or server/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AIVA_NER_WARM", "0")  # tests load the model synchronously on first use
