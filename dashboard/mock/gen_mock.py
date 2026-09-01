# -*- coding: utf-8 -*-
"""dashboard/mock/gen_mock.py —— 驾驶舱 mock 数据生成器(任务 0709-08 配套, dev-time 工具)
================================================================================
定位
  0709-08 要求驾驶舱骨架"先读 mock json"。本脚本以【固定种子】确定性地生成
  dashboard/mock/mock_results.json —— 它是骨架期 app.py 的唯一输入。
  app.py 运行期只读该 JSON(缺失时调用本模块 build_mock() 在内存重建, 绝不写盘);
  写盘只发生在开发者手工执行 `python dashboard/mock/gen_mock.py` 时,
  且只写 dashboard/mock/ 自身(驾驶舱资产目录, 不属于 data/ 或 results/, 不违反宪法②)。

与真实契约同构(后续任务零改动换真数据的底气, 逐条对齐 docs/interfaces.md):
  · 样本 ID          —— §1.2  <部件>_<轨道>_B<β>_H<退化档>_L<负载>_S<序号>
  · RUNID            —— §1.3  <路线><动作>_<线>_<MMDD>_s<种子>(取 0718, 呼应 0718-05 首批真 preds)
  · 遥测通道名        —— §6.5  rw.speed_rpm / rw.motor_current_a / rw.bearing_temp_c
                               bat.voltage_v / bat.current_a / bat.temp_c
  · labels 原值口径   —— §5.4  rul_days = Tf − t(不封顶) · hi: 0=健康基线,1=失效阈值 · fail 单调不减
  · 失效模式码        —— §5.4  1=RW 磨损超阈 / 2=RW 转速跟踪失败 / 11=BAT SOH≤80%
  · preds 列          —— §9.2  run_id,task,dataset_id,unit_id,t,split,y_true_rul,y_pred_rul,
                               p10,p50,p90,y_pred_hi,y_true_hi,domain
形态取白皮书 §2.3/§2.4 的定性物理(仅供演示, 非仿真数据):
  RW: 磨损分数 w=(t/Tf)^1.6 加速上升 → 摩擦↑ → 电机电流爬升; 轴温随 β 季节波动;
      模式 2 样本在末期叠加转速跟踪跌落。
  BAT: SOH 幂律衰减至 0.8(=HI 1.0); 母线电压随 SOH 缓降并带工况纹波。
纯标准库实现(math/random/json), 与数值栈版本零耦合。
================================================================================
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

SCHEMA_VERSION = "1.0"
SEED = 20260709  # 任务日, 固定不改 —— tests/test_dashboard_app.py 校验两次构建逐字节一致

# §6.5 遥测可见通道(骨架期每线取表 13 的三通道; 0713-08 回放 dev .mat 时同名扩展)
CHANNELS_BY_LINE = {
    "rw": ["rw.speed_rpm", "rw.motor_current_a", "rw.bearing_temp_c"],
    "bat": ["bat.voltage_v", "bat.current_a", "bat.temp_c"],
}

# §5.4 failure_mode_code 码表(节选, 判据全文以 docs/failure_criteria.md 为准)
_CRITERION = {
    1: "RW 累计磨损超阈(code 1)",
    2: "RW 转速跟踪持续失败(code 2)",
    11: "BAT SOH≤80%(code 11)",
}

# 四条 mock 样本: 2×RW + 2×BAT, 覆盖 H0/H1/H2 三档初始退化(§1.2 / DOE 表 6)
_SAMPLES = [
    dict(sample_id="RWA_LEO550_B30_H1_L2_S017", line="rw", dataset_id="SIM_rwa",
         failure_time_days=210.0, t_step_days=0.5, failure_mode_code=1,
         base_speed_rpm=3000.0, hi0=0.15),
    dict(sample_id="RWA_LEO700_B60_H0_L1_S003", line="rw", dataset_id="SIM_rwa",
         failure_time_days=290.0, t_step_days=0.5, failure_mode_code=2,
         base_speed_rpm=1500.0, hi0=0.0),
    dict(sample_id="BAT_LEO550_B30_H0_L1_S001", line="bat", dataset_id="SIM_bat",
         failure_time_days=480.0, t_step_days=1.0, failure_mode_code=11, soh0=1.00),
    dict(sample_id="BAT_LEO500_B00_H2_L3_S021", line="bat", dataset_id="SIM_bat",
         failure_time_days=300.0, t_step_days=1.0, failure_mode_code=11, soh0=0.90),
]

_RUN_BY_LINE = {"rw": "t1ft_rw_0718_s0", "bat": "t1ft_bat_0718_s0"}  # §1.3; 0718=首批真 preds 之日

_R4 = lambda x: round(float(x), 4)  # noqa: E731  统一 4 位小数, 控制 JSON 体积


def _time_axis(tf: float, step: float) -> list[float]:
    n = int(round(tf / step)) + 1
    return [_R4(i * step) for i in range(n)]


def _rw_series(spec: dict, rng: random.Random) -> tuple[dict, dict]:
    tf, hi0 = spec["failure_time_days"], spec["hi0"]
    t = _time_axis(tf, spec["t_step_days"])
    beta_period = 62.0  # β 角季节项(天), 仅定性
    speed, current, temp, hi = [], [], [], []
    for x in t:
        frac = x / tf
        w = frac ** 1.6                                   # 磨损分数, 加速上升(§2.3 恶性循环)
        season = math.sin(2.0 * math.pi * x / beta_period)
        tmp = 18.0 + 6.0 * season + 3.0 * w + rng.gauss(0.0, 0.35)
        cur = 0.25 * (1.0 + 0.9 * w) + 0.002 * (tmp - 18.0) + rng.gauss(0.0, 0.006)
        spd = spec["base_speed_rpm"] + rng.gauss(0.0, 4.0)
        if spec["failure_mode_code"] == 2 and frac > 0.94:  # 末期转速跟踪跌落
            spd -= ((frac - 0.94) / 0.06) ** 2 * 140.0 * (0.6 + 0.4 * rng.random())
        speed.append(_R4(spd))
        current.append(_R4(cur))
        temp.append(_R4(tmp))
        hi.append(_R4(min(1.0, hi0 + (1.0 - hi0) * w)))
    channels = {"rw.speed_rpm": speed, "rw.motor_current_a": current, "rw.bearing_temp_c": temp}
    return channels, {"t": t, "hi": hi}


def _bat_series(spec: dict, rng: random.Random) -> tuple[dict, dict]:
    tf, soh0 = spec["failure_time_days"], spec["soh0"]
    t = _time_axis(tf, spec["t_step_days"])
    beta_period = 62.0
    volt, curr, temp, hi = [], [], [], []
    for x in t:
        frac = x / tf
        soh = soh0 - (soh0 - 0.80) * (frac ** 1.1)        # 幂律衰减至 0.8(§6.6 失效阈值)
        h = (1.0 - soh) / 0.20                            # HI: SOH 1.0→0, 0.8→1
        season = math.sin(2.0 * math.pi * x / beta_period)
        tmp = 22.0 + 6.0 * season + 2.0 * h + rng.gauss(0.0, 0.30)
        v = 3.45 + 0.45 * (soh - 0.80) / 0.20 + 0.05 * math.sin(2.0 * math.pi * x / 30.0) \
            + rng.gauss(0.0, 0.008)
        i = 1.20 + 0.30 * math.sin(2.0 * math.pi * x / 30.0) + rng.gauss(0.0, 0.03)
        volt.append(_R4(v))
        curr.append(_R4(i))
        temp.append(_R4(tmp))
        hi.append(_R4(min(1.0, h)))
    channels = {"bat.voltage_v": volt, "bat.current_a": curr, "bat.temp_c": temp}
    return channels, {"t": t, "hi": hi}


def _labels(t: list[float], hi: list[float], tf: float) -> dict:
    # §5.4 原值口径: rul_days = Tf − t(不封顶; 封顶是 processed 层 §7.3 的事)
    rul = [_R4(tf - x) for x in t]
    fail = [1 if x >= tf else 0 for x in t]               # 失效时刻起为 1, 单调不减
    return {"hi": hi, "rul_days": rul, "fail": fail}


def _interp(x: float, xs: list[float], ys: list[float]) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            k = (x - xs[i - 1]) / (xs[i] - xs[i - 1])
            return ys[i - 1] + k * (ys[i] - ys[i - 1])
    return ys[-1]


def _preds_for(spec: dict, lab: dict, t: list[float], rng: random.Random) -> list[dict]:
    """§9.2 逐列同名的 mock 预测记录: 每样本 14 个窗末检查点(寿命 30%→95%)。
    误差随寿命进度收窄(经典行为), p10≤p50≤p90; 供 0714-07/0728-05 直接消费。"""
    tf, out = spec["failure_time_days"], []
    for j in range(14):
        frac = 0.30 + 0.05 * j
        tj = _R4(frac * tf)
        y_true = _R4(tf - tj)
        noise = rng.gauss(0.0, 0.22 * (1.0 - frac) + 0.05)
        y_pred = max(0.0, y_true * (1.0 + noise))
        half = y_true * (0.28 * (1.0 - frac) + 0.07)
        hi_t = _interp(tj, t, lab["hi"])
        out.append(dict(
            run_id=_RUN_BY_LINE[spec["line"]], task="rul", dataset_id=spec["dataset_id"],
            unit_id=spec["sample_id"], t=tj, split="val",
            y_true_rul=y_true, y_pred_rul=_R4(y_pred),
            p10=_R4(max(0.0, y_pred - half)), p50=_R4(y_pred), p90=_R4(y_pred + half),
            y_pred_hi=_R4(min(1.05, max(0.0, hi_t + rng.gauss(0.0, 0.03)))),
            y_true_hi=_R4(hi_t), domain="tgt",
        ))
    return out


def build_mock() -> dict:
    """纯函数(无 I/O): 组装整份 mock。app.py 在 JSON 缺失时调用它在内存兜底。"""
    rng = random.Random(SEED)
    samples_meta, telemetry = [], {}
    runs = {rid: dict(task="rul", scenario=line, seed=0, split="val", preds=[])
            for line, rid in _RUN_BY_LINE.items()}
    for spec in _SAMPLES:
        channels, base = (_rw_series if spec["line"] == "rw" else _bat_series)(spec, rng)
        lab = _labels(base["t"], base["hi"], spec["failure_time_days"])
        telemetry[spec["sample_id"]] = {
            "t_days": base["t"], "channels": channels, "labels": lab,
        }
        runs[_RUN_BY_LINE[spec["line"]]]["preds"] += _preds_for(spec, lab, base["t"], rng)
        samples_meta.append(dict(
            sample_id=spec["sample_id"], line=spec["line"], dataset_id=spec["dataset_id"],
            failure_time_days=spec["failure_time_days"],
            failure_mode_code=spec["failure_mode_code"],
            criterion_text=_CRITERION[spec["failure_mode_code"]],
            run_id=_RUN_BY_LINE[spec["line"]],
            n_points=len(base["t"]), t_step_days=spec["t_step_days"],
        ))
    return {
        "schema_version": SCHEMA_VERSION,
        "mock": True,
        "generated_by": "dashboard/mock/gen_mock.py (0709-08, seed=%d)" % SEED,
        "contract_refs": ["interfaces.md §1.2", "§1.3", "§5.4", "§6.5", "§9.2", "§9.5"],
        "labels_note": "labels 存原值 rul_days=Tf−t 不封顶(§5.4); 封顶是 processed 层变换(§7.3)",
        "channels_by_line": CHANNELS_BY_LINE,
        "samples": samples_meta,
        "telemetry": telemetry,
        "runs": runs,
    }


def main() -> None:
    out_path = Path(__file__).resolve().parent / "mock_results.json"
    payload = build_mock()
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    out_path.write_text(text + "\n", encoding="utf-8")
    digest = hashlib.sha256((text + "\n").encode("utf-8")).hexdigest()
    n_pts = sum(s["n_points"] for s in payload["samples"])
    n_preds = sum(len(r["preds"]) for r in payload["runs"].values())
    print("[OK] mock_results.json 已生成: %d 样本 / %d 遥测点 / %d 条 preds" %
          (len(payload["samples"]), n_pts, n_preds))
    print("[OK] sha256=%s" % digest)


if __name__ == "__main__":
    main()
