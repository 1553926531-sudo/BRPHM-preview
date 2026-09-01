# 数据与仿真说明

## 公开数据与版本

BRPHM 使用公开跨领域退化数据进行基础训练，并用自建航天场景数据进行部件调整。可下载数据地址为 [ModelScope BRPHM-datasets](https://www.modelscope.cn/datasets/modelscope1553926531/BRPHM-datasets)。三个数据档的版本和完整性信息以 `data/dataset_versions.json` 为准：

| 数据档 | 文件数 | 字节数 | 作用 |
| --- | --- | ---: | ---: | --- |
| `BRPHM_RUL_mini` | 8 | 3,112,496 | 表结构与最小链路验收 |
| `BRPHM_RUL_standard` | 1,151 | 997,506,805 | 公开复现、仿真记录检查和重建 |
| `BRPHM_RUL_complete` | 67,539 | 107,243,597,734 | 完整数据复现 |

使用 Git LFS 下载所需档位，并按档位根目录 `MANIFEST.json` 逐文件校验 SHA-256：

```bash
git clone https://www.modelscope.cn/datasets/modelscope1553926531/BRPHM-datasets.git
cd BRPHM-datasets
git lfs pull --include="BRPHM_RUL_mini/**,BRPHM_RUL_standard/**,BRPHM_RUL_complete/**"
```

下载后按档位根目录的 `MANIFEST.json` 逐文件计算 SHA-256，并按 `data/dataset_versions.json` 中的清单校验值、文件数和字节数核对。

本说明对应的远端版本为 master 提交 `85ebba12f3ec132dc9e0ea8ae49012f57505ccf1`，三个公开数据档均可按上述方式取得与核对。

## 场景、可观测量与寿命终点

### 电池

电池场景覆盖状态健康度（SOH）、锂库存损失（LLI）和活性材料损失（LAM）的长期变化。在线可观测量包括容量、平均温度、充电时长和平均倍率。寿命终点定义为首次 `SOH <= 0.80`。部署预测只使用在预测时已经可获得的遥测，退化真值和未来标签仅用于训练、验证与回放解释。

### 反作用轮

反作用轮场景跟踪磨损、润滑和摩擦的演化。在线可观测量包括轮速、电机电流、指令力矩和轴承温度。寿命终点为磨损体积首次线性穿越 `2 mm^3`，或在 1 Hz 监督处理中轮速跟踪误差超过 `150 rpm` 且持续 `30 s`。磨损、润滑等内部状态及寿命标签不作为部署遥测。

两类场景覆盖 LEO 500/550/700 km、太阳 beta 角、热等级、负载等级和场景编号。此类上下文被保存在数据元信息中，用于可追溯性和分析，不是当前模型的外部预测输入。

## 数据结构与可见性

标准化层按单元保存 Parquet 时间序列。分区 `meta.json` 说明字段、单位、时间、可见性和标签。`telemetry` 是可观测输入候选，`label.*` 是监督目标，`privileged` 是研究参考量，不能作为部署输入。训练张量使用 `x: float32 [N,L,C]`、`y_rul: float32 [N]`，并带有单元和窗口索引。

模型实际使用的外部输入范围比数据字典更窄：电池使用容量、平均温度和充电时长；反作用轮使用转速、电机电流和轴承温度，或已聚合的 13 项规范特征。其他遥测和元信息可以保留，但不会自动替代必需通道。无法唯一确认时间、部件、单位或通道含义时，程序会停止并给出原因。

## 仿真记录重建与边界

包内 `reconstruct` 入口将已下载标准档的 `raw/sim/bat/*.mat` 和 `raw/sim/rwa/*.mat` 重新转换为 `interim/SIM_bat` 和 `interim/SIM_rwa` 的 Parquet 与元数据，并支持写后回读核对：

```bash
python -m brphm reconstruct --dataset-root /path/to/BRPHM_RUL_standard --component both --workers 1 --verify
```

该步骤的输入是公开数据档中保留的原始 MATLAB 记录。它可以复现从原始记录到规范化数据的处理过程，但不等同于重新运行最初的 MATLAB/GMAT 场景生成过程。原始场景生成涉及外部软件、许可证和星历；在相应源代码、配置和可执行环境完整纳入发布包并经实际运行验证前，本文将记录重建与原始场景再生成分开说明。

质量核对、来源、字段字典和每个单元的配置/种子/原始记录身份以已下载数据档的 `README.md`、`DEVELOPER_GUIDE.md`、`PROVENANCE.json`、`QUALITY_SUMMARY.json`、分区 `meta.json` 和 `MANIFEST.json` 为准。它们为数据来源、构建过程和完整性提供可复核依据。测评数据的具体构成和工况分布由组委会统一掌握，因此本说明仅呈现公开数据上的可复现依据；方案在统一测评中的表现以组委会的核验和认定为准。
