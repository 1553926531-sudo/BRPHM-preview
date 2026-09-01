# BRPHM：面向电池与反作用轮的混合迁移寿命预测方法

BRPHM（Battery and Reaction-wheel Prognostics with Hybrid Migration）是面向航天器电池和反作用轮的剩余寿命预测程序。它提供完整的 PyTorch 训练、航天数据调整、预测、仿真记录重建和浏览器操作入口。

预测默认使用已经核验的历史最佳模型组合。电池和反作用轮独立使用各自效果最好的模型，不会因重新训练而被替换。历史独立评估记录为：电池 RMSE `1.565145748990264 cycles`，反作用轮 RMSE `0.018440836607000497 days`。由于测评数据的具体构成和工况分布尚由组委会统一管理，本项目当前报告公开数据和既定独立评估设置下的可复现结果，作为默认模型的选择依据。我们期待通过组委会统一测评，结合实际测评任务对方案效果作出更全面、公正的核验，最终结果以组委会认定为准。

## 开始使用

安装依赖：

```bash
python -m pip install -r requirements-competition.txt
```

各平台启动方式：

```text
Linux / Intel macOS:  ./brphm <command> [options]
Windows PowerShell:   .\brphm.cmd <command> [options]
通用 Python:          python -m brphm <command> [options]
```

公开命令为 `{train,adapt,predict,reproduce,reconstruct,serve,verify}`。每个命令均支持英文 `--help`、`-help` 和 `-?`。不需要直接运行 `src/` 或 `scripts/` 下的文件。

```bash
# 核对默认预测模型、实现、配置和成员文件
python -m brphm verify

# 直接预测外部文件；默认使用历史最佳模型
python -m brphm predict --input ./battery.csv --component battery
python -m brphm predict --input ./reaction_wheel.csv --component reaction-wheel --time-unit second

# 完整复现：训练基础模型、航天数据调整、验证预测
python -m brphm reproduce --device auto

# 从已下载数据的原始仿真记录重建中间 Parquet
python -m brphm reconstruct --dataset-root /path/to/BRPHM_RUL_standard --component both --workers 1 --verify

# 启动浏览器驾驶舱
python -m brphm serve --address 127.0.0.1 --port 8501
```

浏览器打开 `http://127.0.0.1:8501/`。驾驶舱和命令行共用同一份默认模型、输入检查和预测服务；核对、训练、航天数据调整、预测、完整复现和仿真记录重建都有对应的受控页面操作。

## 预测输入

系统接受可唯一识别的长表和宽表。长表表达时间、遥测量和数值；宽表每一行对应一个时间点。可处理的常见格式包括分隔文本、JSON/JSONL、Excel/ODS、Parquet/Feather/Arrow、MAT/HDF5/NPY/NPZ 和受支持的归档容器。上传时可以保留工况、组件标识、轨道上下文、退化状态和标签等辅助字段，但它们不进入当前预测。

电池必须提供容量、平均温度和充电时长，并具有至少 60 个从零开始连续的循环。反作用轮必须提供转速、电机电流和轴承温度，并具有至少 30 个连续的 574 秒时间桶；也可以提供已聚合的 13 项规范特征。系统允许缺少不参与模型的字段，但当部件、时间单位、时间轴或必需遥测语义无法唯一确定时会明确拒绝，不会按列位置猜测或填补数据。

启动服务后，可从下列接口下载电池和反作用轮的空白、单窗口和多窗口范例，分别支持长表和宽表：

```text
GET /api/telemetry/examples
GET /api/telemetry/examples/<example-id>.<format>?layout=wide|long
```

详细的安装、训练、输入、输出和安全停止方式见 [复现说明](docs/reproduction.md)。数据来源、数据校验、仿真场景和重建边界见 [数据与仿真说明](docs/data_and_simulation.md)。

## 数据与仿真边界

直接预测不要求下载训练数据。公开训练和航天仿真数据可从 [ModelScope 数据集](https://www.modelscope.cn/datasets/modelscope1553926531/BRPHM-datasets) 获得，提供 `BRPHM_RUL_mini`、`BRPHM_RUL_standard` 和 `BRPHM_RUL_complete` 三个档位。当前可复核远端版本为 master 提交 `85ebba12f3ec132dc9e0ea8ae49012f57505ccf1`，覆盖三档数据。档位名称、文件数、字节数和清单校验值统一记录在 `data/dataset_versions.json`；下载后必须以所选档位的 `MANIFEST.json` 逐文件核验 SHA-256。

```bash
git clone https://www.modelscope.cn/datasets/modelscope1553926531/BRPHM-datasets.git
cd BRPHM-datasets
git lfs pull --include="BRPHM_RUL_mini/**,BRPHM_RUL_standard/**,BRPHM_RUL_complete/**"
```

本程序能将已发布的原始 `.mat` 仿真记录重建为规范化 Parquet，并进行写后回读核对。原始 MATLAB/GMAT 场景生成环境、许可证和外部星历不是本包的一部分；在它们的完整源码、配置和可执行环境闭合前，不能把记录重建表述为原始仿真器再生成。

Docker 配置已用于构建 `brphm-preview:20260831-final`，并在独立容器中完成解包、启动、接口核验、默认模型核对和 BAT/RWA 预测验收。最终提交的镜像文件位于 release 包的 `01_pytorch_docker/`，可直接导入后运行。
