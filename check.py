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
        if "fast-503" in self.path:
            time.sleep(0.3)
            return self._s(503, {"error": {"message": "busy"}})
        if "slow-503" in self.path:
            time.sleep(1.8)
            return self._s(503, {"error": {"message": "busy"}})
        if "slow-head" in self.path:
            time.sleep(3.2)
        if "slow-body" in self.path:
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            self.wfile.flush(); time.sleep(3.2)
            self.wfile.write(b'data: {"candidates": [{"content": {"parts": [{"text": "late"}]}, "finishReason": "STOP"}]}\n\n')
            return
        names = [d["name"] for t in seen["req"].get("tools", []) for d in t["functionDeclarations"]]
        called = []
        if "fill" in names:
            called += [{"thoughtSignature": "SIG1"}, {"functionCall": {"name": "fill", "args": {"row": "y"}, "id": "g1"}}]
        if "reply" in names:
            called += [{"functionCall": {"name": "reply", "args": {"text": "long reply"}}}]
        if called and ":streamGenerateContent" in self.path:
            self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
            for c in ({"candidates": [{"content": {"parts": [{"text": "plan", "thought": True}]}}]},
                      {"candidates": [{"content": {"parts": called}, "finishReason": "STOP"}]}):
                self.wfile.write(f"data: {json.dumps(c)}\n\n".encode()); self.wfile.flush()
            return
        if called:
            return self._s(200, {"candidates": [{"content": {"parts": called}, "finishReason": "STOP"}]})
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
        "type": "object", "additionalProperties": False, "properties": {"row": {"type": ["string", "null"]}}}}},
        {"type": "function", "function": {"name": "ping", "parameters": {"type": "object", "properties": {}}}}]
    rule = lambda g: "reply(text=" in json.dumps(g.get("systemInstruction"))
    decls = lambda g: g["tools"][0]["functionDeclarations"]
    # plain model + plugin tools: tools go through untouched, the call comes back as tool_calls
    code, out = req("/chat/completions", {"model": "gemini-x", "messages": msgs[:2], "tools": tools, "stream": True})
    g = seen["req"]
    assert [d["name"] for d in decls(g)] == ["fill", "ping"] and not rule(g), g
    assert "additionalProperties" not in decls(g)[0]["parameters"] and "parameters" not in decls(g)[1], decls(g)
    assert decls(g)[0]["parameters"]["properties"]["row"] == {"type": "string", "nullable": True}, decls(g)
    assert g["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"
    assert '"name": "fill"' in out and '"name": "reply"' not in out and '"finish_reason": "tool_calls"' in out, out
    cid = out.split('"tool_calls": [{"id": "')[1].split('"')[0]
    # replaying that call hands Gemini its own signature and id back; a call we never issued gets the bypass
    for tc_id, want_sig, want_id in ((cid, "SIG1", "g1"), ("c1", "skip_thought_signature_validator", None)):
        tmsgs = msgs[:2] + [{"role": "assistant", "content": None, "tool_calls": [
            {"id": tc_id, "type": "function", "function": {"name": "fill", "arguments": "{\"row\": \"x\"}"}}]},
            {"role": "tool", "tool_call_id": tc_id, "content": "{\"ok\": true}"}]
        o = json.loads(req("/chat/completions", {"model": "gemini-x", "messages": tmsgs, "tools": tools})[1])
        assert o["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "fill", o
        assert o["choices"][0]["finish_reason"] == "tool_calls" and o["choices"][0]["message"]["content"] is None, o
        parts = [p for c in seen["req"]["contents"] for p in c["parts"]]
        fc = next(p for p in parts if "functionCall" in p)
        fr = next(p["functionResponse"] for p in parts if "functionResponse" in p)
        assert fc["functionCall"]["args"] == {"row": "x"} and fc["thoughtSignature"] == want_sig, fc
        assert fc["functionCall"].get("id") == want_id and fr.get("id") == want_id and fr["name"] == "fill", (fc, fr)
    # [防截断] + plugin tools: reply() rides along, prose comes back as content and the plugin call as tool_calls
    code, out = req("/chat/completions", {"model": "gemini-x[防截断]", "messages": msgs[:2], "tools": tools, "stream": True})
    g = seen["req"]
    assert [d["name"] for d in decls(g)] == ["fill", "ping", "reply"] and rule(g), g
    assert "allowedFunctionNames" not in g["toolConfig"]["functionCallingConfig"], g["toolConfig"]
    assert '"content": "long reply"' in out and '"name": "fill"' in out and '"name": "reply"' not in out, out
    assert '"finish_reason": "tool_calls"' in out and out.rstrip().endswith("data: [DONE]"), out
    # a forced plugin tool is left alone; tool_choice none leaves reply() by itself
    req("/chat/completions", {"model": "gemini-x[防截断]", "messages": msgs[:2], "tools": tools, "tool_choice": "required"})
    g = seen["req"]
    assert [d["name"] for d in decls(g)] == ["fill", "ping"] and not rule(g), g
    assert g["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
    o = json.loads(req("/chat/completions", {"model": "gemini-x[防截断]", "messages": msgs[:2], "tools": tools, "tool_choice": "none"})[1])
    g = seen["req"]
    assert [d["name"] for d in decls(g)] == ["reply"] and rule(g), g
    assert o["choices"][0]["message"]["content"] == "long reply" and "tool_calls" not in o["choices"][0]["message"], o
    # while Gemini is silent an empty delta goes out every second, before and after its headers arrive
    beat = '"delta": {"content": ""}'
    for m, want in (("slow-head", '"content": "h"'), ("slow-body", '"content": "late"')):
        t = time.time()
        r = urllib.request.Request(B + "/chat/completions", headers={"Authorization": "Bearer k1"},
                                   data=json.dumps({"model": m, "messages": msgs[:2], "stream": True}).encode())
        with urllib.request.urlopen(r) as x:
            x.readline()
            first = time.time() - t
            out = x.read().decode()
        assert first < 2 and out.count(beat) >= 2 and out.index(beat) < out.index(want), (m, first, out)
        assert out.rstrip().endswith("data: [DONE]"), out
    assert beat not in req("/chat/completions", {"model": "gemini-x", "messages": msgs, "stream": True})[1]
    # an upstream error keeps its status when it is quick, and becomes an error event once the stream has started
    code, out = req("/chat/completions", {"model": "fast-503", "messages": msgs[:2], "stream": True})
    assert code == 503 and json.loads(out)["error"]["message"] == "busy", (code, out)
    code, out = req("/chat/completions", {"model": "slow-503", "messages": msgs[:2], "stream": True})
    assert code == 200 and '"error": {"message": "busy", "type": "upstream_error", "code": 503}' in out, (code, out)
    assert out.rstrip().endswith("data: [DONE]"), out
    r = urllib.request.Request(B + "/models", method="OPTIONS",
                               headers={"Origin": "http://127.0.0.1:8000", "Access-Control-Request-Method": "GET",
                                        "Access-Control-Request-Headers": "authorization,content-type"})
    with urllib.request.urlopen(r) as x:
        assert "authorization" in x.headers.get("Access-Control-Allow-Headers", "").lower()
    print("all ok")
finally:
    br.terminate()
