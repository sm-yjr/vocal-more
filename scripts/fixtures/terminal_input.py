# SPDX-License-Identifier: GPL-3.0-only
"""Owned terminal receiver. Never interprets or executes pasted text."""
import json
import os
from pathlib import Path
import sys
import select
import tty

tty.setraw(sys.stdin.fileno())
os.write(sys.stdout.fileno(), b'\x1b[>31u\x1b[?2004hOwned terminal input fixture\r\n')
output = Path(sys.argv[1]) / 'terminal-input.json'
data = bytearray()
while True:
    part = os.read(sys.stdin.fileno(), 4096)
    if not part:
        break
    data.extend(part)
    if b'\x1b[201~' in data:
        while select.select([sys.stdin], [], [], 0.3)[0]:
            data.extend(os.read(sys.stdin.fileno(), 4096))
        text = bytes(data).replace(b'\x1b[200~', b'').replace(b'\x1b[201~', b'').decode('utf-8')
        output.write_text(json.dumps({'received': text}, ensure_ascii=False))
