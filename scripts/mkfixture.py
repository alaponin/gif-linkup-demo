#!/usr/bin/env python3
"""Parse a raw `curl -ksi` dump (status line + headers + blank line + body)
into a clean fixture JSON: {status, headers, body, captured, context}.
Used by scripts/capture-xroad-fixtures.sh (testing-strategy plan Task 6) to
record real X-Road admin-API/proxy responses instead of hand-written
approximations."""
import json
import re
import sys
from datetime import datetime, timezone

raw_path, out_path, context = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(raw_path).read()

# curl -i with a redirect/retry can print more than one status block; take
# the LAST one (the final response), matching what a real client sees.
blocks = re.split(r"(?=^HTTP/\d)", text, flags=re.M)
block = blocks[-1]
head, _, body = block.partition("\n\n")
if not body.strip() and "\r\n\r\n" in block:
    head, _, body = block.partition("\r\n\r\n")

lines = head.strip().splitlines()
status = int(lines[0].split()[1])
headers = {}
for line in lines[1:]:
    if ":" in line:
        k, v = line.split(":", 1)
        headers[k.strip()] = v.strip()

body = body.strip()
try:
    body_json = json.loads(body) if body else None
except json.JSONDecodeError:
    body_json = body

fixture = {
    "status": status,
    "headers": headers,
    "body": body_json,
    "captured": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "context": context,
}
with open(out_path, "w") as f:
    json.dump(fixture, f, indent=2)
    f.write("\n")
print(f"wrote {out_path}: status={status} body={body_json}")
