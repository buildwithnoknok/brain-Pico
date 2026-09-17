#!/usr/bin/env python3
"""rpc_call.py — send one Device Protocol v1 message to a brain and print the reply.

  rpc_call.py <host> <op> [json-args]
  rpc_call.py 192.168.1.30 hello
  rpc_call.py noknok-be38.local status '{"since": 30}'
  rpc_call.py 192.168.1.30 settings.set '{"scope":"product","values":{"color":"#FF8040"}}'

Stdlib only; runs on the Pi or any PC. Exit 0 when the reply says ok:true.
"""
import json
import sys
import urllib.request

host, op = sys.argv[1], sys.argv[2]
args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
msg = {"id": 1, "op": op, "args": args}
req = urllib.request.Request("http://%s/rpc" % host, data=json.dumps(msg).encode(),
                             headers={"Content-Type": "application/json"})
try:
    with urllib.request.urlopen(req, timeout=25) as r:
        reply = json.loads(r.read().decode())
except Exception as e:
    print("ERROR:", repr(e))
    sys.exit(2)
print(json.dumps(reply, indent=2, sort_keys=True))
sys.exit(0 if reply.get("ok") else 1)
