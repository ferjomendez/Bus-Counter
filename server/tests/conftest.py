import sys
from pathlib import Path

# Permite importar el paquete 'app' desde server/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
