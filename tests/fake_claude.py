#!/usr/bin/env python3
"""Tut so, als wäre es `claude -p --output-format json`. Protokolliert Aufruf + Umgebung."""
import json, os, sys, uuid

prompt = sys.stdin.read()
log = os.environ["FAKE_CLAUDE_LOG"]
with open(log, "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt,
                        "api_key_present": "ANTHROPIC_API_KEY" in os.environ}) + "\n")
if "--resume" in sys.argv and sys.argv[sys.argv.index("--resume") + 1] == "expired":
    print(json.dumps({"type": "result", "is_error": True, "result": "No conversation found with session ID: expired"}))
    sys.exit(1)
sid = sys.argv[sys.argv.index("--resume") + 1] if "--resume" in sys.argv else str(uuid.uuid4())
print(json.dumps({"type": "result", "subtype": "success", "is_error": False,
                  "result": "Antwort auf: " + prompt.splitlines()[-1], "session_id": sid}))
