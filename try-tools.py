"""Live tool-calling check against the running bridge (real Google). Run: python try-tools.py [model]"""
import json, os, re, sys, urllib.request, urllib.error

BASE = f"http://127.0.0.1:{os.environ.get('OAI_BRIDGE_PORT', '8765')}/v1"
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_key():
    if os.environ.get("OAI_BRIDGE_KEY"):
        return os.environ["OAI_BRIDGE_KEY"]
    path = os.environ.get("AISTUDIO_CONFIG_FILE") or os.path.expanduser("~/aistudio-api/config.yaml")
    try:
        cfg = open(path, encoding="utf-8").read()
    except OSError:
        return ""
    try:
        import yaml
        keys = (yaml.safe_load(cfg) or {}).get("api_keys") or []
    except Exception:
        # no PyYAML: entries are either "- name: ..\n  key: sk-.." or a bare "- sk-.."
        block = re.split(r"^\S", cfg.split("api_keys:", 1)[-1], maxsplit=1, flags=re.M)[0]
        keys = re.findall(r"\bkey:\s*['\"]?([^\s'\"]+)", block) or re.findall(r"^\s*-\s*['\"]?([^\s'\":]+)['\"]?\s*$", block, re.M)
    if isinstance(keys, str):
        keys = re.split(r"[,\n]", keys)
    keys = [(k.get("key") if isinstance(k, dict) else k) for k in keys]
    keys = [str(k).strip() for k in keys if k and str(k).strip()]
    return keys[0] if keys else ""


KEY = api_key()


def call(path, body=None):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body else None,
                               headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
    try:
        with DIRECT.open(r, timeout=300) as x:
            return x.status, x.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except OSError as e:
        return 0, str(e)


def read_stream(raw):
    text, calls, fin = "", [], None
    for line in raw.splitlines():
        if not line.startswith("data: {"):
            continue
        ch = json.loads(line[6:])["choices"][0]
        text += ch["delta"].get("content") or ""
        calls += ch["delta"].get("tool_calls") or []
        fin = ch.get("finish_reason") or fin
    return text, calls, fin


def pick_model():
    if len(sys.argv) > 1:
        return sys.argv[1]
    code, out = call("/models")
    ids = [m["id"] for m in json.loads(out).get("data", [])] if code == 200 else []
    ids = [i for i in ids if not i.endswith("]") and not re.search(r"image|tts|live|audio|embed", i)]
    for want in ("argon", "flash"):
        for i in ids:
            if want in i:
                return i
    return "gemini-2.5-flash"


TOOLS = [{"type": "function", "function": {
    "name": "save_row", "description": "Save one row into the character table.",
    "parameters": {"type": "object", "required": ["name", "mood"],
                   "properties": {"name": {"type": "string"}, "mood": {"type": "string"}}}}}]
MSGS = [{"role": "system", "content": "You keep a character table. Record every new fact with save_row before answering."},
        {"role": "user", "content": "Alice is happy today. Save it, then tell me in one short sentence that it is saved."}]
bad = 0


def report(n, what, ok, detail):
    global bad
    bad += not ok
    print(f"{n} {what}：{'通过' if ok else '没过'}  {detail}")


if call("/models")[0] == 401:
    sys.exit("钥匙不对：没能从 ~/aistudio-api/config.yaml 读到反代的钥匙。在命令最前面加 OAI_BRIDGE_KEY=你的钥匙 再跑一次")
model = pick_model()
print(f"模型：{model}（想换别的：在命令最后加上模型名）")

code, out = call("/chat/completions", {"model": model, "messages": MSGS, "tools": TOOLS})
msg = json.loads(out)["choices"][0]["message"] if code == 200 else {}
calls = msg.get("tool_calls") or []
report(1, "插件发表，谷歌交表", bool(calls), json.dumps(calls[0]["function"], ensure_ascii=False) if calls else f"HTTP {code} {out[:300]}")

if calls:
    again = MSGS + [{"role": "assistant", "content": msg.get("content"), "tool_calls": calls}] + [
        {"role": "tool", "tool_call_id": c["id"], "content": "{\"ok\": true}"} for c in calls]
    code, out = call("/chat/completions", {"model": model, "messages": again, "tools": TOOLS})
    m2 = json.loads(out)["choices"][0]["message"] if code == 200 else {}
    report(2, "把表的结果回传，谷歌接着说", code == 200 and bool(m2.get("content") or m2.get("tool_calls")),
           (m2.get("content") or "又交了一张表")[:80] if code == 200 else f"HTTP {code} {out[:300]}")

code, out = call("/chat/completions", {"model": model, "messages": MSGS, "tools": TOOLS, "stream": True})
text, calls, fin = read_stream(out) if code == 200 else ("", [], None)
report(3, "流式下交表", bool(calls) and fin == "tool_calls", f"结束标记 {fin}" if code == 200 else f"HTTP {code} {out[:300]}")

guard = model + "[防截断]"
code, out = call("/chat/completions", {"model": guard, "messages": MSGS, "tools": TOOLS, "stream": True})
text, calls, fin = read_stream(out) if code == 200 else ("", [], None)
report(4, "开防截断时交表", bool(calls), f"交表 {len(calls)} 张，同时带正文 {len(text)} 字" if code == 200 else f"HTTP {code} {out[:300]}")

if calls:
    again = MSGS + [{"role": "assistant", "content": text or None, "tool_calls": [
        {"id": c["id"], "type": "function", "function": c["function"]} for c in calls]}] + [
        {"role": "tool", "tool_call_id": c["id"], "content": "{\"ok\": true}"} for c in calls]
    code, out = call("/chat/completions", {"model": guard, "messages": again, "tools": TOOLS, "stream": True})
    text, calls, fin = read_stream(out) if code == 200 else ("", [], None)
    pieces = sum('"content": "' in l and '"content": ""' not in l for l in out.splitlines())
    report(5, "开防截断，填完表后出正文", bool(text) and not text.startswith("[error]"),
           f"{text[:60]}（分 {pieces} 块到，1 块就是防截断生效）" if code == 200 else f"HTTP {code} {out[:300]}")

print("全部通过" if not bad else f"有 {bad} 项没过，把上面整段发给作者")
