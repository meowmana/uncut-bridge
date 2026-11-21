"""Self-check: fake Gemini upstream + bridge, asserts the translation. Run: python check.py"""
import json, os, subprocess, sys, threading, time, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

seen = {}

class Fake(BaseHTTPRequestHandler):
    def _s(self, code, o):
        b = json.dumps(o).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if self.headers.get("x-goog-api-key") != "k1":
            return self._s(401, {"error": {"message": "bad key"}})
        self._s(200, {"models": [{"name": "models/gemini-x"}]})
    def do_POST(self):
        seen["path"] = self.path
        seen["req"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if seen["req"].get("tools") and ":streamGenerateContent" in self.path:
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for c in ({"candidates": [{"content": {"parts": [{"text": "plan", "thought": True}]}}]},
                      {"candidates": [{"content": {"parts": [{"functionCall": {"name": "reply", "args": {"text": "long reply"}}}]},
                                       "finishReason": "STOP"}]}):
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode()); self.wfile.flush()
            return
        if ":streamGenerateContent" in self.path:
            assert "alt=sse" in self.path
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for c in ({"candidates": [{"content": {"parts": [{"text": "hmm", "thought": True}]}}]},
                      {"candidates": [{"content": {"parts": [{"text": "h"}]}}]},
                      {"candidates": [{"content": {"parts": [{"text": "i"}]}, "finishReason": "STOP"}]}):
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode()); self.wfile.flush()
            return
        self._s(200, {"candidates": [{"content": {"parts": [{"text": "hmm", "thought": True}, {"text": "hi"}]},
                                      "finishReason": "MAX_TOKENS"}]})
    def log_message(self, *a): pass

up = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=up.serve_forever, daemon=True).start()
env = dict(os.environ, OAI_BRIDGE_UPSTREAM=f"http://127.0.0.1:{up.server_port}", OAI_BRIDGE_PORT="18765",
           http_proxy="http://127.0.0.1:9", HTTP_PROXY="http://127.0.0.1:9", no_proxy="", NO_PROXY="")
br = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "oai-bridge.py")],
                      env=env, stdout=subprocess.DEVNULL)
B = "http://127.0.0.1:18765/v1"

def req(path, body=None, key="k1"):
    r = urllib.request.Request(B + path, data=json.dumps(body).encode() if body else None,
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

try:
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:18765/health"); break
        except OSError:
            time.sleep(0.1)
    code, out = req("/models")
    assert code == 200 and [m["id"] for m in json.loads(out)["data"]] == ["gemini-x", "gemini-x[防截断]"], out
    assert req("/models", key="nope")[0] == 401
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "a"},
            {"role": "user", "content": [{"type": "text", "text": "b"}]}, {"role": "assistant", "content": "c"}]
    code, out = req("/chat/completions", {"model": "gemini-x", "messages": msgs, "max_tokens": 9})
    o = json.loads(out)
    assert code == 200 and o["choices"][0]["message"]["content"] == "hi", out
    assert o["choices"][0]["finish_reason"] == "length"
    g = seen["req"]
    assert seen["path"] == "/v1beta/models/gemini-x:generateContent"
    assert g["systemInstruction"] == {"parts": [{"text": "S"}]}
    assert [c["role"] for c in g["contents"]] == ["user", "model", "user"] and len(g["contents"][0]["parts"]) == 2
    assert all(s["threshold"] == "BLOCK_NONE" for s in g["safetySettings"]) and len(g["safetySettings"]) == 4
    assert g["generationConfig"]["maxOutputTokens"] == 9
    code, out = req("/chat/completions", {"model": "gemini-x", "messages": msgs, "stream": True})
    assert code == 200 and seen["path"].startswith("/v1beta/models/gemini-x:streamGenerateContent"), out
    assert '"content": "h"' in out and '"content": "i"' in out and '"reasoning_content": "hmm"' in out, out
    assert '"content": "hmm"' not in out and "tools" not in seen["req"], out
    o = json.loads(req("/chat/completions", {"model": "gemini-x", "messages": msgs})[1])
    assert o["choices"][0]["message"]["reasoning_content"] == "hmm", o
    code, out = req("/chat/completions", {"model": "gemini-x[防截断]", "messages": msgs, "stream": True,
                                          "reasoning_effort": "high"})
    g = seen["req"]
    assert seen["path"].startswith("/v1beta/models/gemini-x:streamGenerateContent"), seen["path"]
    assert g["toolConfig"]["functionCallingConfig"]["mode"] == "ANY" and g["tools"][0]["functionDeclarations"][0]["name"] == "reply"
    assert "reply(text=" in g["systemInstruction"]["parts"][-1]["text"]
    assert g["generationConfig"]["thinkingConfig"] == {"includeThoughts": True, "thinkingLevel": "HIGH"}
    assert '"reasoning_content": "plan"' in out and '"content": "long reply"' in out, out
    assert '"finish_reason": "stop"' in out and out.rstrip().endswith("data: [DONE]"), out
    tools = [{"type": "function", "function": {"name": "fill", "description": "d", "parameters": {
        "type": "object", "additionalProperties": False, "properties": {"row": {"type": ["string", "null"]}}}}}]
    tmsgs = msgs[:2] + [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "fill", "arguments": "{\"row\": \"x\"}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{\"ok\": true}"}]
    code, out = req("/chat/completions", {"model": "gemini-x[防截断]", "messages": tmsgs, "tools": tools, "stream": True})
    g = seen["req"]
    d = g["tools"][0]["functionDeclarations"][0]
    assert d["name"] == "fill" and "additionalProperties" not in d["parameters"], g["tools"]
    assert d["parameters"]["properties"]["row"] == {"type": "string", "nullable": True}, d
    assert g["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO" and "reply(text=" not in json.dumps(g.get("systemInstruction"))
    parts = [p for c in g["contents"] for p in c["parts"]]
    assert any(p.get("functionCall", {}).get("args") == {"row": "x"} for p in parts), parts
    assert any(p.get("functionResponse", {}).get("name") == "fill" for p in parts), parts
    assert '"tool_calls": [{"id": "call_' in out and '"name": "reply"' in out and '"finish_reason": "tool_calls"' in out, out
    r = urllib.request.Request(B + "/models", method="OPTIONS",
                               headers={"Origin": "http://127.0.0.1:8000", "Access-Control-Request-Method": "GET",
                                        "Access-Control-Request-Headers": "authorization,content-type"})
    with urllib.request.urlopen(r) as x:
        assert "authorization" in x.headers.get("Access-Control-Allow-Headers", "").lower()
    print("all ok")
finally:
    br.terminate()
