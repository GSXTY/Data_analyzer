# 呼吸波形查看器

面向小范围研发试用的本地 Windows 波形分析工具。CSV 和标记数据保留在使用者电脑；无需公司服务器、Codex 或云端模型即可查看、自动筛查和人工标记。

## 下载与启动

在 [Releases](https://github.com/GSXTY/Data_analyzer/releases) 下载 `RespiratoryViewer-版本-windows-x64.zip`，解压整个文件夹后双击 `RespiratoryViewer.exe`。浏览器会打开本机网页 `http://127.0.0.1:8765`。保持运行窗口打开；关闭窗口或 Ctrl+C 退出。新版发布后从同一页面下载，关闭旧版再运行新版。

Windows 试用包未做代码签名。具体操作见 [使用说明](USER_GUIDE.md)。本仓库未设置公开开源许可证；依赖仍按各自许可证分发。

## 使用流程

1. 打开CSV。默认按旧脚本估算展开重复时间戳、气压1/2减气压3，并叠加压力通道。支持通道搜索、分图/叠加、横向拖动、Ctrl+滚轮缩放和时间定位。
2. 扫描整份文件或当前区间，生成待复核候选。默认最短10秒、波动降至参考30%，可调整。
3. 正确候选点“确认”，误报点“忽略”；类型/边界可修改；漏检可手动框选补充。忽略保留记录，避免重复扫描后重新出现。
4. 选择扫描批次，导出**自动检测JSON**（不可变原结果）。人工校验后填写校验人和已检查范围，导出**人工校验JSON**（包含最新结果、自动快照与修改历史）。
5. 通过公司指定渠道交回原始CSV及两份JSON。已有相同内容CSV时只需交两份JSON；内容SHA-256用于匹配。
6. 负责人打开对应原始CSV，使用“查看收回的校验JSON”只读比较并跳转波形，不覆盖本机标记。人工校验不会自动训练程序，需要研发人员修改算法后验证。

## 数据与更新

打包版存储在 `%LOCALAPPDATA%\VVFLY\RespiratoryViewer\events.sqlite3`，与解压目录分离。升级程序不会覆盖标记。开发运行默认存储在项目 `data/`；可用 `--data-dir` 指定目录。旧浏览器标记首次打开时合并，冲突会提示。建议导出JSON备份。

每次扫描创建独立批次，记录程序/算法版本、参数、时间范围和原结果。人工变更按版本保存前后状态。人工JSON中已检查范围是校验人声明，未确认候选不视为已复核。反馈不自动上传GitHub；CSV、反馈、数据库及依赖/构建目录不纳入Git。

## 检测含义

输入是**机器总流量和总漏气**，不相减、不重建患者流量。规则读取全部原始样本，按源时间戳分秒统计流量5%～95%分位差，取3秒最大值作为波动包络，与前120秒75分位参考比较。前30秒/中断/状态变化后基线不足、有效样本不足的区间不产生流量减弱候选；压力设置突变与总漏气升高作为干扰排除。

总漏气相对基线超过 `max(10个原始单位, 基线绝对值×50%, 4×偏差MAD)` 并持续达到阈值，标为总漏气升高；时间中断与采样不足生成质量事件。

这是未经临床验证的工程筛查，不是确诊暂停/低通气，不区分中枢/阻塞、不计算AHI。单位与采样机制尚待确认；原始计时为秒级，旧脚本秒内时间是估算。显示模式不改写标记计时依据。旧脚本模式沿用第14列大于125减256及121秒断线规则，原始模式超过1秒断线。

## 开发与打包

Python 3.12，Windows x64：

```powershell
python -m pip install -r requirements.txt
python viewer.py
python viewer.py "C:\path\recording.csv"
python -m unittest discover -s tests -v
node tests/test_annotations.cjs
node tests/test_interaction.cjs
python -m pip install -r requirements-build.txt
python build_windows.py
python tests/package_smoke.py dist/RespiratoryViewer/RespiratoryViewer.exe
```

打包输出到 `release/`，包含ZIP和SHA-256。构建显式包含程序与网页资源，不打包CSV/数据库。GitHub Actions 手动运行生成下载制品；推送与 `app_version.py` 匹配的 `v版本` 标签时构建并发布预发布版。

主文件：`viewer.py` 本机服务；`detector.py` 检测；`event_store.py` 标记/历史/快照；`viewer.js` 网页交互；`annotation-model.js` 标记校验和反馈比较。

## 可选 Skill 与 MCP

`skills/respiratory-waveform` 是配套Skill；`respiratory_mcp.py` 使用官方 MCP Python SDK，以stdio连接本机查看器。可选安装 `requirements-mcp.txt`，配置示例见 `mcp-config.example.json`。查看器未启动时MCP无法工作。

支持读取当前文件与显示波形、事件列表、创建/修改Agent候选、统计和 `detect_events` 自动筛查。网页与MCP共用本机事件库，候选需人工确认。显示抽取点不能代替完整采样检测。MCP仅访问当前文件，不提供任意Python执行。隔离测试：`python tests/mcp_smoke.py`。
