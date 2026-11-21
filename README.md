# uncut-bridge

给 [aistudio-api](https://github.com/DeconstructedCube/aistudio-api) 套一层 OpenAI 格式的翻译，让只认 OpenAI 接口的酒馆插件（比如写摘要的）也能用上它。

- 只用 Python 自带的库，一个文件，不用装任何东西
- 只在本机 `127.0.0.1:8765` 听，转给本机 `127.0.0.1:8080` 的 aistudio-api
- 钥匙原样转发，不存、不发给任何别的地方
- 四类安全设置全开到 `BLOCK_NONE`
- 流式请求（`stream: true`）接 aistudio-api 的流式口，边生成边吐
- 流式时谷歌还没出声的那段，每秒递一个空块：酒馆那层楼上的秒数会接着走，连接也不容易被当成断了掐掉
- 思考过程放在 `reasoning_content` 里递出去，酒馆能显示思考框
- 模型列表里每个模型多一个 `[防截断]` 版本，选了才生效，不选照常走
- 支持工具调用：酒馆或插件（比如数据库填表）传来的工具会转给谷歌，谷歌调工具时按 OpenAI 的样子交回，流式也一样。选了 `[防截断]` 也不冲突：正文照样走防截断，插件的工具照样能调

## 防截断是什么

选 `gemini-xxx[防截断]` 时，翻译层会让谷歌不要直接写正文，而是调用一个叫 `reply` 的假工具，把整段正文当参数交回来，翻译层再把正文抠出来给酒馆。圈里传言这样被审查掐断得少。

先说清楚：

- 管不管用没有严格验证过，是传言
- 这也是在躲谷歌的审查，被抓到的话封的是 aistudio-api 登着的那个谷歌号，自己掂量
- 思考和正文都是整块到的：谷歌写完才一口气交回来，等的时间跟不开流式一样长。等的时候酒馆那层楼上的秒数照样在走，没卡死

## Termux 安装

装过 aistudio-oai-bridge（这个项目的前身）的，先粘这一行把旧的停掉、删掉旧的自启，再往下装：

```sh
pkill -f oai-bridge.py; sed -i '/oai-bridge/d' ~/.bashrc
```

没反应是正常的，冒出新的 `~ $` 就好。

先把 aistudio-api 装好跑起来，然后一次粘：

```sh
curl -fsSL https://raw.githubusercontent.com/meowmana/uncut-bridge/main/oai-bridge.py -o ~/oai-bridge.py
sed -i '1i curl -sf --noproxy "*" -m 2 127.0.0.1:8765/health >/dev/null || (nohup ~/aistudio-api/.venv/bin/python ~/oai-bridge.py >~/oai-bridge.log 2>&1 &)' ~/.bashrc
nohup ~/aistudio-api/.venv/bin/python ~/oai-bridge.py >~/oai-bridge.log 2>&1 &
sleep 1; curl -s --noproxy '*' 127.0.0.1:8765/health
```

- 国内下载 GitHub 文件常连不上，记得开梯子
- aistudio-api 没装在 `~/aistudio-api` 的，把第二行里的 python 路径改成你自己的

最后一行看到 `"ok": true` 就是起来了。以后每次打开 Termux 它会自己起，日志在 `~/oai-bridge.log`。

## 电脑上（Linux / macOS / Windows）

程序本身不挑系统，有 Python 3.9 以上就能跑。下载 `oai-bridge.py`，在它所在的文件夹里：

```sh
python3 oai-bridge.py      # Linux / macOS
python oai-bridge.py       # Windows
```

看到 `oai-bridge on http://127.0.0.1:8765/v1` 就是起来了，窗口别关。aistudio-api 不在本机 8080 的话，先设 `OAI_BRIDGE_UPSTREAM` 指过去（Windows PowerShell 里是 `$env:OAI_BRIDGE_UPSTREAM="http://..."`）。

## 更新

Termux：菜单按 0 退到 `~ $`，粘这一行，下新版、停旧的、起新的：

```sh
curl -fsSL https://raw.githubusercontent.com/meowmana/uncut-bridge/main/oai-bridge.py -o ~/oai-bridge.new && mv ~/oai-bridge.new ~/oai-bridge.py && pkill -f oai-bridge.py; sleep 1; nohup ~/aistudio-api/.venv/bin/python ~/oai-bridge.py >~/oai-bridge.log 2>&1 &
```

再粘这行，看到 `"ok": true` 就是新版起来了：

```sh
curl -s --noproxy '*' 127.0.0.1:8765/health
```

自启那行不用动。下载要开梯子，没下下来的话旧的会照常跑着，换个网再粘一次就行。

Linux / macOS：在跑它的窗口按 Ctrl+C 关掉，在 `oai-bridge.py` 所在的文件夹里：

```sh
curl -fsSL https://raw.githubusercontent.com/meowmana/uncut-bridge/main/oai-bridge.py -o oai-bridge.py
python3 oai-bridge.py
```

Windows：在跑它的窗口按 Ctrl+C 关掉，在 `oai-bridge.py` 所在的文件夹里开 PowerShell：

```powershell
curl.exe -fsSL https://raw.githubusercontent.com/meowmana/uncut-bridge/main/oai-bridge.py -o oai-bridge.py
python oai-bridge.py
```

也可以直接在浏览器重新下 `oai-bridge.py`，把旧文件覆盖掉，再照常启动。

## 卸载

Termux：菜单按 0 退到 `~ $`，粘这一行，停掉、删掉自启、删掉文件：

```sh
pkill -f oai-bridge.py; sed -i '/oai-bridge/d' ~/.bashrc; rm -f ~/oai-bridge.py ~/oai-bridge.log
```

没反应是正常的，冒出新的 `~ $` 就是删完了。

电脑上：在跑它的窗口按 Ctrl+C 关掉，再把 `oai-bridge.py`（下了 `check.py` 的话也一起）删掉。电脑上没有自启，也没装别的东西。

最后把酒馆和插件里填的 `http://127.0.0.1:8765/v1` 改回原来的地址，不然会连不上。

## 插件里怎么填

- 接口地址：`http://127.0.0.1:8765/v1`
- API Key：aistudio-api 的 `config.yaml` 里 `api_keys` 那把
- 模型：能直接拉列表，比如 `gemini-2.5-flash`，或者 `gemini-2.5-flash[防截断]`

## 酒馆聊天也走这里

- 聊天补全来源选「自定义（兼容 OpenAI）」，地址和密钥同上
- 想看思考：把「请求模型推理」勾上。酒馆的「推理强度」对 gemini 模型名不会发出来，选什么都一样

## 可调的环境变量

| 变量 | 默认 |
|---|---|
| `OAI_BRIDGE_UPSTREAM` | `http://127.0.0.1:8080` |
| `OAI_BRIDGE_PORT` | `8765` |
| `OAI_BRIDGE_HOST` | `127.0.0.1` |

## 自检

```sh
python check.py
```

会起一个假的 Gemini 端，把模型列表、错钥匙、普通对话、流式、思考、防截断各走一遍。

想拿真谷歌试工具调用（翻译层要先跑着，会用掉五次请求）：

```sh
python try-tools.py
```

它装成一个要填表的插件，发表、收表、回传结果、开防截断再来一遍，每一步写通过还是没过。
