"""Start only this repository's backend; no external Planora checkout is imported."""
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'backend'), str(root / 'vendor' / 'planora' / 'backend')]

if __name__ == '__main__':
    from yoyu.app import main
    main()
