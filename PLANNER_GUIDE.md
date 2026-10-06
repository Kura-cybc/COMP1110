# 依赖建模与时间线生成：模块说明

本文件说明 `planner/` 模块（分工：Gao Huaze，依赖建模与时间线生成）的设计、算法、输出字段与接手方式。面向三类读者：整合与界面同学、验证与实验同学、写报告的同学。

---

## 1. 这个模块解决什么问题

研究模块给出的是**一组任务**：`programmes[].steps[]`，每项含 `action`、`channel`、`deadline`、`documents`、`prerequisites`、`applies_if`、`notes` 与 `evidence`。它回答“要做什么、依据是什么”，但回答不了：

- 这些任务**按什么顺序**做？（依赖关系）
- 每项**最晚什么时候必须开始**？（倒推）
- 哪一项**拖延会整体推迟**？（浮动时间与关键路径）
- 已经有的证据**在需要时还有效吗**？（时效窗口）
- 冲突在哪里**提前暴露**，而不是事后才发现？

`planner/` 就是这一层：把任务集编译成**有序、带日期窗口、带来源、带冲突说明**的计划。它不新增事实：所有日期都来自原文（`stated`）或明确写出的规则（`inferred`），找不到就报 `unknown`。

## 2. 文件结构

| 文件 | 职责 |
| --- | --- |
| `planner/schema.py` | 规划合同：`PlanRequest`／`PlanningTask`／`PlanningAnchor`／`ValidityWindow`／`Plan`；`adapt_research_result()` 把 `ResearchResult` 转成规划任务 |
| `planner/graph.py` | 依赖图：边构建、Kahn 拓扑排序、DFS 环路列举、关键路径、最长链 |
| `planner/schedule.py` | 截止解析、正推／倒推、浮动时间、时效窗口校验、冲突分类、可行性判定 |
| `planner/ahp.py` | AHP 判断矩阵、主特征向量（幂迭代）、一致性比率、四准则打分与优先级 |
| `planner/timeline.py` | 工作顺序（就绪任务中取优先级最高）、逐项日期与解释、按周分组、Markdown 渲染 |
| `planner/cli.py` | 命令行入口：`plan --case ...` / `plan --research ... --anchors ...` |
| `planner/explain.py` | 白话层：把内部字段翻译成普通学生能读懂的句子（不新增事实、不软化冲突） |
| `planner/cases/*.json` | 三个可复现案例（正式／对照／环路反例），任务带中文名与一句话说明 |

与既有代码的关系：**只读** `models.py` 的类型，不修改研究模块；`app.py` 增加了 4 个 `/api/planner/*` 接口与 `/planner` 查看器页面；`tests/test_planner.py` 是回归测试。

### 2.1 查看结果的五种方式

按“从最快到最完整”排序，都在仓库根目录（`ExchangePrep-main`）执行：

```powershell
# 方式 1：一条命令跑完四个案例 + 测试，并把交付件写到 output\
powershell -NoProfile -ExecutionPolicy Bypass -File .\run_planner_demo.ps1

# 方式 2：单看正式案例，Markdown 直接打印在终端
.\.venv\Scripts\python.exe -m planner.cli plan --case planner\cases\hkuww_main_round_2027.json

# 方式 3：导出 JSON 与报告附录，再用编辑器／浏览器打开
.\.venv\Scripts\python.exe -m planner.cli plan --case planner\cases\hkuww_main_round_2027.json `
  --json output\timeline_main_case.json --markdown output\timeline_main_case.md
Start-Process output\timeline_main_case.md

# 方式 4：起本地网页，交互式看时间线、冲突、依赖边与来源
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000/planner ，下拉选择案例

# 方式 5：跑测试，确认行为与上面描述一致
.\.venv\Scripts\python.exe -m unittest tests.test_planner -v
```

`output/` 里会得到：`timeline_main_case.json`／`.md`（正式案例）、`timeline_valid_score.md`（对照组）、`timeline_cycle_example.md`（环路反例）、`timeline_from_research.json`（从研究模块 `sample.json` 直接生成）、`plan_for_viewer.json`（可粘贴进 `static/planner.html`）。

预期看到的关键结论（可用于自检，与第 6 节一致）：

| 案例 | 可行性 | 阻塞冲突 | 应看到的现象 |
| --- | --- | --- | --- |
| 正式案例 | `infeasible` | 1 | 雅思到期 2025-09-09 早于出发 2027-08-22，并提示最晚可用签发日期 2025-08-22 |
| 对照组 | `at_risk` | 0 | 同样的依赖图，成绩仍有效，无阻塞冲突 |
| 环路反例 | `infeasible` | 1 | 报出 `reference_letter -> shortlist -> reference_letter` 并拒绝排期 |
| 研究快照 | `infeasible` | 1 | 6 个步骤全部映射，`visa_check` 被标为无引文，样本中的相对截止被标为无法解析 |

网页查看器（`/planner`）各区块对应关系：徽章=可行性汇总，锚点表=日期来源，时间线表=倒推结果（含松动天数与关键标记），冲突表=阻塞／待核实，依赖边表=图的边，来源表=每项的原文引文。

## 3. 输入合同

### 3.1 为什么另开一份合同

`models.Step` 是“证据合同”，不含排期量：没有办理耗时、缓冲、时效窗口、锚点日期。README 第 5 节把它列为待扩展项。为了不破坏研究模块与其 48 项测试，本模块采用**附加合同**：`plan_schema_version = "1.0"`，字段缺失时记录冲突而不是报错。

### 3.2 任务字段

| 字段 | 含义 |
| --- | --- |
| `id`、`title`、`category` | 任务标识；与研究模块的 `step.id` 对齐 |
| `deadline_basis` | `fixed`（有确切日期）／`relative`（锚点＋偏移）／`none`（明确无固定截止）／`unknown` |
| `deadline`、`deadline_relative` | 二者取一；`relative` 形如 `{"anchor": "submission_deadline", "offset_days": -30}` |
| `deadline_state` | `stated`／`inferred`／`unknown`／`conflicting`，与事实状态一致 |
| `duration` | `{min_days, max_days, basis}`；**排期使用 `max_days`**（保守上界） |
| `buffer_days` | 等待／排队类任务的额外保护时间（如成绩出分、审核） |
| `priority_weight` | 该任务在 AHP 之上的额外权重（默认 1.0） |
| `validity[]` | 证据时效窗口，见 3.4 |
| `prerequisites[]` | 同项目内前置任务 ID |
| `valid_until[]` | “本任务的产出必须对某后继仍然有效”的跨任务约束 |
| `evidence[]` | `source_id` + `quote`（可附 `url`／`retrieved_at`），用于时间线溯源 |
| `origin` | `extracted`（来自研究模块）／`curated`（人工整理）／`derived`（规则推出） |

### 3.3 锚点

日期一律相对锚点表达，避免“猜出发日期”：

| 锚点 | 说明 |
| --- | --- |
| `today` | 规划基准日（通常为官网快照日期） |
| `submission_deadline` | 校内／外校申请截止 |
| `result_release` | 结果公布 |
| `acceptance_deadline` | 接受名额与押金截止 |
| `programme_start` | 外校开课（来自外校学术日历） |
| `departure` | 出发日期（**必填**，作为整个计划的地平线） |
| `return` | 回港日期 |

锚点自身带 `state`：`inferred` 锚点会参与计算，但在输出中标注为待确认。

### 3.4 时效窗口（本项目最关键的建模点）

例：雅思成绩 24 个月有效期，必须**在出发时仍然有效**。

```json
{
  "label": "IELTS Academic score validity (24 months)",
  "taken_date": "2023-09-09",
  "valid_months": 24,
  "must_cover": "departure",
  "deadline_kind": "retention"
}
```

计算：`expiry = taken_date + valid_months`（日历月，1 月 31 日 + 1 月 = 2 月 28 日）。若 `expiry < anchor(departure)` → **阻塞冲突**，并给出最晚可用的签发日期 `anchor − valid_months`。

**重要区分**（这是常见建模错误）：

- **保留型**（`retention`）：证据已经存在，问“到那一刻还有效吗” → 形成有效期校验，可产生冲突。
- **计划型**（`schedule`）：任务还没做，问“最早什么时候做才不会过期” → 只作为复核提示（`task_insufficient_lead_time`），**绝不变成截止日期**。因为考试越晚考、过期越晚，把有效期当成“必须尽早上考场”的截止日期是反的。

同理，倒推的最晚完成时间只由 **自身截止** 与 **各后继的最晚开始** 与 **规划地平线** 决定；时效窗口只做校验，不参与倒推边界。

## 4. 算法

### 4.1 依赖图

两类边：

- `prerequisite`：`A → B` 表示 A 完成前 B 不能开始（参与拓扑排序与倒推）。
- `valid_until`：`持有者 → 需求者`，表示持有者的证据／资格对需求者仍然有效（不参与拓扑排序，只做时效校验与说明）。

自环忽略；不存在的前置 ID 记为 `dangling`，输出 `unknown_predecessor` 冲突（不崩溃、不静默丢弃）。

### 4.2 环路检查与拓扑排序

Kahn 算法按**字典序**取就绪节点，保证同一输入产生完全一致的计划（评估脚本依赖这一点）。若无法输出全部节点，则对剩余子图做 DFS，列举每个简单环（用最小旋转规范化去重）并报 `dependency_cycle`，给出环路路径 `a -> b -> a`。**有环时不排出一个看似合理的顺序**，这是本模块的硬约束。

### 4.3 正推与倒推

设任务 `t` 的保守耗时 `d(t)`、缓冲 `b(t)`、规划基准日 `as_of`：

```text
正推（按拓扑序）：
  ES(t) = max(as_of, max{ EF(p) | p 是 t 的前置 })
  EF(t) = ES(t) + d(t)

倒推（按逆拓扑序）：
  LF(t) = min( 自身截止(t), min{ LS(s) | s 是 t 的后继 }, 地平线 )
  LS(t) = LF(t) − d(t) − b(t)
  浮动 float(t) = LS(t) − ES(t)
```

地平线取 `departure`（有日期时），否则取第一个有日期的锚点。

### 4.4 可行性判定

| 冲突类型 | 触发条件 | 是否阻塞 |
| --- | --- | --- |
| `dependency_cycle` | 存在环 | 是 |
| `dependency_impossible` | 前置的最早完成晚于后继的截止 | 是 |
| `validity_expires_before_departure` | 证据到期早于它必须覆盖的锚点 | 是 |
| `earliest_after_deadline` | `LS < as_of` 或 `ES > LS` | 是 |
| `unknown_anchor` | 相对截止引用了未定义或无日期的锚点 | 是 |
| `unknown_predecessor` | 前置 ID 不存在 | 是 |
| `unknown_deadline` | 无法得出任何截止 | 否（待核实） |
| `missing_duration` | `duration.basis = unknown` 且为 0 | 否（待核实） |
| `validity_unknown` | 时效窗口既无到期日也无有效期长度 | 否（待核实） |
| `task_insufficient_lead_time` | 计划型窗口的最早可用日期已过 | 否（待核实） |

整体 `feasibility`：有环或任一阻塞冲突 → `infeasible`；有软冲突或存在浮动 ≤ 7 天 → `at_risk`；否则 `feasible`。

### 4.5 关键路径与浮动时间

`float = 0` 的任务拖延会直接移动整体完成时间，构成关键路径。若没有任何任务恰好为 0（例如每个任务都还有余量），则退化为**最长依赖链**，并在计划说明中写清依据（`critical path basis`）。

### 4.6 AHP 优先级

四个准则，各自归一到 0–1：

| 准则 | 含义 | 取值 |
| --- | --- | --- |
| `impact` | 出错代价（必做 +0.25、有材料 +0.15、有时效 +0.20、易出错 +0.10，基准 0.30） | 0–1 |
| `urgency` | 离最晚开始还有多久，7 天内为 1.0，线性衰减到 120 天为 0 | 0–1 |
| `flexibility` | 浮动越小越刚性：`(30 − float)/30` | 0–1 |
| `effort` | 保守耗时，21 天饱和：`d/21` | 0–1 |

权重来自成对判断矩阵（默认：紧迫 2× 影响、3× 刚性、4× 努力……），用**幂迭代**求主特征向量，并计算一致性比率：

```text
CI = (λmax − n) / (n − 1)
CR = CI / RI(n)        RI = {3: 0.58, 4: 0.90, 5: 1.12, ...}
CR < 0.10 视为可接受
```

默认判断的实测值：`λmax = 4.0310`，`CI = 0.0103`，`CR = 0.0115`，`accepted = true`。

### 4.7 工作顺序与日期

- **工作顺序**：拓扑顺序是硬约束，优先级只在“当前就绪”的任务之间排序（就绪 = 全部前置已排）。因此优先级永远不会破坏前置关系。
- **推荐开始日**：无浮动（关键）任务取最早可开始日；有浮动的任务按优先级向前提，但不越过最晚开始日：

```text
recommended = min( ES + round(float × (1 − min(score,1.5)/1.5)), LS )
```

- **按周分组**：以推荐开始日的周一为界分周，累计 `effort_days`；若请求给了 `weekly_capacity_days`，超载的周会给该周任务追加警告。

## 5. 输出合同

`Plan`（`plan_schema_version = "1.0"`）主要字段：

| 字段 | 说明 |
| --- | --- |
| `anchors[]` | 本次使用的锚点及状态（含证据） |
| `entries[]` | 有序时间线：`order`、`title`、`category`、`earliest_start`、`latest_start`、`recommended_start`、`deadline`、`deadline_source`、`duration_days`、`buffer_days`、`float_days`、`on_critical_path`、`state`、`blocking_conflicts`、`warnings`、`documents`、`prerequisites`、`sources[]`、`priority{score,criteria,weights,rationale}` |
| `weeks[]` | 周分组与每周工作量 |
| `edges[]` | 依赖边（含 `kind` 与生成理由） |
| `critical_path[]` | 关键路径任务序列 |
| `conflicts[]` | 冲突与待核实项，阻塞项排在前 |
| `ahp` | 准则、权重、`λmax`、`CI`、`CR`、是否接受 |
| `feasibility` | `feasible`／`at_risk`／`infeasible`／`unknown` |
| `stats` | 任务数、来源覆盖率、有截止任务数、阻塞冲突数、待核实数等 |
| `notes[]` | 推导说明（含每条时效窗口的校验结论） |

`state` 取 `scheduled`／`tight`／`at_risk`／`infeasible`／`unknown`，表示“排期结论”；事实三态（`stated`／`inferred`／`unknown`）保留在输入与证据中。

## 6. 三个内置案例与结果

### 6.1 `hkuww_main_round_2027.json` — 正式案例（对应计划书里的雅思例子）

HKUWW Main Round、2027/28 第一学期、英国伙伴院校；14 个任务；基准日 2026-09-28；锚点含 `submission_deadline = 2026-12-11`、`departure = 2027-08-22`（由外校学术日历推得，标注 `inferred`）。

学生档案里存的雅思成绩是 **2023-09-09**（24 个月 → 2025-09-09 到期），而出发日是 **2027-08-22**，因此计划输出：

- `feasibility = infeasible`，1 条阻塞冲突 `validity_expires_before_departure`：到期比出发早 712 天，最晚可用签发日期 2025-08-22，需重考或补交。
- 其余 13 项任务照常给出日期：例如 HKUWW 提交 `recommended 2026-11-30 / latest 2026-12-07`（截止 2026-12-11），成绩单办理 `latest 2026-11-09`（含 10 个工作日与 4 天缓冲）。
- 来源覆盖率 1.0：14 项任务全部带 `source_id` 与原文引文。

### 6.2 `hkuww_main_round_2027_feasible.json` — 对照案例

同一路径，成绩改为 2025-07-10（24 个月 → 2027-07-10，覆盖 2027-06-20 出发）：`blocking_conflicts = 0`，`feasibility = at_risk`（部分任务浮动较短）。

### 6.3 `cycle_example.json` — 环路反例

“推荐信依赖入选名单、入选名单又依赖推荐信”：输出 `dependency_cycle`（`reference_letter -> shortlist -> reference_letter`）并拒绝排期，而不是给出一个看似合理的顺序。用于回归测试与报告中的“冲突检测”证据。

## 7. 运行与验证

```powershell
# 生成计划（Markdown 打印到终端；infeasible 时退出码 1）
.\.venv\Scripts\python.exe -m planner.cli plan --case planner\cases\hkuww_main_round_2027.json

# 导出 JSON 与报告附录
.\.venv\Scripts\python.exe -m planner.cli plan --case planner\cases\hkuww_main_round_2027.json `
  --json out\timeline.json --markdown out\timeline.md

# 规划模块回归测试（58 项）
.\.venv\Scripts\python.exe -m unittest tests.test_planner -v

# 全套测试
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## 8. 如何从研究模块接手

```python
from planner.schema import PlanningAnchor, StudentProfile, TaskSpec, DurationEstimate, adapt_research_result
from planner.timeline import build_plan, render_markdown

anchors = [
    PlanningAnchor(kind="departure", label="Departure", anchor_date=date(2027, 8, 22), state="inferred"),
    PlanningAnchor(kind="submission_deadline", label="HKUWW DDL", anchor_date=date(2026, 12, 11), state="stated"),
]
specs = {
    "host_application": TaskSpec(
        task_id="host_application",
        duration=DurationEstimate(min_days=5, max_days=12, basis="inferred_convention"),
        buffer_days=5,
    )
}
request = adapt_research_result(result, task_specs=specs, anchors=anchors, profile=StudentProfile(label="demo"))
plan = build_plan(request)
markdown = render_markdown(plan)
```

命令行等价形式：`planner.cli plan --research sample.json --anchors anchors.json [--specs specs.json] [--as-of 2026-10-01]`。

注意：研究模块不返回出发日期与外校学术日历，`--anchors` 必填；缺失时本模块报 `unknown_anchor`，不会代为猜测。

## 9. 依赖与可选交叉验证

规划核心只依赖 **Python 标准库 + pydantic**（与仓库其余部分一致），因此 `requirements.txt` 不需要新增包，克隆后可直接运行。

计划书提到“使用图／排期库”。本模块按字典序自行实现 Kahn 拓扑排序、DFS 环路列举与最长链，并用 `tests/test_planner.py::NetworkXCrossCheckTests` 与 NetworkX 做**独立交叉验证**（随机 DAG 上比对拓扑序合法性与最长链长度）。该交叉验证是**可选**的：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-planner-optional.txt
.\.venv\Scripts\python.exe -m unittest tests.test_planner -v
```

未安装 NetworkX 时这两项测试会显示 `skipped`，其余 60 项照常运行；本次离线环境中即为此状态。

## 10. 两套说法：给程序看的，和给人看的

计划输出里有**两套并行的字段**，同一件事、两种说法：

| 谁看 | 字段 | 例子 |
| --- | --- | --- |
| 程序／报告／其他模块 | `deadline_source`、`state`、`conflict.detail`、`notes` | `-30 days from HKUWW Main Round application deadline (2026-12-11)`、`infeasible`、`validity_expires_before_departure` |
| 完全不懂技术的同学 | `deadline_source_plain`、`state_plain`、`conflict.detail_plain`、`entry.blocking_plain`、`notes_plain`、`title_plain`、`action_plain` | `从「HKUWW Main Round application deadline」(2026-12-11) 往前推 30 天`、`这份计划现在还不能执行`、`材料会在需要用到它之前就过期（步骤：重考雅思）请重新考一次或重新开具这份材料……` |

规则（写在 `planner/explain.py` 的模块注释里）：

1. **只翻译，不软化**：阻塞问题仍然是阻塞，具体日期照旧出现，只是措辞变成人话。
2. **只改写，不新增**：每句白话都是已有字段的复述，不会产生新的日期或要求。
3. **任务名双语**：`PlanningTask.title_plain` / `action_plain`（中文名 + 一句话说明）与英文原 `title` 并存；网页优先显示中文，终端与报告附录也用中文名并保留 `task_id` 供追溯。

对应的界面是 `static/planner.html`（`/planner`）：页面主区域**不出现** `task_id`、状态枚举、AHP、critical path 等字眼；依赖关系写成「先完成／才能开始」，关键路径写成「如果时间很紧，先做哪一步」，AHP 写成「排序时更看重什么」。原始技术记录收在折叠区，供组员审计。

写测试时把这条当成硬约束：`tests/test_planner.py::PlainLanguageTests` 会检查每个任务的每个白话字段都存在、`detail_plain` 含处理建议、每个冲突类型都有白话说明与建议，并确保三个案例的标题都是中文。

## 11. 已知限制

- 耗时与缓冲多为惯例估计（`inferred_convention`／`inferred_similar_case`），不是官方处理时长；采用保守上界，因此计划偏早。
- 锚点需人工确认；`programme_start`、`departure` 依赖外校学术日历。
- AHP 权重取决于成对判断，换一组判断即换一组顺序；一致性比率随计划输出，但理由需在报告中说明。
- `applies_if` 只展示不自动取舍；跨项目资源冲突（同一周两场考试）未建模。
- 不做签证与移民判断，签证只被排序。
- 三个内置案例用于回归与展示，不等于验证集；`stats` 中的指标仅对当前案例成立。

## 12. 与报告章节的对应

| 报告要点 | 本模块提供的证据 |
| --- | --- |
| 算法与数据结构 | 依赖图表示（两类边）、Kahn 拓扑排序、DFS 环路列举、CPM 正推／倒推、浮动时间与关键路径 |
| 可复现性 | 字典序确定性排序；`tests/test_planner.py` 58 项测试；三个案例 JSON |
| 冲突检测 | 三类阻塞冲突（环路、依赖不可能、时效过期）+ 三类待核实项；`feasibility` 汇总 |
| 决策透明 | 每项的 `deadline_source`、`float_days`、`priority.rationale`、`sources[].quote` |
| 定量指标 | `stats.source_grounding_rate`、`blocking_conflicts`、`review_items`、`ahp_consistency_ratio` |
| 个人化 | `StudentProfile`（年级／专业／目标学期／语言证据）影响适用性与时效校验；AHP 权重可按学生调整 |
