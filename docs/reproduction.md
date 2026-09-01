# 运行、安装与复现说明

本说明覆盖安装、默认预测、训练、航天数据调整、仿真记录重建、浏览器操作和安全停止。所有公开命令支持英文 `--help`、`-help` 和 `-?`。

## 1. 环境安装

使用 Python 3.10，并在项目根目录执行：

```bash
python -m pip install -r requirements-competition.txt
```

依赖清单包含 PyTorch、数值计算、Tornado 驾驶舱和公开输入格式所需的读取库。`--device auto` 在可用时使用 CUDA，否则使用 CPU。训练、调整和预测均由 PyTorch 实现。

启动方式：

```text
Linux / Intel macOS:  ./brphm <command> [options]
Windows PowerShell:   .\brphm.cmd <command> [options]
通用 Python:          python -m brphm <command> [options]
```

先核对发布包中的默认模型：

```bash
python -m brphm verify
python -m brphm verify --json
```

若模型索引、模型成员、实现文件、配置或预处理资产不一致，核对会失败并阻止默认预测。

## 2. 直接预测

外部文件一次只能包含一种部件。省略 `--model` 时，程序始终使用已核验的历史最佳模型，不会采用临时训练结果。

```bash
python -m brphm predict --input ./battery.csv --component battery
python -m brphm predict --input ./reaction_wheel.csv --component reaction-wheel --time-unit second
```

结果写入 `results/prediction/predictions.csv`，终端同时输出结构化状态。普通数值 `time` 列必须通过 `--time-unit` 明确单位；带单位的表头可以使用 `auto`。使用 `verify` 可查看默认模型的历史独立评估记录：电池 RMSE `1.565145748990264 cycles`，反作用轮 RMSE `0.018440836607000497 days`。由于测评数据的具体构成和工况分布尚由组委会统一管理，这些记录用于核对默认模型在公开数据和既定独立评估设置下的选择依据。方案效果期待由组委会统一测评结合实际测评任务作进一步核验，最终结论以组委会认定为准。

### 输入语义和拒绝边界

系统接受宽表或长表。宽表的每行是一个时间点；长表使用时间、遥测量和数值三种语义字段，字段顺序可变。可上传 CSV、TSV、TXT、TAB、DAT、JSON、JSONL、XLSX、ODS、Parquet、Feather、Arrow、MAT、HDF5、NPY、NPZ，以及受支持的 ZIP、TAR、GZIP、7Z 和 RAR 容器。

| 部件 | 实际输入模型的通道 | 最短历史 |
| --- | --- | --- |
| 电池 | 容量、平均温度、充电时长 | 从零开始连续 60 个循环 |
| 反作用轮原始遥测 | 转速、电机电流、轴承温度 | 连续 30 个 574 秒时间桶 |
| 反作用轮已聚合输入 | 已登记的 13 项规范特征 | 连续 30 个桶 |

组件标识、工况、轨道上下文、退化状态、RUL/失效标签和其他未知列可以保留作溯源，但不会进入当前模型。缺少这些非模型字段不会拒绝输入；相反，缺少必需通道、混合部件、重复时间、非连续时间轴、不可解释的时间单位或无法唯一映射的遥测语义会返回明确原因。程序不会按列位置猜测，不会静默补列、填补时序缺口或使用标签和未来信息。

### 表格范例

启动服务后，范例接口提供六类真实生成的样表：电池和反作用轮各有空白模板、单窗口输入和多窗口输入。每类可选长表或宽表，并可下载多种格式。

```bash
python -m brphm serve --address 127.0.0.1 --port 8501
curl http://127.0.0.1:8501/api/telemetry/examples
curl -OJ 'http://127.0.0.1:8501/api/telemetry/examples/battery-single.csv?layout=wide'
curl -OJ 'http://127.0.0.1:8501/api/telemetry/examples/reaction-wheel-complete.parquet?layout=long'
```

空白模板只展示合法表头，不能直接预测。单窗口样表刚好满足一个模型窗口；多窗口样表可产生多次滚动预测。

## 3. 训练、航天数据调整和完整复现

发布包保留了经版本核验的处理后张量，供所附训练、调整和验证链路直接使用。外部公开数据下载主要用于审阅来源、校验和仿真记录重建。执行顺序为公开退化数据训练、航天数据调整、验证预测：

```bash
python -m brphm train --device auto
python -m brphm adapt --device auto
python -m brphm predict --split validation --component both --device auto

# 依次执行上述步骤；任一步失败即停止
python -m brphm reproduce --device auto
```

复现产物写入 `results/reproduction/`。它们不会覆盖默认预测模型。若要使用新生成的复现模型进行验证预测，显式传入其模型索引：

```bash
python -m brphm predict --model results/reproduction/production/manifest.json --split validation --component both --device auto
```

## 4. 数据下载和校验

公开数据地址：[ModelScope BRPHM-datasets](https://www.modelscope.cn/datasets/modelscope1553926531/BRPHM-datasets)。三档数据的版本与完整性记录如下：

| 数据档 | 文件数 | 字节数 | 用途 |
| --- | --- | ---: | ---: | --- |
| `BRPHM_RUL_mini` | 8 | 3,112,496 | 格式与最小链路验收 |
| `BRPHM_RUL_standard` | 1,151 | 997,506,805 | 公开复现、仿真记录检查和重建 |
| `BRPHM_RUL_complete` | 67,539 | 107,243,597,734 | 完整数据复现 |

下载需要 Git LFS：

```bash
git clone https://www.modelscope.cn/datasets/modelscope1553926531/BRPHM-datasets.git
cd BRPHM-datasets
git lfs pull --include="BRPHM_RUL_mini/**,BRPHM_RUL_standard/**,BRPHM_RUL_complete/**"
```

下载后按各档根目录的 `MANIFEST.json` 逐文件重算 SHA-256；本包内 `data/dataset_versions.json` 给出相应清单校验值、文件数与字节数。

本说明对应的远端版本为 master 提交 `85ebba12f3ec132dc9e0ea8ae49012f57505ccf1`，其中 mini、standard 和 complete 三档均已纳入可下载的数据版本记录。

## 5. 仿真记录重建

标准档包含可读取的原始 `.mat` 记录和规范化 Parquet。下列命令将原始记录重建到 `interim/`，并可在写入后回读核对：

```bash
python -m brphm reconstruct --dataset-root /path/to/BRPHM_RUL_standard --component both --workers 1 --verify

# 仅重建一个已下载的原始文件到隔离目录
python -m brphm reconstruct --dataset-root /path/to/BRPHM_RUL_standard --component battery --workers 1 --verify --output-root /tmp/brphm-sim-smoke --files /absolute/path/to/BAT_example.mat
```

`--workers 1` 是确定性、跨平台的默认值。该入口是原始记录到规范化记录的重建，不替代 MATLAB/GMAT 原始场景生成器。需要原始场景再生成时，必须满足相应的外部软件、许可证和星历条件；这些条件没有被伪装为包内依赖。

## 6. 浏览器驾驶舱与安全停止

```bash
python -m brphm serve --address 0.0.0.0 --port 8501
```

浏览器打开 `http://127.0.0.1:8501/`。页面中的模型核对、训练、航天数据调整、预测、完整复现、输入范例和仿真记录重建均对应本说明中的公开命令，服务端只构造受控参数，不接收任意命令文本。

建议以前台方式运行。评审结束后，在启动服务的同一终端按一次 `Ctrl+C`，等待进程结束，再访问 `http://127.0.0.1:8501/healthz` 确认服务已经停止。这只停止网页服务，不删除模型、数据或预测结果。

如需后台运行，只终止自己保存的进程号：

```bash
python -m brphm serve --address 127.0.0.1 --port 8501 >serve.log 2>&1 &
server_pid=$!
kill -TERM "$server_pid"
wait "$server_pid"
```

Windows PowerShell 可在前台按 `Ctrl+C`；若使用 `Start-Process -PassThru`，只对返回对象执行 `Stop-Process -Id $process.Id`，不要结束无关 Python 进程。
