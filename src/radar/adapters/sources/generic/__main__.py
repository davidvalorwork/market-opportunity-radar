"""Offline smoke run only. No live, cookie, credential or arbitrary CLI flags."""

import argparse
import json

from .fixtures import demo


def main():
    parser = argparse.ArgumentParser(description='Synthetic generic source smoke run (no network).')
    parser.add_argument('--fixture', action='store_true', required=True)
    parser.parse_args()
    reports = demo()
    print(json.dumps({'mode': 'synthetic_offline', 'real_sources_verified': False,
                      'reports': [r.control() for r in reports]}, separators=(',', ':')))
    return 0 if all(r.complete for r in reports) else 1


if __name__ == '__main__':
    raise SystemExit(main())
