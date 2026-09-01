# 湖南大学品牌资产溯源

本驾驶舱使用的湖南大学校徽和校名来自湖南大学官方校庆专题网站的“标识下载”页面：<https://100.hnu.edu.cn/bsxz1.htm>。

下载页链接到官方压缩包 `bsxz/xiaohui.zip`。下载时间为 2026-08-06（Asia/Shanghai），压缩包 SHA-256 为 `ea575503fe97c96f22a9cbdc7e7c7a5b35441e02ab2ac5590c93f1a77b32fb45`。

| 交付文件 | 来源与处理 | SHA-256 |
| --- | --- | --- |
| `hnu-official-source.ai` | 压缩包中的官方原始 AI/PDF 兼容矢量源；未修改。 | `3f677c004988f44549aea4a715385b1b0aa6b5f85d35943ffdfa68ca551fb0b1` |
| `hnu-official-vertical.svg` | 从原始 AI 第 1 页导出的浏览器 SVG；文字保持路径，未重描、未改色。 | `fc3932d2c011663a2950a91262db7ab85e8c6027c3af34aa101ecd1adbccf271` |
| `hnu-official-horizontal.svg` | 从原始 AI 第 2 页导出的浏览器 SVG，仅将 `viewBox` 收紧为 `280 760 700 250` 以去除官方画板留白，并补齐源文件使用的 Inkscape XML 命名空间声明；路径未重描、未改色。 | `4b6e58ee3ce6fae13a16401e6f7b56b148b13dd8c0a68628ce7b1f621b92bd0c` |

浏览器兼容版本由 PyMuPDF/MuPDF 从官方 PDF 兼容 AI 直接序列化为 SVG。横式文件的紧画幅只影响浏览器排版边界，不改变任何官方路径、色值或校名结构；补齐的 `xmlns:inkscape` 仅用于声明源文件已有的 `inkscape:*` 元数据前缀，不触发联网。源 AI 同时保留，便于逐路径复核。

## 使用规则

- 浅色底：校徽和校名组合使用官方“湖大红”版本。
- 仅当整块背景为“湖大红”时：才可使用官方反白版本。
- 当前 BRPHM 驾驶舱顶栏为深色石墨工作台，不是“湖大红”背景，因此使用官方湖大红 SVG，不使用白色反白稿。
- 系统图标、数据标识和工程示意可使用本地 SVG；湖南大学校徽不得自行描摹、改色或伪称非官方版本。
