# -*- coding: utf-8 -*-
"""Offline, read-only host for the RUL-SPACE evidence cockpit.

本应用只读 results/，永不触碰训练代码与数据。
It reads completed holdout replay artifacts and result receipts, never imports
training code, never writes project data, and embeds all frontend assets locally.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

try:  # streamlit run 下脚本目录在 sys.path; pytest 下由仓库根导入 —— 两态兼容
    from dashboard import payload as payload_mod
except ImportError:  # pragma: no cover
    import payload as payload_mod

APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR / "assets"
FONT_DIR = ASSET_DIR / "fonts"
VENDOR_DIR = ASSET_DIR / "vendor"
BRANDING_DIR = ASSET_DIR / "branding"
CANVAS_HEIGHT = 920

st.set_page_config(
    page_title="BRPHM · 湖南大学航天器寿命预测驾驶舱",
    page_icon="湖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------- 资产读取(只读)
_ASSETS = ("cockpit.css", "cockpit.html", "cockpit.js", "streamlit_shell.css")
_VENDOR_ASSETS = ("three.min.js", "d3.min.js", "gsap.min.js")


def read_asset(name: str) -> str:
    with (ASSET_DIR / name).open("r", encoding="utf-8") as fh:  # 显式 "r"
        return fh.read()


def read_vendor(name: str) -> str:
    """Read a fixed local dependency; the cockpit never reaches a CDN."""
    return (VENDOR_DIR / name).read_text(encoding="utf-8")


def inline_brand_asset(name: str) -> str:
    """Embed the official vector derivative so the dashboard remains offline."""
    path = BRANDING_DIR / name
    if not path.is_file() or path.stat().st_size == 0:
        raise FileNotFoundError(f"缺少官方品牌资产，拒绝使用空白占位图: {path}")
    mime = "image/svg+xml" if path.suffix.lower() == ".svg" else "image/png"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def inline_fonts() -> str:
    """assets/fonts/*.woff2 → @font-face(base64 内联)。命名约定见 fonts/README.md。"""
    fam = {"mono": "RSMono", "ui": "RSUI"}
    rules = []
    if FONT_DIR.is_dir():
        for f in sorted(FONT_DIR.glob("*.woff2")):
            stem = f.stem.lower()
            base = stem.replace("-bold", "")
            if base not in fam:
                continue
            weight = 700 if stem.endswith("-bold") else 400
            b64 = base64.b64encode(f.read_bytes()).decode("ascii")
            rules.append(
                "@font-face{font-family:'%s';font-weight:%d;font-display:swap;"
                "src:url(data:font/woff2;base64,%s) format('woff2');}"
                % (fam[base], weight, b64)
            )
    return "\n".join(rules)


def compose(payload: dict) -> str:
    """组装单文件 srcdoc: 样式 + 负载 + 引擎, 零外部引用。"""
    styles = "\n".join((inline_fonts(), read_asset("cockpit.css")))
    doc = read_asset("cockpit.html")
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    doc = doc.replace("__STYLES__", styles)
    doc = doc.replace("__HNU_HORIZONTAL_SVG_DATA__", inline_brand_asset("hnu-official-horizontal.svg"))
    doc = doc.replace("__HNU_VERTICAL_SVG_DATA__", inline_brand_asset("hnu-official-vertical.svg"))
    vendor_placeholders = {"three.min.js": "__THREE_SCRIPT__", "d3.min.js": "__D3_SCRIPT__", "gsap.min.js": "__GSAP_SCRIPT__"}
    for name in _VENDOR_ASSETS:
        doc = doc.replace(vendor_placeholders[name], read_vendor(name).replace("</script", "<\\/script"))
    doc = doc.replace("__PAYLOAD_JSON__", payload_json)
    doc = doc.replace("__CONTROL_JSON__", '{"enabled":false,"reason":"使用 dashboard.server 启用任务工作流"}')
    doc = doc.replace("__SCRIPT__", read_asset("cockpit.js"))
    return doc


# ---------------------------------------------------------------- 页面
missing = [n for n in _ASSETS if not (ASSET_DIR / n).exists()]

if not missing:
    st.markdown("<style>%s</style>" % read_asset("streamlit_shell.css"), unsafe_allow_html=True)
    components.html(compose(payload_mod.public_payload()), height=CANVAS_HEIGHT, scrolling=True)
else:  # ---- 降级兜底: 资产缺失也绝不白屏(保 ①②③ 三分区与纪律徽记) ----
    st.warning("画布资产缺失 %s —— 已降级为极简三栏。" % ", ".join(missing))
    data = payload_mod.public_payload()
    col_left, col_mid, col_right = st.columns([1.0, 2.05, 1.25], gap="medium")
    with col_left:
        st.subheader("① 部件 / 工况")
        options = [s["sample_id"] for s in data["samples"]]
        sid = st.selectbox("样本", options) if options else None
    with col_mid:
        st.subheader("② 遥测回放")
        if sid:
            st.line_chart(data["telemetry"][sid]["channels"])
        else:
            st.info(data.get("source_state", {}).get("reason", "无可用回放样本"))
    with col_right:
        st.subheader("③ 健康状态")
        if sid:
            st.metric("末点 RUL (days)", data["telemetry"][sid]["labels"]["rul_days"][-1])
    st.caption(payload_mod.DISCIPLINE)
