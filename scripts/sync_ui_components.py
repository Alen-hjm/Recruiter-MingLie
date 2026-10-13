"""Copy the workspace's native UI components into the standalone MV3 package.

Run after editing static/{js,css}/ui-components.*; --check verifies packaging.
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    for kind in ('css', 'js'):
        source = ROOT / 'static' / kind / f'ui-components.{kind}'
        target = ROOT / 'chrome-extension' / source.name
        if args.check:
            if not target.exists() or source.read_bytes() != target.read_bytes():
                raise SystemExit(f'Out of sync: {target.name}; run scripts/sync_ui_components.py')
        else:
            target.write_bytes(source.read_bytes())
    print('UI components: in sync')


if __name__ == '__main__':
    main()
