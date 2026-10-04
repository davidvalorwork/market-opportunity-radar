"""Administrative session export to a NEW private directory; no network."""
import argparse
import json

from radar.adapters.local.session_snapshot import snapshot_session, SnapshotError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--destination',required=True)
    parser.add_argument('--authorize-session-copy',action='store_true')
    args = parser.parse_args(argv)
    try:
        result = snapshot_session(source=args.source,destination=args.destination,
            authorize_copy=args.authorize_session_copy)
        print(json.dumps(result))
        return 0
    except SnapshotError as error:
        print(json.dumps({'copied':False,'code':str(error)}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
