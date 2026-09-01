# -*- coding: utf-8 -*-
"""sim_loader.py — SIM 系（§simout .mat）→ interim 统一中间层适配器。

日程卡：0711-03 · 7/11 周六 · W1 · ALG-1 · 「sim_loader 打通」
输入 ➜ 输出：data/raw/sim/dev/*.mat（0711-01 真机产物，-v7.3/HDF5，AE.6-① 限期例外路径）
            ➜ data/interim/SIM_dev/unit_<样本ID>.parquet + meta.json
验收标准：Python 端画图与 MATLAB 端曲线一致（机器化 = scripts/plot_sim_loader_acceptance.py
          的逐通道 sup|Δ|==0 位级对表；validate_interim R1–R14 全绿为前置）。

契约依据（docs/interfaces.md v1.0 + 附录 AE/AF，冲突处一律从总册）：
  §1.2  仿真样本 ID 文法（DOE 六段；dev 两卡 RWA_dev_S001 / BAT_dev_S001 为 AE.6-① 例外）
  §1.4  `.` 载体映射：HDF5 组层级 `/telemetry/rw/speed_rpm` ↔ parquet 列名字面量 `rw.speed_rpm`
  §1.5  只进不出：本模块内置 _guard_write，禁写 data/raw 与 data/holdout（宪法②③）
  §5    simout 五组骨架（/telemetry /telemetry_hi /truth /labels /meta）
  附录AF（simout-1.0 字节级定稿，本模块逐条硬编码）：
        N×1 列向量 → HDF5 (1,N)，消费端 ravel（AF.2-①）；
        字符串 = uint8 UTF-8 字节向量，tobytes().decode('utf-8')（AF.2-④）；
        MATLAB_* 簿记属性忽略（AF.2-⑤）；/labels attrs 恰 3 个契约属性；
        /meta 九个稳定字段（固定序）+ meta_json 聚合逐项一致（AF.1）；
        /truth/cycle_index = floor(t/P)，P=5740 s（AF.1；§5.3 骨架的 /truth/bat/cycle_index
        旧位兼容读取并告警）；按线过滤 RWA=orbit/*+rw/*、BAT=orbit/*+bat/*（AF.3）。
  §6.2  容器与 dtype：t float64；遥测/特权 float32（orbit.eclipse_flag/label.fail int8）；
        label.rul/label.hi float32；parquet 引擎 pyarrow、压缩 zstd；列名即通道字典名
  §6.3  profile：SIM_dev/SIM_rwa = rw_telemetry(t_unit=day)；SIM_bat = battery_cycle(cycle)
  §6.4  meta.json 必填字段 + channels[] 全登记 + units{} 单元字典（含 extra 溯源）
  §6.5  通道字典：rw.*/bat.* 遥测；rw.friction_torque_nm/wear_volume_mm3/lube_level 与
        bat.soh/lli/lam = privileged（bat.soh 亦为标签源，desc 注明）；bat.c_rate_mean
        为 §6.7-A2 卡先例的可选逐循环列
  §6.6  标签口径：/labels 原值直搬（rul 不封顶、hi 只下夹 0、fail 单调不减；删失
        rul=NaN/fail≡0）；hi_def：RWA=rw_wear_fric_max_v1、BAT=bat_soh_lin80
  §6.7  SIM 映射卡（本模块逐字落地）：/telemetry→遥测列；/truth→privileged 列；
        /labels→label.* 原值直搬；SIM_bat 另按 cycle_index 重建 battery_cycle 逐循环表
        （bat.capacity_ah = soh×q0 等）；units[].extra 记 YAML 摘要、DOE 格点、种子；
        /telemetry_hi 不入 interim（§5.2）
  附录AE：q0 推导（Z.3/N.2 与 AE.5 轨1账单恒等式：DOD=Δah_dis/Q0 ⇒ Q0=2.0 Ah）；
        [查3] 逐轨结算拍 t=kP 的样本记结算后 SOH（本表逐循环重建的取样锚）；
        AE.6-① dev 隔离路径 0714-01 收编即废（本模块 SIM_dev 出口为其 interim 镜像）
  §10.1 R6 ⇒ label.rul 必须逐点满足 rul = failure_time − t（|Δ|≤1e-6）：本模块对
        f32 量化边界做预检（|rul| < 32 天时 f32 半 ULP≈9.5e-7 < 1e-6，dev 两样本
        RUL≲9.9 天远在带内；≥32 天进入 2^5 指数档 ULP 翻倍、半 ULP≈1.9e-6 越 R6，
        此时大声报错并指路附录 AG-6，不静默放行）
  §10.1 R7 ⇒ label.hi 观测域 [0,1.5]：/labels/hi 原值直搬 + 装载预检（越界大声
        报错，不静默截断——SIM 系 hi 可由特权列还原，处置须走 §11，见附录 AG-6）

三个出口（--dataset 选择，schema 完全同构）：
  SIM_dev   dev 联调隔离区（本任务日出口；role=evidence 借 C1 语义=永不进 processed，
            防误食；0714-01 批量收编正式树时随 AE.6-① 一并退役，附录 AG-4）
  SIM_rwa   正式 rw_telemetry 时序（0714-01 批量生产启用；接线零改动）
  SIM_bat   正式 battery_cycle 逐循环重建（0714-01 启用；重建数学本日交付并锁测）

用法（仓库根执行；真机需 h5py + pyarrow，均在钉版 requirements 内，依赖增量为零）::

    python -m src.datasets.sim_loader --dev              # dev .mat → data/interim/SIM_dev/
    python -m src.datasets.sim_loader --dev --verify     # 附加写后回读逐位复核
    python -m src.datasets.sim_loader --dataset SIM_rwa  # 0714-01 正式路（rwa 全量）
    python -m src.datasets.sim_loader --dataset SIM_bat  # 0714-01 正式路（逐循环重建）
    python -m src.datasets.sim_loader --dev --workers 32 # 服务器并行（默认 auto）
    python -m src.datasets.sim_loader --selftest         # 零数据零痕迹自检（沙箱可跑）

服务器优化说明（多路 GPU / 超多核 CPU / 大内存环境；不引任何新依赖）：
  · 文件级进程池：每个 .mat 由独立 worker 完成「读 HDF5 → 组帧 → 写 parquet」全程，
    读与写的 I/O 天然并行；--workers auto = min(样本数, CPU 核数)。0714-01 的 648 条
    样本在 64C/128C 服务器上近线性加速（单条 dev BAT ≈ 亚秒级）。
  · worker 内先钉死 BLAS/OMP 线程数=1（OMP/MKL/OPENBLAS/NUMEXPR/BLIS），杜绝
    「进程数 × 线程数」在超多核机上的超订阅抖动；本负载为 memcpy/IO 密集，单线程
    numpy 向量化即打满内存带宽。
  · 大内存策略：整样本一次性驻留（BAT dev 全表 ≈ 数十 MB），HDF5 原始块缓存放大到
    64 MiB/文件（rdcc_nbytes），消灭重复块解压；逐循环重建用 reshape(n_orb, 574)
    整块向量化，零 Python 逐轨循环。
  · GPU：本链路为 I/O+memcpy 密集，无 GPU 收益；刻意不 import torch（依赖面零新增，
    亦符合解析器不触训练栈的分层纪律）。GPU 请留给 0711-04 起的张量化与训练任务。
  · 确定性：输出 parquet 内容与 --workers 取值严格无关（帧构造纯函数化；meta 按
    unit_id 排序装配）；--verify 写后回读逐位复核并打印每表 sha256。

沙箱降级（承 0709-02/0709-09「无网沙箱可验收」惯例）：无 h5py 环境下本模块可导入、
--selftest 可跑（内存合成样本走全部纯函数路径，真 HDF5/parquet 项打 [SKIP-*]）；
tests/test_sim_loader.py 以 FakeH5 + parquet 打桩覆盖全部映射逻辑，真机自动升级为
真 h5py/pyarrow 路（惯例同 tests/test_matr.py 头注）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import math
import os
import re
import sys

try:
    from scripts._serveropt import run_map as _run_map
except ModuleNotFoundError:  # direct ``python scripts/sim_loader.py``
    from _serveropt import run_map as _run_map
import time
from pathlib import Path

import numpy as np
import pandas as pd

# =====================================================================
# 契约常量区（全部硬编码自 docs/interfaces.md + 附录 AE/AF；禁止运行期改写）
# =====================================================================
PARSER_VERSION = "1.0 (0711-03)"
SCHEMA_VERSION = "1.0"                       # §6.4 interim 契约版本
SIMOUT_SCHEMA = "simout-1.0"                 # 附录 AF 首发
P_ORBIT = 5740.0                             # [s] 附录 AE.2 周期统一
DT01 = 10.0                                  # [s] §5.1 主档栅格
BEATS_PER_ORBIT = 574                        # = P_ORBIT / DT01（附录 AE）
Q0_AH = 2.0                                  # [Ah] ECM 标称容量：附录 Z.3
#   lut_ah_scale=2.5=q0_lut/q0_ecm 且 N.2 等效循环基于 q0_lut=5.0 ⇒ q0_ecm=2.0；
#   与 AE.5 轨 1 账单恒等式互证：DOD=0.5583=1.11667Ah/Q0 ⇒ Q0=2.0。
Q0_DEF = "nominal"
BAT_FAIL_SOH = 0.80                          # §6.6-3 唯一失效判据（BAT）
HI_R7_MAX = 1.5                              # §10.1-R7 观测域上界（装载预检）
RUL_R6_TOL = 1e-6                            # §10.1-R6 逐点容差（f32 边界预检）
RUL_CARRY_ATOL = 2e-4                        # 原值直搬一致性（AF/V6e 同款 f32 容差）

META_FIELDS = ["sample_id", "config_yaml", "seed", "sim_model_version",
               "lut_versions", "gmat_env_file", "matlab_ver",
               "created_utc", "schema_version"]          # AF.1 固定序

LINES = {
    "rwa": {"grp": "rw",
            "tel": ["speed_rpm", "motor_current_a", "cmd_torque_nm",
                    "bearing_temp_c"],
            "tru": ["friction_torque_nm", "wear_volume_mm3", "lube_level"],
            "hi_def": "rw_wear_fric_max_v1"},
    "bat": {"grp": "bat",
            "tel": ["voltage_v", "current_a", "temp_c", "soc"],
            "tru": ["soh", "lli", "lam"],
            "hi_def": "bat_soh_lin80"},
}
FAILURE_MODE_NAMES = {                        # §5.4 码表 → meta.units[].failure_mode 文字名
    0: "none", 1: "rw_wear_over_threshold", 2: "rw_speed_tracking_fail",
    3: "rw_lube_sudden_loss", 11: "bat_soh_le_80", 12: "bat_micro_short",
}

# 通道字典（§6.5 相关行的逐字落地；name → (unit, visibility, dtype, desc)）
_CH = {
    "t": ("t_unit", "index", "float64", "interim 统一索引（§6.2；simout t_days→t）"),
    "orbit.eclipse_flag": ("-", "env", "int8", "0 日照/1 半影/2 本影（§5.1/AF.1）"),
    "orbit.beta_deg": ("deg", "env", "float32", "太阳 β 角（§5.1）"),
    "rw.speed_rpm": ("rpm", "telemetry", "float32", "轮速（表 13）"),
    "rw.motor_current_a": ("A", "telemetry", "float32", "电机电流（表 13）"),
    "rw.cmd_torque_nm": ("N·m", "telemetry", "float32", "指令力矩（清单⑤扩展）"),
    "rw.bearing_temp_c": ("degC", "telemetry", "float32", "轴承温度（表 13）"),
    "bat.voltage_v": ("V", "telemetry", "float32", "端电压（表 13）"),
    "bat.current_a": ("A", "telemetry", "float32", "电流，正=放电（附录 K.3）"),
    "bat.temp_c": ("degC", "telemetry", "float32", "电池温度（表 13）"),
    "bat.soc": ("0-1", "telemetry", "float32", "板载 SOC 估计（§5.1）"),
    "rw.friction_torque_nm": ("N·m", "privileged", "float32",
                              "摩擦力矩真值（/truth，仅 tgt，R8）"),
    "rw.wear_volume_mm3": ("mm3", "privileged", "float32",
                           "累计磨损真值（/truth，仅 tgt，R8）"),
    "rw.lube_level": ("0-1", "privileged", "float32",
                      "润滑水平真值（/truth，仅 tgt，R8）"),
    "bat.soh": ("0-1", "privileged", "float32",
                "SOH 真值；亦是标签源 SOH<=0.8 失效（§6.5 privileged+label_source）"),
    "bat.lli": ("0-1", "privileged", "float32", "LLI 真值 = 1−soh（附录 AE.2）"),
    "bat.lam": ("0-1", "privileged", "float32", "LAM 真值（v1 恒 0，附录 AE.2）"),
    "bat.capacity_ah": ("Ah", "telemetry", "float32",
                        "逐循环容量 = soh×q0（§6.7 SIM_bat 重建）"),
    "bat.temp_mean_c": ("degC", "telemetry", "float32", "逐循环电池温度均值（§6.5）"),
    "bat.charge_time_s": ("s", "telemetry", "float32",
                          "逐循环充电时长（i<0 拍数×10 s，K.3 符号约定）"),
    "bat.c_rate_mean": ("1/h", "telemetry", "float32",
                        "逐循环平均倍率 mean|i|/q0（§6.7-A2 卡先例可选列）"),
    "label.rul": ("t_unit", "label", "float32", "RUL 原值不封顶；删失 NaN（§6.6-1）"),
    "label.hi": ("0-1+", "label", "float32", "0=健康基线,1=失效阈值,只下夹 0（§6.6-2）"),
    "label.fail": ("0/1", "label", "int8", "失效起 1、单调不减；删失恒 0（§6.6-4）"),
}
TS_COLS = {
    "rwa": ["t", "orbit.eclipse_flag", "orbit.beta_deg",
            "rw.speed_rpm", "rw.motor_current_a", "rw.cmd_torque_nm",
            "rw.bearing_temp_c",
            "rw.friction_torque_nm", "rw.wear_volume_mm3", "rw.lube_level",
            "label.rul", "label.hi", "label.fail"],
    "bat": ["t", "orbit.eclipse_flag", "orbit.beta_deg",
            "bat.voltage_v", "bat.current_a", "bat.temp_c", "bat.soc",
            "bat.soh", "bat.lli", "bat.lam",
            "label.rul", "label.hi", "label.fail"],
}
CYCLE_COLS = ["t", "bat.capacity_ah", "bat.temp_mean_c", "bat.charge_time_s",
              "bat.c_rate_mean", "bat.soh", "bat.lli", "bat.lam",
              "label.rul", "label.hi", "label.fail"]

LICENSE_SIM = ("团队自建仿真数据集(YAML+种子+一键再生成, 见 docs/sim_method.md)")
DATASETS = {
    "SIM_dev": dict(
        display_name="自建仿真 dev 联调样本（隔离区，AE.6-① interim 镜像）",
        domain="tgt", role="evidence", product_line="generic",
        profile="rw_telemetry", t_unit="day", mode="timeseries",
        lines=("rwa", "bat"), raw_default="data/raw/sim/dev",
        registered=False),
    "SIM_rwa": dict(
        display_name="自建反作用轮仿真 (数据工厂)",
        domain="tgt", role="finetune", product_line="rw",
        profile="rw_telemetry", t_unit="day", mode="timeseries",
        lines=("rwa",), raw_default="data/raw/sim/rwa",
        registered=True),
    "SIM_bat": dict(
        display_name="自建电池仿真 (数据工厂)",
        domain="tgt", role="finetune", product_line="bat",
        profile="battery_cycle", t_unit="cycle", mode="cycle",
        lines=("bat",), raw_default="data/raw/sim/bat",
        registered=True),
}


class LoaderUnavailable(RuntimeError):
    """请求的加载路不可用（h5py 未装 / 文件缺失）。"""


class ContractViolation(RuntimeError):
    """输入 .mat 或产出帧违反 interfaces.md 契约（大声失败，不静默修补）。"""


# =====================================================================
# 宪法防线：任何写点不得落入 data/raw 或 data/holdout（§1.5 只进不出）
# =====================================================================
def _guard_write(path, root):
    p = Path(path).resolve()
    r = Path(root).resolve()
    for banned in (r / "data" / "raw", r / "data" / "holdout"):
        try:
            p.relative_to(banned)
        except ValueError:
            continue
        raise PermissionError(
            f"接口总册宪法②③：禁止向 {banned} 写入（目标 {p}）")
    return p


# =====================================================================
# HDF5 读取小件（AF.2 字节级约定；_open_h5 为 IO seam，测试可打桩 FakeH5）
# =====================================================================
def _open_h5(path):
    try:
        import h5py
    except ImportError as e:                              # pragma: no cover
        raise LoaderUnavailable(
            "读取 §simout .mat 需要 h5py（钉版清单 h5py==3.16.0 已含；"
            "沙箱无 h5py 时请以 --selftest / tests 的 FakeH5 路自证）") from e
    # 大内存服务器：原始块缓存 64 MiB/文件，消灭重复块解压（rdcc_*）
    return h5py.File(path, "r", rdcc_nbytes=64 * 2 ** 20, rdcc_nslots=1_000_003)


def _vec(f, path):
    """(1,N)/(N,1)/(N,) 数据集 → 一维向量（保持原生 dtype；AF.2-① ravel 读）。"""
    return np.asarray(f[path][()]).ravel()


def _s(f, path):
    """uint8 UTF-8 字节向量 → str（AF.2-④）。"""
    a = np.asarray(f[path][()], dtype=np.uint8)
    return a.tobytes().decode("utf-8")


def _user_attrs(obj):
    """契约属性 = 非 MATLAB_* / H5PATH 属性（AF.2-⑤，与 validate_simout 同式）。"""
    return {str(k): v for k, v in obj.attrs.items()
            if not str(k).startswith("MATLAB") and str(k) != "H5PATH"}


def _attr_scalar(v):
    return float(np.asarray(v).ravel()[0])


def _attr_text(v):
    a = np.asarray(v)
    if a.dtype.kind in ("u", "i"):
        return a.astype(np.uint8).tobytes().decode("utf-8")
    if a.dtype.kind == "S":
        return b"".join(a.ravel().tolist()).decode("utf-8", "ignore")
    return str(v)


def _sha256(path, chunk=4 * 2 ** 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


# =====================================================================
# 样本卡受限 YAML 子集解析（附录 AE.7；与 run_dev_s001.m::read_flat_yaml 同构）
# =====================================================================
def read_flat_yaml(text):
    """顶层 `k: v` + 两空格缩进 `overrides:` 带点键；# 整行注释；数值转 float。"""
    top, over, in_over = {}, {}, False
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if not raw.startswith(" "):
            in_over = False
            key, _, val = raw.partition(":")
            key, val = key.strip(), val.strip()
            if key == "overrides":
                in_over = True
                continue
            top[key] = _yaml_scalar(val)
        elif in_over and raw.startswith("  "):
            key, _, val = raw.strip().partition(":")
            over[key.strip()] = _yaml_scalar(val.strip())
    return {"top": top, "overrides": over}


def _yaml_scalar(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v


_DOE_RE = re.compile(
    r"^(RWA|BAT)_([A-Za-z0-9]+)_B(\d+)_H(\d+)_L(\d+)_S(\d+)$")   # §1.2 六段文法


def parse_sample_id(sid):
    """§1.2 DOE 文法解析；dev 卡（RWA_dev_S001）走宽容分支并显式打 dev 标。"""
    m = _DOE_RE.match(sid)
    if m:
        return {"part": m.group(1), "orbit": m.group(2),
                "beta_deg": int(m.group(3)), "h_level": int(m.group(4)),
                "load_level": int(m.group(5)), "series": int(m.group(6)),
                "dev": False, "raw_id": sid}
    m2 = re.match(r"^(RWA|BAT)_dev_S(\d+)$", sid)
    if m2:
        return {"part": m2.group(1), "orbit": None, "beta_deg": None,
                "h_level": None, "load_level": None,
                "series": int(m2.group(2)), "dev": True, "raw_id": sid}
    return {"part": sid[:3] if sid[:3] in ("RWA", "BAT") else None,
            "orbit": None, "beta_deg": None, "h_level": None,
            "load_level": None, "series": None, "dev": None, "raw_id": sid}


# =====================================================================
# 读取层：§simout .mat（AF 定稿布局）→ SimSample（普通 dict 容器）
# =====================================================================
def read_simout(path, lenient=False, _h5=None):
    """按 AF.1/AF.2/AF.3 逐条装载与预检；返回 SimSample dict。

    预检（装载即验，红=ContractViolation；lenient=True 降级为 [WARN] 继续）：
      P1 顶层五组齐全            P2 sample_id ↔ 按线过滤（AF.3）
      P3 t 轴严格递增/自 0 起/10 s 栅格（§5.1/AF.1）
      P4 遥测有限性 + N 对齐      P5 labels 不变式（fail 单调、hi≥0、rul=Tf−t 原值一致）
      P6 /labels attrs 恰 3 个 + 码表（AF.1/§5.4）
      P7 truth 结构（cycle_index=floor(t/P)；BAT lli=1−soh、lam≡0；RWA 磨损/润滑单调）
      P8 /meta 九个稳定字段 + meta_json 聚合逐项一致（AF.1）
      P9 R6/R7 门禁边界预检（f32 量化余量、hi≤1.5 观测域）——interim 侧不通过的
         输入在装载点就地拦截，错误信息给出处置指路（附录 AG-6）
    """
    warns = []

    def _flag(cond, tag, msg):
        if cond:
            return
        line = f"[{tag}] {msg}"
        if lenient:
            warns.append(line)
            print("[WARN]", line)
        else:
            raise ContractViolation(line + "（--lenient 可降级为警告以便排障）")

    f = _h5 if _h5 is not None else _open_h5(path)
    try:
        top = set(f.keys()) - {"#refs#", "#subsystem#"}
        _flag({"telemetry", "telemetry_hi", "truth", "labels", "meta"} <= top,
              "P1", f"顶层五组不全：{sorted(top)}（§5/AF.1）")

        sid = _s(f, "meta/sample_id")
        line = ("rwa" if sid.startswith("RWA")
                else "bat" if sid.startswith("BAT") else None)
        _flag(line is not None, "P2",
              f"sample_id={sid!r} 不属 RWA/BAT 线（§1.2）")
        spec = LINES[line]
        g = spec["grp"]
        other = "bat" if g == "rw" else "rw"
        _flag(f"telemetry/{g}" in f and f"telemetry/{other}" not in f,
              "P2", f"/telemetry 按线过滤违例（AF.3：{sid} 只应含 orbit/*+{g}/*）")
        _flag(f"truth/{g}" in f and f"truth/{other}" not in f,
              "P2", f"/truth 按线过滤违例（AF.3）")

        t_days = _vec(f, "telemetry/t_days").astype(np.float64)
        n = t_days.size
        _flag(bool(np.all(np.diff(t_days) > 0)), "P3",
              "t_days 非严格递增（§5.1）")
        _flag(abs(float(t_days[0])) < 1e-12
              and bool(np.allclose(np.diff(t_days) * 86400.0, DT01, atol=1e-6)),
              "P3", "主档非自 0 起的 10 s 均匀栅格（§5.1/AF.1）")

        env = {"eclipse_flag": _vec(f, "telemetry/orbit/eclipse_flag"),
               "beta_deg": _vec(f, "telemetry/orbit/beta_deg")}
        tel, tru = {}, {}
        for c in spec["tel"]:
            x = _vec(f, f"telemetry/{g}/{c}")
            _flag(x.size == n and bool(np.all(np.isfinite(
                np.asarray(x, dtype=np.float64)))), "P4",
                f"telemetry/{g}/{c} 长度或有限性违例")
            tel[c] = x
        for c in spec["tru"]:
            x = _vec(f, f"truth/{g}/{c}")
            _flag(x.size == n, "P4", f"truth/{g}/{c} 长度违例")
            tru[c] = x

        # cycle_index：AF.1 定稿位 /truth/cycle_index；§5.3 骨架旧位兼容读并告警
        if "truth/cycle_index" in f:
            cyc = _vec(f, "truth/cycle_index").astype(np.int64)
        elif f"truth/{g}/cycle_index" in f:
            print(f"[WARN] [P7] {sid}: cycle_index 位于 §5.3 骨架旧位 "
                  f"/truth/{g}/cycle_index（AF.1 定稿位为 /truth/cycle_index）；"
                  "已兼容读取，请提醒写手按 AF 重存")
            cyc = _vec(f, f"truth/{g}/cycle_index").astype(np.int64)
        else:
            _flag(False, "P7", "缺 /truth/cycle_index（AF.1）——按 floor(t/P) 重建")
            cyc = np.floor(t_days * 86400.0 / P_ORBIT + 1e-9).astype(np.int64)
        _flag(bool(np.array_equal(
            cyc, np.floor(t_days * 86400.0 / P_ORBIT + 1e-9).astype(np.int64))),
            "P7", "cycle_index ≠ floor(t/P)（AF.1）")

        lab = {"rul_days": _vec(f, "labels/rul_days"),
               "hi": _vec(f, "labels/hi"),
               "fail": _vec(f, "labels/fail")}
        fail_i = np.asarray(lab["fail"], dtype=np.int64)
        _flag(set(np.unique(fail_i)).issubset({0, 1})
              and bool(np.all(np.diff(fail_i) >= 0)),
              "P5", "labels/fail 取值或单调性违例（§6.6-4）")
        hi64 = np.asarray(lab["hi"], dtype=np.float64)
        _flag(bool(np.all(hi64 >= -1e-6)), "P5", "labels/hi 出现 <0（§6.6-2 只下夹 0）")

        ua = _user_attrs(f["labels"])
        _flag(set(ua) == {"failure_time_days", "failure_mode_code",
                          "criterion_text"},
              "P6", f"/labels attrs 应恰 3 个契约属性，得 {sorted(ua)}（AF.1）")
        tf_days = _attr_scalar(ua.get("failure_time_days", np.nan))
        mode = int(_attr_scalar(ua.get("failure_mode_code", 0)))
        criterion = _attr_text(ua.get("criterion_text", b""))
        _flag(mode in FAILURE_MODE_NAMES and len(criterion) > 0,
              "P6", f"失效码 {mode} 不在 §5.4 码表或判据串为空")

        rul64 = np.asarray(lab["rul_days"], dtype=np.float64)
        if math.isfinite(tf_days):
            _flag(bool(np.allclose(rul64, tf_days - t_days,
                                   atol=RUL_CARRY_ATOL)),
                  "P5", "rul_days ≠ Tf − t（原值直搬一致性，f32 容差 2e-4）")
            _flag(bool(np.max(np.abs(rul64 - (tf_days - t_days)))
                       <= RUL_R6_TOL),
                  "P9", "label.rul 的 f32 量化超 R6 容差 1e-6：本样本 RUL 量程"
                        "超出 f32 精确带（|rul|≥32 天进入 2^5 指数档，半 ULP"
                        "≈1.9e-6）。§6.2 钉 label.rul=float32、§10.1-R6 钉 1e-6，"
                        "两者在长视界样本上冲突——"
                        "按附录 AG-6 走 §11 澄清后再收此样本，勿静默放行")
        else:
            _flag(bool(np.all(fail_i == 0))
                  and bool(np.all(np.isnan(rul64))),
                  "P5", "删失口径违例：应 rul≡NaN 且 fail≡0（AF.1/§6.6）")
        _flag(bool(np.nanmax(hi64) <= HI_R7_MAX + 1e-9), "P9",
              f"labels/hi 最大值 {np.nanmax(hi64):.4f} 超 §10.1-R7 观测域上界 "
              f"{HI_R7_MAX}：原值直搬将红 R7。SIM 系 hi 可由特权列还原，处置"
              "（截断留痕 vs 放宽 R7）须走 §11，见附录 AG-6；本装载不静默截断")

        if line == "bat":
            soh = np.asarray(tru["soh"], dtype=np.float64)
            _flag(bool(np.allclose(np.asarray(tru["lli"], dtype=np.float64),
                                   1.0 - soh, atol=2e-6)),
                  "P7", "lli ≠ 1−soh（附录 AE.2 一阶等价）")
            _flag(bool(np.allclose(np.asarray(tru["lam"], dtype=np.float64),
                                   0.0, atol=2e-6)),
                  "P7", "lam ≠ 0（v1 占位，附录 AE.2）")
            _flag(bool(np.all(np.diff(soh) <= 1e-9)), "P7", "SOH 非单调不增")
        else:
            wear = np.asarray(tru["wear_volume_mm3"], dtype=np.float64)
            lube = np.asarray(tru["lube_level"], dtype=np.float64)
            _flag(bool(np.all(np.diff(wear) >= -1e-7)), "P7", "磨损非单调不减")
            _flag(bool(np.all(np.diff(lube) <= 1e-7))
                  and float(lube.min()) >= -1e-9
                  and float(lube.max()) <= 1 + 1e-9,
                  "P7", "润滑非单调不增或越 [0,1]")

        meta = {}
        for k in META_FIELDS:
            if k == "seed":
                meta[k] = int(np.asarray(f["meta/seed"][()]).ravel()[0])
            else:
                meta[k] = _s(f, f"meta/{k}")
                _flag(len(meta[k]) > 0, "P8", f"meta/{k} 为空（AF.1）")
        _flag(meta["schema_version"] == SIMOUT_SCHEMA, "P8",
              f"schema_version={meta['schema_version']!r} ≠ {SIMOUT_SCHEMA}")
        _flag(meta["sample_id"] == sid, "P8", "meta/sample_id 与判线所用不一致")
        mj = json.loads(_s(f, "meta/meta_json"))
        _flag(all(mj.get(k) == meta[k] for k in META_FIELDS), "P8",
              "meta_json 与独立字段不逐项一致（AF.1/V8c 同口径）")
    finally:
        if _h5 is None:
            f.close()

    return {"sample_id": sid, "line": line, "n": int(n),
            "t_days": t_days, "env": env, "tel": tel, "tru": tru,
            "cycle_index": cyc,
            "labels": lab,
            "failure_time_days": (tf_days if math.isfinite(tf_days) else None),
            "failure_mode_code": mode, "criterion_text": criterion,
            "meta": meta, "warns": warns,
            "source_mat": str(path) if path is not None else "<in-memory>"}


# =====================================================================
# 组帧层 ①：时序 rw_telemetry 表（SIM_dev / SIM_rwa；§6.7「同构透传」本体）
# =====================================================================
def _f32(x):
    return np.asarray(x, dtype=np.float32)


def to_timeseries_frame(sample):
    """SimSample → (df, unit_meta)。列序 = TS_COLS[line]；dtype 按 §6.2。"""
    line, spec = sample["line"], LINES[sample["line"]]
    g = spec["grp"]
    cols = {"t": np.asarray(sample["t_days"], dtype=np.float64),
            "orbit.eclipse_flag": np.asarray(sample["env"]["eclipse_flag"],
                                             dtype=np.int8),
            "orbit.beta_deg": _f32(sample["env"]["beta_deg"])}
    for c in spec["tel"]:
        cols[f"{g}.{c}"] = _f32(sample["tel"][c])
    for c in spec["tru"]:
        cols[f"{g}.{c}"] = _f32(sample["tru"][c])
    cols["label.rul"] = _f32(sample["labels"]["rul_days"])
    cols["label.hi"] = _f32(sample["labels"]["hi"])
    cols["label.fail"] = np.asarray(sample["labels"]["fail"], dtype=np.int8)
    df = pd.DataFrame({k: cols[k] for k in TS_COLS[line]})
    unit = _unit_meta(sample, df, t_unit_days=True)
    return df, unit


# =====================================================================
# 组帧层 ②：battery_cycle 逐循环重建（SIM_bat；§6.7「另按 cycle_index 重建」）
# =====================================================================
def to_battery_cycle_frame(sample, q0=Q0_AH):
    """按 /truth cycle_index 把 0.1 Hz 时序重建为逐循环表（整块向量化）。

    取样锚（附录 AE [查3]：t=kP 样本记结算后 SOH）：
      第 k 轨（k=0..n_orb−1，行 = 完整轨）的容量取「本轨末结算」样本
      j=(k+1)·574 —— bat.capacity_ah(k) = q0·soh[j]；label.hi(k)=labels/hi[j]
      （数学上 ≡ (1−soh[j])/0.2，装载时互证）。逐轨聚合（温度均值/充电时长/
      平均倍率）取本轨 574 拍窗 [k·574, (k+1)·574)。
    失效循环量化（承 §6.7 A1/B1 电池卡「首个容量越阈循环」惯例）：
      failure_time(cycle) = min{k : capacity(k) ≤ 0.8·q0}（整数 ⇒ label.rul
      为整数值，f32 精确，R6 天然满足）；时间域精确 Tf 保留在
      units[].extra.failure_time_days_exact（AE.4 折线穿越原值，不丢信息）。
    """
    if sample["line"] != "bat":
        raise ContractViolation("battery_cycle 重建仅适用于 BAT 线（§6.7）")
    n = sample["n"]
    n_orb = (n - 1) // BEATS_PER_ORBIT
    if n_orb < 1:
        raise ContractViolation("样本不足一整轨，无法逐循环重建（§6.7 SIM_bat）")
    m = n_orb * BEATS_PER_ORBIT

    def seg(x):
        return np.asarray(x[:m], dtype=np.float64).reshape(n_orb,
                                                           BEATS_PER_ORBIT)

    settle = (np.arange(n_orb) + 1) * BEATS_PER_ORBIT       # 结算样本索引
    soh_s = np.asarray(sample["tru"]["soh"], dtype=np.float64)[settle]
    cap = q0 * soh_s
    i_seg = seg(sample["tel"]["current_a"])
    temp_mean = seg(sample["tel"]["temp_c"]).mean(axis=1)
    charge_time = DT01 * (i_seg < 0.0).sum(axis=1)          # K.3：i<0=充电
    c_rate = np.abs(i_seg).mean(axis=1) / q0
    hi_s = np.asarray(sample["labels"]["hi"])[settle]
    if not np.allclose(np.asarray(hi_s, dtype=np.float64),
                       (1.0 - soh_s) / (1.0 - BAT_FAIL_SOH), atol=5e-6):
        raise ContractViolation(
            "逐循环 hi 与 (1−soh)/0.2 不一致：labels/hi 或 truth/soh 口径漂移"
            "（AE.4-3 / §6.6-2）")

    thr = BAT_FAIL_SOH * q0
    hit = np.nonzero(cap <= thr + 1e-9)[0]
    censored = hit.size == 0
    ft_cyc = None if censored else int(hit[0])
    if not censored and sample["failure_time_days"] is not None:
        ft_time_cyc = int(math.floor(
            sample["failure_time_days"] * 86400.0 / P_ORBIT + 1e-9))
        if abs(ft_time_cyc - ft_cyc) > 1:
            raise ContractViolation(
                f"失效循环量化 {ft_cyc} 与时间域 floor(Tf/P)={ft_time_cyc} "
                "偏差 >1 轨：labels 与 truth 折线口径漂移（AE.4）")

    t_cyc = np.arange(n_orb, dtype=np.float64)
    if censored:
        rul = np.full(n_orb, np.nan, dtype=np.float32)
        failc = np.zeros(n_orb, dtype=np.int8)
    else:
        rul = _f32(float(ft_cyc) - t_cyc)                   # 整数差 ⇒ f32 精确
        failc = (t_cyc >= ft_cyc).astype(np.int8)
    df = pd.DataFrame({
        "t": t_cyc,
        "bat.capacity_ah": _f32(cap),
        "bat.temp_mean_c": _f32(temp_mean),
        "bat.charge_time_s": _f32(charge_time),
        "bat.c_rate_mean": _f32(c_rate),
        "bat.soh": _f32(soh_s),
        "bat.lli": _f32(np.asarray(sample["tru"]["lli"])[settle]),
        "bat.lam": _f32(np.asarray(sample["tru"]["lam"])[settle]),
        "label.rul": rul,
        "label.hi": _f32(hi_s),
        "label.fail": failc,
    })[CYCLE_COLS]
    unit = _unit_meta(sample, df, t_unit_days=False,
                      cycle_ft=ft_cyc, cycle_censored=censored, q0=q0)
    return df, unit


# =====================================================================
# 单元级 meta（§6.4 units.<unit_id> 字典；extra = YAML 摘要/DOE 格点/种子/溯源）
# =====================================================================
def _unit_meta(sample, df, t_unit_days, cycle_ft=None, cycle_censored=None,
               q0=Q0_AH):
    sid, line = sample["sample_id"], sample["line"]
    card = read_flat_yaml(sample["meta"]["config_yaml"])
    over_txt = ", ".join(f"{k}={_py(v)}" for k, v in
                         sorted(card["overrides"].items())) or "defaults"
    if t_unit_days:
        censored = sample["failure_time_days"] is None
        ft = None if censored else float(sample["failure_time_days"])
    else:
        censored = bool(cycle_censored)
        ft = None if censored else float(cycle_ft)
    mode = 0 if censored else sample["failure_mode_code"]
    tv = df["t"].to_numpy()
    unit = {
        "file": f"unit_{sid}.parquet",
        "n_rows": int(len(df)),
        "t_start": float(tv[0]), "t_end": float(tv[-1]),
        "failure_time": ft,
        "censored": censored,
        "failure_mode": FAILURE_MODE_NAMES.get(mode, str(mode)),
        "failure_criterion": sample["criterion_text"],
        "hi_def": LINES[line]["hi_def"],
        "operating_condition": f"{sid}: {over_txt}",
        "sampling_native": ("0.1 Hz 主档（10 s 栅格，§5.1）" if t_unit_days
                            else "逐轨结算（P=5740 s，附录 AE [查3]）"),
        "extra": {
            "seed": int(sample["meta"]["seed"]),
            "doe": parse_sample_id(sid),
            "sample_yaml_sha256": hashlib.sha256(
                sample["meta"]["config_yaml"].encode("utf-8")).hexdigest(),
            "sample_yaml_overrides": {k: _py(v) for k, v in
                                      card["overrides"].items()},
            "failure_mode_code": int(sample["failure_mode_code"]),
            "failure_time_days_exact": _py(sample["failure_time_days"]),
            "sim_model_version": sample["meta"]["sim_model_version"],
            "simout_schema_version": sample["meta"]["schema_version"],
            "gmat_env_file": sample["meta"]["gmat_env_file"],
            "matlab_ver": sample["meta"]["matlab_ver"],
            "simout_created_utc": sample["meta"]["created_utc"],
            "lut_versions_sha256": hashlib.sha256(
                sample["meta"]["lut_versions"].encode("utf-8")).hexdigest(),
            "source_mat": sample["source_mat"],
            "telemetry_hi_ingested": False,      # §5.2/§6.7：精档不入 interim
        },
    }
    if line == "bat":
        unit["q0_ah"] = float(q0)
        unit["q0_def"] = Q0_DEF
        if not t_unit_days:
            unit["extra"]["failure_time_cycles_exact"] = _py(
                None if sample["failure_time_days"] is None else
                sample["failure_time_days"] * 86400.0 / P_ORBIT)
            unit["extra"]["cycle_quantization"] = (
                "failure_time = 首个 capacity<=0.8*q0 的循环序号"
                "（§6.7 A1/B1 电池卡惯例；精确折线 Tf 见 *_exact 字段）")
    if Path(sample["source_mat"]).exists():
        unit["extra"]["source_mat_sha256"] = _sha256(sample["source_mat"])
    return unit


def _py(v):
    """numpy 标量/容器 → JSON 原生类型（meta.json 全部原生类型，承 matr 惯例）。"""
    if isinstance(v, dict):
        return {k: _py(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_py(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    return v


# =====================================================================
# 数据集级 meta.json（§6.4）与落盘（§6.2 pyarrow+zstd；_to_parquet 为 IO seam）
# =====================================================================
def build_meta(dataset_id, units, root=".", created_utc=None):
    ds = DATASETS[dataset_id]
    names = (TS_COLS["rwa"] + [c for c in TS_COLS["bat"]
                               if c not in TS_COLS["rwa"]]
             if ds["mode"] == "timeseries" and set(ds["lines"]) == {"rwa", "bat"}
             else TS_COLS[ds["lines"][0]] if ds["mode"] == "timeseries"
             else CYCLE_COLS)
    channels = []
    for nme in names:
        unit_, vis, dt, desc = _CH[nme]
        u = ({"day": "day", "cycle": "cycle"}[ds["t_unit"]]
             if unit_ == "t_unit" else unit_)
        channels.append({"name": nme, "unit": u, "visibility": vis,
                         "dtype": dt, "desc": desc})
    notes = (
        "解析器 = src.datasets.sim_loader（0711-03，§6.7 SIM 卡逐字落地）。"
        "/telemetry→遥测列、/truth→privileged 列（R8：仅 tgt 域）、/labels→"
        "label.* 原值直搬（rul 不封顶、hi 只下夹 0；R7 上界与 R6 f32 边界在装载"
        "点预检，越界大声失败不静默修补，处置见 interfaces.md 附录 AG-6）；"
        "/telemetry_hi 按 §5.2 不入 interim（units[].extra.telemetry_hi_ingested"
        "=false 留痕）。字节级读法按附录 AF.2：(1,N) ravel、uint8 UTF-8 字符串、"
        "MATLAB_* 簿记属性忽略、meta_json 与九个稳定字段逐项互证。"
        + ("q0_ah=2.0/nominal：附录 Z.3 lut_ah_scale=2.5 与 N.2 等效循环基准的"
           "闭式推导，并与 AE.5 轨 1 账单 DOD 恒等式互证；逐循环重建取样锚 = "
           "AE [查3] 结算后 SOH（本轨末 j=(k+1)*574），失效循环按 A1/B1 惯例"
           "量化为首个容量越阈循环（整数 RUL ⇒ f32 精确过 R6），精确折线 Tf "
           "存 units[].extra.failure_time_days_exact。" if ds["mode"] == "cycle"
           else "")
        + ("本数据集为 dev 联调隔离区（附录 AE.6-① 的 interim 镜像；附录 AG-4 "
           "限期例外）：role=evidence 借 C1 语义 = 加载器默认排除、永不进 "
           "processed，防误食训练树；0714-01 批量生产收编 SIM_rwa/SIM_bat "
           "正式路径时本目录随例外一并退役。dataset_id 不入 registry.py"
           "（§1.1 表为宪法级 23 项，测试硬锁），R13 预期 [SKIP]。"
           if dataset_id == "SIM_dev" else "")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "display_name": ds["display_name"],
        "domain": ds["domain"], "role": ds["role"],
        "product_line": ds["product_line"],
        "profile": ds["profile"], "t_unit": ds["t_unit"],
        "license_cite": LICENSE_SIM,
        "source_url": "",
        "parser": {"module": "src.datasets.sim_loader",
                   "version": PARSER_VERSION},
        "created_utc": created_utc or _dt.datetime.now(_dt.timezone.utc)
                                         .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "native_checksums_ref": "data/checksums.sha256",
        "notes": notes,
        "channels": channels,
        "units": {k: units[k] for k in sorted(units)},
    }


def _to_parquet(df, path):
    """IO seam：无 pyarrow 环境的等价验证可对此打桩（正式路 = pyarrow+zstd，§6.2）。"""
    df.to_parquet(path, engine="pyarrow", compression="zstd", index=False)


def write_interim(frames, units, out_dir, root, dataset_id, prune=True):
    out_dir = _guard_write(out_dir, root)
    out_dir.mkdir(parents=True, exist_ok=True)
    for uid, df in frames.items():
        _to_parquet(df, _guard_write(out_dir / units[uid]["file"], root))
    meta = build_meta(dataset_id, units, root=root)
    if prune:
        registered = {u["file"] for u in units.values()}
        for p in sorted(out_dir.glob("*.parquet")):
            if p.name not in registered:
                p.unlink()
                print(f"[i] 清除未登记 parquet：{p.name}（R1）")
    (out_dir / "meta.json").write_text(
        json.dumps(_py(meta), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return meta


# =====================================================================
# 并行编排（服务器优化主体；输出与 --workers 严格无关）
# =====================================================================
_THREAD_VARS = ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                "NUMEXPR_NUM_THREADS", "BLIS_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS")


def _pin_worker_threads():
    """worker 内钉死数值库线程=1，防超多核机上的进程×线程超订阅（初始化器）。"""
    for k in _THREAD_VARS:
        os.environ[k] = "1"


def convert_one(path, dataset_id, out_dir, root=".", lenient=False,
                q0=Q0_AH, write=True):
    """单文件全程：读 → 组帧 → （可选）写 parquet。返回 (unit_id, unit_meta, df|None)。"""
    ds = DATASETS[dataset_id]
    t0 = time.perf_counter()
    sample = read_simout(path, lenient=lenient)
    if sample["line"] not in ds["lines"]:
        raise ContractViolation(
            f"{sample['sample_id']}（{sample['line']} 线）不属于 {dataset_id} "
            f"的受理线 {ds['lines']}（AF.3 按线过滤 / §6.7）")
    if ds["mode"] == "cycle":
        df, unit = to_battery_cycle_frame(sample, q0=q0)
    else:
        df, unit = to_timeseries_frame(sample)
    if write:
        outp = _guard_write(Path(out_dir) / unit["file"], root)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        _to_parquet(df, outp)
    dt = time.perf_counter() - t0
    print(f"[i] {sample['sample_id']}: {len(df)} 行 × {df.shape[1]} 列 "
          f"({sample['line']}, {ds['mode']}) — {dt:.2f}s")
    return sample["sample_id"], unit, (None if write else df)


def _pool_worker(args):                                     # pragma: no cover
    """进程池工位（模块级、可 picklable；真机 h5py 路）。"""
    path, dataset_id, out_dir, root, lenient, q0 = args
    _pin_worker_threads()
    try:
        uid, unit, _ = convert_one(path, dataset_id, out_dir, root=root,
                                   lenient=lenient, q0=q0, write=True)
        return {"ok": True, "uid": uid, "unit": unit, "path": str(path)}
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}",
                "path": str(path)}


def _probe_top_groups(path):
    """尽力探测 .mat 顶层组名（供杂件搁置告警用；失败返回 None，不抛）。"""
    try:
        f = _open_h5(path)
        try:
            return sorted(set(f.keys()) - {"#refs#", "#subsystem#"})
        finally:
            f.close()
    except Exception:
        return None


def _partition_inputs(paths):
    """§1.2 文件名闸（0711-03 真机首跑暴露 · hotfix2）。

    正文钉死文件名约定 = `<样本ID>.mat`（§1.5 目录树、表 I5、§5 标题；写手
    `run_dev_s001` 亦按 `[sid '.mat']` 落盘）。全量 glob 时只受理文件名命中
    §1.2 文法（DOE 六段或 dev）的 .mat；其余（调试残留如 dev0.mat、系统副本
    `xxx (1).mat`、`._xxx.mat` 等）**搁置并大声列名**——不转、不删、不改
    （宪法②：data/raw 只进不出，杂件请人工移出或 --files 显式指定）。
    安全属性：本闸只看文件名，**名字合文法而内容坏的文件永远不会被静默跳过**，
    仍在装载预检处整批大声失败。--files 显式指定不走本闸。
    """
    ok, quarantined = [], []
    for p in paths:
        if parse_sample_id(p.stem)["dev"] is not None:      # 两种文法之一命中
            ok.append(p)
        else:
            quarantined.append(p)
    return ok, quarantined


def run(dataset_id, files=None, root=".", raw_dir=None, out_dir=None,
        workers=1, lenient=False, verify=False, q0=Q0_AH):
    """公开 API：批量转换并落 meta.json；返回 meta。tests 走本入口（workers=1）。"""
    if dataset_id not in DATASETS:
        raise KeyError(f"未知 dataset：{dataset_id}（可选 {sorted(DATASETS)}）")
    root = Path(root)
    raw = Path(raw_dir) if raw_dir else root / DATASETS[dataset_id]["raw_default"]
    out = Path(out_dir) if out_dir else root / "data" / "interim" / dataset_id
    paths = ([Path(p) for p in files] if files
             else sorted(raw.glob("*.mat")))
    quarantined = []
    if files is None and paths:
        paths, quarantined = _partition_inputs(paths)
        for p in quarantined:
            keys = _probe_top_groups(p)
            probe = ("顶层组探测: " + ", ".join(keys) + "（非 §simout 五组）"
                     if keys else "顶层组无法探测（h5py 缺失或文件不可读）")
            print(f"[WARN] 搁置非 §1.2 命名的 .mat（不转不删；宪法② data/raw "
                  f"只进不出，请人工移出该目录，或 --files 显式指定后按契约"
                  f"硬校验）：{p.name} —— {probe}")
        if not paths and quarantined:
            raise LoaderUnavailable(
                f"{raw} 下没有可受理的 §simout .mat：{len(quarantined)} 个"
                f"非 §1.2 命名杂件已搁置（{', '.join(p.name for p in quarantined)}）。"
                "请移走杂件（建议 mv 到 build/scratch/ 等契约区外）后重跑，"
                "或 --files 显式指定；无 MATLAB 环境可用 "
                "scripts/make_sim_dev_fixture.py 生成 oracle 同构夹具。")
    if not paths:
        if dataset_id in ("SIM_rwa", "SIM_bat"):
            # 0711-03 真机首跑暴露（hotfix1）：正式路空目录是 0714-01 前的预期，
            # 不得误指「0711-01 未就位」，并主动探测 dev 产物给出下一步。
            dev_dir = root / "data" / "raw" / "sim" / "dev"
            n_dev = len(list(dev_dir.glob("*.mat"))) if dev_dir.exists() else 0
            tail = ("请用 --dev（等价 --dataset SIM_dev）读取它们"
                    if n_dev else
                    "该目录同样为空——先核对 0711-01 是否真落盘：仓库根 "
                    "find . -name '*_dev_S001.mat' ；MATLAB 日志 grep "
                    "'A 组机器验收' 或 '[sim] 完成'")
            raise LoaderUnavailable(
                f"{raw} 下无 .mat —— 这通常是预期：{dataset_id} 是 0714-01 批量"
                f"生产的正式路，0711-03 当日尚无产物；0711-01 的 dev 真机产物落 "
                f"data/raw/sim/dev/（当前 {n_dev} 个 .mat），{tail}。提前压测"
                "正式路可 --raw-dir 指向任意含 .mat 的目录；无 MATLAB 环境可用 "
                "scripts/make_sim_dev_fixture.py 生成 oracle 同构夹具。")
        raise LoaderUnavailable(
            f"{raw} 下无 .mat 可转（0711-01 真机产物未就位？先核对 MATLAB 日志"
            "（grep 'A 组机器验收' / '[sim] 完成'）与仓库根 find . -name "
            "'*_dev_S001.mat'；无 MATLAB 环境可用 "
            "scripts/make_sim_dev_fixture.py 生成 oracle 同构夹具自证链路）")
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise LoaderUnavailable(f"输入缺失：{missing}")

    n_workers = (min(len(paths), os.cpu_count() or 1) if workers in
                 ("auto", 0, None) else max(1, int(workers)))
    units, t0 = {}, time.perf_counter()
    try:
        if n_workers <= 1 or len(paths) == 1:
            for p in paths:
                uid, unit, _ = convert_one(p, dataset_id, out, root=root,
                                           lenient=lenient, q0=q0, write=True)
                units[uid] = unit
        else:
            args = [(str(p), dataset_id, str(out), str(root), lenient, q0)
                    for p in paths]
            bad = []
            for r in _run_map(_pool_worker, args, n_workers,
                              initializer=_pin_worker_threads):
                if r["ok"]:
                    units[r["uid"]] = r["unit"]
                else:
                    bad.append(f"{r['path']}: {r['err']}")
            if bad:
                raise ContractViolation(
                    "并行转换失败：\n  " + "\n  ".join(bad))
    except ContractViolation as e:
        # 0711-03 真机首跑暴露（hotfix2）：批量失败时明说半成品状态与自愈路径
        raise ContractViolation(
            str(e) + "\n[i] 本次未写/未更新 meta.json：失败前已转换的 unit "
            "parquet 属半成品，validate_interim 在此期间会红（预期）；修复"
            "输入后重跑，成功的全量跑会按 R1 清扫/覆盖它们")
    # worker 已把 parquet 写入 out（读写全并行）；此处只装配 meta 并清扫（R1）。
    meta = _finalize_meta(units, out, root, dataset_id)
    dt = time.perf_counter() - t0
    print(f"[OK] sim_loader: {dataset_id} {len(units)} units → {out} "
          f"(unit_*.parquet + meta.json)  [{dt:.2f}s, workers={n_workers}]")
    if quarantined:
        print(f"[i] 另有 {len(quarantined)} 个非 §1.2 命名杂件被搁置未转"
              f"（见上方 [WARN]）：{', '.join(p.name for p in quarantined)}")
    if verify:
        _verify_roundtrip(meta, out, root, dataset_id, lenient=lenient, q0=q0)
    print("[i] 下一步：python scripts/validate_interim.py --root . --dataset "
          f"{dataset_id} ；验收图 python scripts/plot_sim_loader_acceptance.py "
          f"--dataset {dataset_id}")
    return meta


def _finalize_meta(units, out_dir, root, dataset_id):
    out_dir = _guard_write(out_dir, root)
    meta = build_meta(dataset_id, units, root=root)
    registered = {u["file"] for u in units.values()}
    for p in sorted(Path(out_dir).glob("*.parquet")):
        if p.name not in registered:
            p.unlink()
            print(f"[i] 清除未登记 parquet：{p.name}（R1）")
    (Path(out_dir) / "meta.json").write_text(
        json.dumps(_py(meta), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return meta


def _verify_roundtrip(meta, out_dir, root, dataset_id, lenient, q0):
    """--verify：写后回读，与 .mat 重导帧逐位对表并打印 sha256（确定性证词）。"""
    print("[i] --verify：写后回读逐位复核 …")
    for uid, unit in sorted(meta["units"].items()):
        src = unit["extra"]["source_mat"]
        got = pd.read_parquet(Path(out_dir) / unit["file"])
        _, _, want = convert_one(src, dataset_id, out_dir, root=root,
                                 lenient=lenient, q0=q0, write=False)
        if list(got.columns) != list(want.columns):
            raise ContractViolation(f"--verify [{uid}] 列序漂移")
        for c in want.columns:
            a, b = got[c].to_numpy(), want[c].to_numpy()
            same = (np.array_equal(a, b) or
                    bool(np.all((a == b) | (np.isnan(a.astype("float64"))
                                            & np.isnan(b.astype("float64"))))))
            if not same:
                raise ContractViolation(f"--verify [{uid}] 列 {c} 回读不一致")
        print(f"[OK]   {uid}: 逐位一致；sha256("
              f"{unit['file']})={_sha256(Path(out_dir) / unit['file'])[:16]}…")


# =====================================================================
# --selftest：零数据零痕迹自检（合成样本走全部纯函数路；沙箱可跑）
# =====================================================================
def _synth_sample(line, n_orb=2, censored=False):
    """契约同构合成样本（不代表任何真机数值；仅供逻辑自证与门禁）。"""
    n = n_orb * BEATS_PER_ORBIT + 1
    t_s = np.arange(n) * DT01
    t_days = t_s / 86400.0
    flag = np.zeros(n, np.int8)
    for k in range(n_orb):
        a, b = int((2950 + k * P_ORBIT) / DT01), int((5380 + k * P_ORBIT) / DT01)
        flag[a:b] = 2
    env = {"eclipse_flag": flag, "beta_deg": np.full(n, 30.0, np.float32)}
    if line == "rwa":
        wear = np.linspace(0.0, 2.5, n)
        mf = 0.009 + 0.02 * wear / 2.5
        tel = {"speed_rpm": np.full(n, 2000.0, np.float32),
               "motor_current_a": np.full(n, 0.2, np.float32),
               "cmd_torque_nm": np.full(n, 0.01, np.float32),
               "bearing_temp_c": np.full(n, 40.0, np.float32)}
        tru = {"friction_torque_nm": _f32(mf), "wear_volume_mm3": _f32(wear),
               "lube_level": _f32(np.linspace(1.0, 0.9, n))}
        tf_s = None if censored else float(
            np.interp(2.0, wear, t_s))
        hi = np.maximum(wear / 2.0, (mf - mf[0]) / (0.045 - mf[0]))
        mode, crit = (0, "censored") if censored else (
            1, "rw_wear_fric_max_v1: 判据① V>=2 mm³（线性穿越）")
    else:
        knots = np.array([1.0, 0.90, 0.83, 0.79, 0.775][:n_orb + 1])
        # [查3]：t=kP 样本记结算后 SOH ⇒ 前向保持自结算拍换挡
        soh = np.empty(n)
        for k in range(n_orb + 1):
            j0 = k * BEATS_PER_ORBIT
            soh[j0:min(n, j0 + BEATS_PER_ORBIT)] = knots[min(k, n_orb)]
        i = np.where(flag == 2, 2.0, np.where(flag == 1, 0.0, -1.2145)) \
            .astype(np.float64)
        tel = {"voltage_v": np.full(n, 3.9, np.float32),
               "current_a": _f32(i),
               "temp_c": np.full(n, 22.0, np.float32),
               "soc": np.full(n, 0.8, np.float32)}
        tru = {"soh": _f32(soh), "lli": _f32(1.0 - soh),
               "lam": np.zeros(n, np.float32)}
        tf_s = None
        if not censored:
            kx = np.arange(n_orb + 1) * P_ORBIT
            for j in range(1, kx.size):
                if knots[j] <= BAT_FAIL_SOH:
                    tf_s = float(kx[j - 1] + (knots[j - 1] - BAT_FAIL_SOH)
                                 / (knots[j - 1] - knots[j])
                                 * (kx[j] - kx[j - 1]))
                    break
        hi = np.maximum((1.0 - soh) / (1.0 - BAT_FAIL_SOH), 0.0)
        mode, crit = ((11, "bat_soh_lin80: SOH<=0.80（逐轨结算折线线性穿越）")
                      if tf_s is not None else (0, "censored"))
    fail = (t_s >= (tf_s if tf_s is not None else np.inf)).astype(np.int8)
    rul = _f32(((tf_s if tf_s is not None else np.nan) - t_s) / 86400.0)
    yaml_txt = ("sample_id: %s_dev_S001\nseed: %d\noverrides:\n"
                "  rw.omega0_rpm: 1400\n" % (line.upper(), 1001))
    meta = dict(sample_id=f"{line.upper()}_dev_S001", config_yaml=yaml_txt,
                seed=1001, sim_model_version="sat_top-1.0 (0711-01)",
                lut_versions="{}", gmat_env_file="synthetic",
                matlab_ver="synthetic",
                created_utc="1970-01-01T00:00:00Z",
                schema_version=SIMOUT_SCHEMA)
    return {"sample_id": meta["sample_id"], "line": line, "n": n,
            "t_days": t_days, "env": env, "tel": tel, "tru": tru,
            "cycle_index": np.floor(t_s / P_ORBIT + 1e-9).astype(np.int64),
            "labels": {"rul_days": rul, "hi": _f32(hi), "fail": fail},
            "failure_time_days": (None if tf_s is None else tf_s / 86400.0),
            "failure_mode_code": mode, "criterion_text": crit,
            "meta": meta, "warns": [], "source_mat": "<selftest>"}


def selftest():                                             # pragma: no cover
    import tempfile
    ok, skip = [], []

    def _t(cond, tag):
        print(f"[{'OK' if cond else 'FAIL'}] {tag}")
        ok.append(bool(cond))

    rwa, bat = _synth_sample("rwa", 4), _synth_sample("bat", 4)
    df_r, u_r = to_timeseries_frame(rwa)
    df_b, u_b = to_timeseries_frame(bat)
    _t(list(df_r.columns) == TS_COLS["rwa"], "S1 RWA 时序列序 = 契约")
    _t(str(df_r["t"].dtype) == "float64"
       and str(df_r["rw.wear_volume_mm3"].dtype) == "float32"
       and str(df_r["label.fail"].dtype) == "int8", "S2 dtype 按 §6.2")
    _t(u_r["hi_def"] == "rw_wear_fric_max_v1"
       and u_b["hi_def"] == "bat_soh_lin80", "S3 hi_def 口径")
    cyc, u_c = to_battery_cycle_frame(bat)
    _t(len(cyc) == 4 and list(cyc.columns) == CYCLE_COLS, "S4 逐循环重建形状")
    _t(bool(np.allclose(cyc["bat.capacity_ah"],
                        Q0_AH * np.array([0.90, 0.83, 0.79, 0.775]),
                        atol=1e-6)), "S5 capacity = q0×结算 SOH（[查3]）")
    _t(u_c["failure_time"] == 2.0
       and bool(np.array_equal(cyc["label.rul"], np.float32([2, 1, 0, -1]))),
       "S6 失效循环量化整数 RUL（R6 f32 精确）")
    cen, u_cc = to_battery_cycle_frame(_synth_sample("bat", 2, censored=True))
    _t(u_cc["censored"] and u_cc["failure_time"] is None
       and bool(np.all(np.isnan(cen["label.rul"])))
       and int(cen["label.fail"].sum()) == 0, "S7 删失口径（R6/R7）")
    meta = build_meta("SIM_dev",
                      {u_r["file"][5:-8]: u_r, u_b["file"][5:-8]: u_b})
    _t(meta["domain"] == "tgt" and meta["role"] == "evidence"
       and meta["profile"] == "rw_telemetry" and meta["t_unit"] == "day",
       "S8 SIM_dev 数据集口径")
    priv = {c["name"] for c in meta["channels"]
            if c["visibility"] == "privileged"}
    _t(priv == {"rw.friction_torque_nm", "rw.wear_volume_mm3", "rw.lube_level",
                "bat.soh", "bat.lli", "bat.lam"}, "S9 特权列打标（R8）")
    try:
        import pyarrow  # noqa: F401
        with tempfile.TemporaryDirectory() as td:
            m = None
            frames = {rwa["sample_id"]: df_r, bat["sample_id"]: df_b}
            units = {rwa["sample_id"]: u_r, bat["sample_id"]: u_b}
            m = write_interim(frames, units, Path(td) / "SIM_dev", td,
                              "SIM_dev")
            back = pd.read_parquet(Path(td) / "SIM_dev" / u_r["file"])
            _t(bool(np.array_equal(back["rw.wear_volume_mm3"].to_numpy(),
                                   df_r["rw.wear_volume_mm3"].to_numpy())),
               "S10 parquet(zstd) 写读逐位一致")
            try:
                sys.path.insert(0, str(Path(".").resolve()))
                import scripts.validate_interim as vi
                rep = vi.validate_dataset(Path(td) / "SIM_dev", root=td)
                _t(rep.dump(), "S11 validate_interim R1–R14 全绿（R13 SKIP）")
            except ImportError:
                skip.append("S11 validate_interim 不可导入")
    except ImportError:
        skip.append("S10/S11 需要 pyarrow（真机自动覆盖）")
    try:
        import h5py  # noqa: F401
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "RWA_dev_S001.mat"
            _write_synth_mat(p, rwa)
            s2 = read_simout(p)
            _t(bool(np.array_equal(s2["tru"]["wear_volume_mm3"],
                                   _f32(rwa["tru"]["wear_volume_mm3"]))),
               "S12 真 HDF5 (1,N)/uint8 布局往返")
    except ImportError:
        skip.append("S12 需要 h5py（真机自动覆盖）")
    for s in skip:
        print(f"[SKIP] {s}")
    n_ok = sum(ok)
    print(f"== sim_loader --selftest: {n_ok}/{len(ok)} PASS"
          + (f", {len(skip)} SKIP" if skip else "") + " ==")
    return 0 if all(ok) else 1


def _write_synth_mat(path, sample):
    """把合成 SimSample 按 AF.2 字节布局落成真 .mat（供 --selftest/tests 用）。"""
    import h5py
    g = LINES[sample["line"]]["grp"]
    u8 = lambda s: np.frombuffer(s.encode("utf-8"), np.uint8)[None, :]  # noqa
    row = lambda x, dt: np.asarray(x, dtype=dt)[None, :]                # noqa
    with h5py.File(path, "w") as f:
        f["telemetry/t_days"] = row(sample["t_days"], np.float64)
        f["telemetry/orbit/eclipse_flag"] = row(sample["env"]["eclipse_flag"],
                                                np.int8)
        f["telemetry/orbit/beta_deg"] = row(sample["env"]["beta_deg"],
                                            np.float32)
        for c, v in sample["tel"].items():
            f[f"telemetry/{g}/{c}"] = row(v, np.float32)
        # 精档：最小合规占位（单窗 1 Hz 整秒；本模块不消费，仅保 P1 结构）
        ts = np.arange(0.0, 241.0)
        f["telemetry_hi/t_s"] = row(ts, np.float64)
        f["telemetry_hi/window_id"] = row(np.ones(ts.size), np.int32)
        f["telemetry_hi/windows_index"] = np.asarray(
            [[1.0, 1.0, 0.0, 240.0]], np.float64).T
        f["telemetry_hi/orbit/eclipse_flag"] = row(np.zeros(ts.size), np.int8)
        f["telemetry_hi/orbit/beta_deg"] = row(np.full(ts.size, 30.0),
                                               np.float32)
        for c in LINES[sample["line"]]["tel"]:
            f[f"telemetry_hi/{g}/{c}"] = row(np.zeros(ts.size), np.float32)
        f["truth/cycle_index"] = row(sample["cycle_index"], np.int32)
        for c, v in sample["tru"].items():
            f[f"truth/{g}/{c}"] = row(v, np.float32)
        f["labels/rul_days"] = row(sample["labels"]["rul_days"], np.float32)
        f["labels/hi"] = row(sample["labels"]["hi"], np.float32)
        f["labels/fail"] = row(sample["labels"]["fail"], np.int8)
        tf = sample["failure_time_days"]
        f["labels"].attrs["failure_time_days"] = float(
            tf if tf is not None else np.nan)
        f["labels"].attrs["failure_mode_code"] = np.int32(
            sample["failure_mode_code"])
        f["labels"].attrs["criterion_text"] = np.frombuffer(
            sample["criterion_text"].encode("utf-8"), np.uint8)
        for k, v in sample["meta"].items():
            f[f"meta/{k}"] = (np.int32([[v]]) if k == "seed" else u8(v))
        f["meta/meta_json"] = u8(json.dumps(sample["meta"],
                                            ensure_ascii=False))


# =====================================================================
# CLI
# =====================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="0711-03 sim_loader：§simout .mat → interim parquet"
                    "（契约见文件头；--selftest 零数据自证）")
    ap.add_argument("--dataset", default=None, choices=sorted(DATASETS),
                    help="目标数据集（SIM_dev/SIM_rwa/SIM_bat）")
    ap.add_argument("--dev", action="store_true",
                    help="等价 --dataset SIM_dev（0711-03 日程字面出口）")
    ap.add_argument("--root", default=".", help="仓库根（默认当前目录）")
    ap.add_argument("--raw-dir", default=None,
                    help="覆盖输入目录（默认按 dataset 的 §1.5/AE.6 落地目录）")
    ap.add_argument("--out", default=None,
                    help="覆盖输出目录（默认 data/interim/<dataset>）")
    ap.add_argument("--files", nargs="*", default=None,
                    help="只转指定 .mat（默认输入目录全量；显式指定绕过 §1.2 "
                         "文件名闸，内容仍按契约硬校验）")
    ap.add_argument("--workers", default="auto",
                    help="并行 worker 数（auto=min(样本数,CPU)；1=串行）")
    ap.add_argument("--lenient", action="store_true",
                    help="装载预检降级为警告（仅排障用；正式路禁用）")
    ap.add_argument("--verify", action="store_true",
                    help="写后回读逐位复核 + 打印 parquet sha256")
    ap.add_argument("--selftest", action="store_true",
                    help="零数据零痕迹自检（临时目录，不碰 data/ 与 results/）")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    dataset = a.dataset or ("SIM_dev" if a.dev else None)
    if dataset is None:
        ap.error("需要 --dev 或 --dataset（或 --selftest）")
    workers = a.workers if a.workers == "auto" else int(a.workers)
    try:
        run(dataset, files=a.files, root=a.root, raw_dir=a.raw_dir,
            out_dir=a.out, workers=workers, lenient=a.lenient,
            verify=a.verify)
    except (LoaderUnavailable, ContractViolation, PermissionError) as e:
        print(f"[FAIL] {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
