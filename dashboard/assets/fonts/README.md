# assets/fonts —— 字体天花板启用位(可选, 落文件即生效)

系统字体栈已保证国内直出不丑; 想再上一档"大厂质感", 把 WOFF2 按下述命名放进本目录,
`app.py` 启动时会自动 base64 内联为 @font-face(零外联、离线可用、评委机零安装):

| 文件名 | 生效字族(tokens.css 已首选) | 建议字体 |
|---|---|---|
| `mono.woff2` | RSMono(读数/代码/坐标) | JetBrains Mono Regular |
| `mono-bold.woff2` | RSMono 700 | JetBrains Mono Bold |
| `ui.woff2` | RSUI(界面正文) | Inter Regular / HarmonyOS Sans |
| `ui-bold.woff2` | RSUI 700 | Inter SemiBold |

获取: 官方 GitHub Release 下载 zip, 取 `fonts/webfonts/*.woff2` 重命名放入即可。
纪律: 本目录只放字体; 引擎不会也不允许在运行期发起任何网络请求(门禁扫描)。
