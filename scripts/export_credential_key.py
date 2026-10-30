"""Create private server configuration from an installed WorkBuddy client."""
import argparse
import base64
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin.credential_codec import _local_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    key_id, key = _local_key()
    payload = json.dumps({'keyId': key_id, 'key': base64.b64encode(key).decode('ascii')})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
        file.write(payload)
    print('Private credential configuration created:', args.output)


if __name__ == '__main__':
    main()
