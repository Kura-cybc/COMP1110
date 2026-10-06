# ExchangePrep 联网调查 Demo

一个本地网页原型：输入 HKU 学生的暑校、寒校或学期交换目标，实时发现官方网页，读取 HTML/PDF，提取每个申请步骤的入口、截止规则、材料和前置条件，并导出可交给时间线模块的 JSON。

## 运行

在 PowerShell 中进入本目录，然后执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

选择一种配置方式。请在自己电脑的终端设置密钥，不要将密钥写入源代码或提交到仓库。

### 方式 A：OpenAI 模型和内置网页搜索

```powershell
$env:EXCHANGEPREP_LLM_PROVIDER = 'openai'
$env:OPENAI_API_KEY = '你的 API Key'
```

### 方式 B：仅使用 DeepSeek Key，调用本机浏览器搜索

```powershell
$env:EXCHANGEPREP_LLM_PROVIDER = 'deepseek'
$env:DEEPSEEK_API_KEY = '你的 DeepSeek API Key'
```

DeepSeek 通过 Tool Calls 请求搜索词，本地程序使用 Edge/Chrome 打开公开搜索结果，再把实际网址返回给模型。无需第二个搜索 API Key，但电脑上需要 Edge 或 Chrome；没有这两种浏览器时可执行 `.\.venv\Scripts\python.exe -m playwright install chromium`。程序也会读取 HKU 的往届认可短期项目目录作为候选补充。**这不是 DeepSeek API 自带的联网搜索**；搜索引擎若出现验证码、封锁或结果不相关，覆盖率会下降，页面会显示警告。填入具体项目官网可提高稳定性。

如有 Brave Search API Key，可另设 `$env:BRAVE_API_KEY = '你的 Brave Search API Key'`，自动模式会优先使用它；也可把 `EXCHANGEPREP_SEARCH_PROVIDER` 设为 `browser`，强制使用 DeepSeek + 本机浏览器。

启动服务：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

在浏览器打开 `http://127.0.0.1:8000`。页面可以切换 OpenAI/DeepSeek；提交表单后可下载 `exchangeprep_research_result.json`。没有密钥时，可以点“查看离线示例”检查界面和数据格式，但该按钮不执行实时搜索。也可直接向 `POST /api/research?provider=deepseek` 发送符合 `ResearchRequest` 的 JSON；接口定义见 `models.py`，交互式 API 文档位于 `/docs`。

可选环境变量：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `EXCHANGEPREP_LLM_PROVIDER` | `openai` | `openai` 或 `deepseek` |
| `EXCHANGEPREP_SEARCH_PROVIDER` | `auto` | `openai`、`brave`、`browser` 或 `manual`；`auto` 优先 Brave，其次 OpenAI；仅有 DeepSeek Key 时使用本机浏览器 |
| `OPENAI_MODEL` | `gpt-5-mini` | OpenAI 模型名 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | DeepSeek 模型名 |

## 交接给时间线模块

页面右上角可点击 `English`／`中文` 切换界面，语言选择保存在本浏览器。新调查请求带 `language: "zh" | "en"`（省略时默认中文），模型按该语言生成说明、步骤和材料。切换按钮不会调用模型或自动翻译已有结果；要改变已生成正文的语言，选好语言后重新调查。知识库原文、原始证据引用、社媒摘要、网址和机器状态不翻译。

英文模式下，如最终结果仍含知识库补充或系统生成的中文说明，会追加一次批量翻译模型调用（可能产生额外 API 费用）。翻译仅处理显示文本，保护网址、邮箱和数字，不修改证据或排期字段；校验失败保留原文并给出提示。

`ResearchResult.programmes[].steps[]` 是核心接口。每个步骤给出：

- `category`、`title`：步骤的类别和名称；
- `action`、`channel`：具体操作与入口；
- `deadline`：固定日期、时间窗口、滚动申请、相对事件、未知或无固定日期；
- `documents`、`prerequisites`：所需材料与前置步骤；
- `evidence`：每条事实对应的来源 ID 和原文；
- `state`：`verified`、`needs_review`、`historical`、`conflicting` 或 `unknown`。

时间线模块只应将 `verified` 的日期用于自动排期；其余状态应在界面上标明并要求用户核查。**本模块不计算建议最晚开始日期**。

“待核实”不是空卡片：先联网补搜，仍未确认的操作、入口、时间和材料使用相关官网／知识库中的参考内容补充，保留来源与年度、国籍、学院等适用限制。补充内容标 `needs_review`，时间参考不自动填写 `deadline.date`，也不用于自动排期。完全没有依据时只给具体核查操作和明确标注的搜索入口，不编造申请网址、日期或必交材料。正文、材料、备注、知识库中的网址与 Markdown 链接均可点击，邮箱可打开邮件客户端。

DeepSeek 结构化提取会检查是否因输出长度而截断；JSON／Schema 解析失败时最多重新生成一次（可能增加一次 API 费用），仍失败则返回明确错误，不直接拼补或猜测缺失事实。

## 搜索策略与社媒线索

DeepSeek + 浏览器模式会先执行两组小红书检索，查询“HKU/港大学生 + 目的地 + 暑校/寒校/交换 + 申请经验”，不强制限定未来年份，以便找到往届学生的项目名与办理经验。之后模型可追加社媒检索，按具体院校查询当前年度申请页、资格、材料、日期和费用，并读取已发现网页的详情链接。检索聚焦 HKU 学生出境，避免误搜为其他学生来港大参加夏校。

HKU 往届认可目录作为候选线索供模型选择，不再用目录前几行直接覆盖模型找到的项目。各院校分别读取申请详情；初次提取后，对所有选中项目的缺失字段进行院校定向补搜。

网页读取器也会解析公开预加载数据中的 HTML 正文，例如 `window.REDUX_DATA` 中用于显示折叠区的资格和材料内容，避免删除脚本标签时漏掉这些段落；解析过程不执行网页脚本。长网页摘要按截止日期、资格、材料、费用、日期、资助和入境分别分配内容。

- `discovery_clues`：社媒搜索结果的标题、网址、摘要、平台和发现它的搜索词。摘要不代表已读到笔记全文。
- `search_log`：搜索词、用途、结果数、空结果或访问错误，可用于评价搜索覆盖率。
- 社媒正文只有实际读到后才能引用，并只用于历史经验或待核实线索；不能将它单独作为当前官方 DDL、资格或材料的已核实证据。

目前通过搜索引擎检索小红书公开可索引内容；不使用用户浏览器的登录态。仅登录后可见、未被索引、验证码或无法访问的笔记，仍可能搜不到。空结果会显示在搜索记录中，不会报告为“没有相关项目”。HKU 知识库已接入，见下节。

## 设计与限制

1. 搜索结果只是线索。Demo 会读取网页正文，要求模型提供原文证据，并用程序检查引用是否实际出现于该网页。
2. 最多读取 28 个页面、调查 1–3 个项目；模型有至多 10 次搜索/读取工具动作，另有两组小红书初始搜索和按项目分配的缺项补搜。首次运行可能耗时数分钟，模型调用量会比旧版本增加。
3. 浏览器只搜索公开网页，不登录、不自动提交申请、不绕过验证码。搜索引擎可能返回无关结果；目录候选也可能已过期。无法访问的页面、登录后内容和无法解析的动态页面会标为不可用。
4. 对未公布、已过期或互相冲突的信息保留不确定状态；不能保证覆盖所有步骤。
5. 签证规则需结合国籍、居住地及项目性质，最终以目的地政府页面为准。
6. 每次调用实时搜索和大模型 API 可能产生费用，具体按所用服务的账户计费。
7. HKU 目录补充目前只覆盖暑校/寒校候选；交换项目及各国签证官网仍依赖浏览器搜索或另配 Brave/OpenAI 搜索，不能保证穷尽。

## 运行检查

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

本地测试覆盖接口结构、证据校验、过期日期处理和数据交接。真实联网搜索与模型提取需要相应 API Key，且其结果取决于当前网页内容。

## 本地知识库（已接入）

默认读取本仓库同目录的 `hku_knowledge_base_template.md`，修改 Markdown 后下一次查询即生效，不需要重建数据库。页面可单独查询，例如“英国签证”“成绩单”“学分转换”，不需要密钥，也不触发联网。

联网调查会按目的地和办理事项检索最多 8 段知识，优先访问对应国家及 HKU 的相关公开链接（自动补充最多 8 个），与院校网页共同交给提取模块。DeepSeek 浏览器智能体也会收到知识段落与链接，可按需读其中的页面。不是每次爬取全部附录／所有国家。

- `GET /api/knowledge-base?query=英国签证`：返回匹配标题、内容和链接。
- `GET /api/knowledge-base/document`：查看完整 Markdown。
- 调查 JSON 新增 `knowledge_matches`；`sources` 用 `publisher_type=knowledge_base` 区分本地资料。
- 本地资料单独支撑的事实／日期／材料只标待核验，不能因 Markdown 写着“官网核对”就当作本次验证。学院、cohort 和往年 DDL 必须保留。
- 检测到登录网址、密码表单、401 或登录重定向时停止读取，`sources[].read_status=login_required`，界面只提供入口网址。不用用户登录态，不填写密码、不绕过验证码；403／动态页面不能确认是否需登录时仍标 unavailable。

项目显示名已改为 **ExchangePrep**，导出文件为 `exchangeprep_research_result.json`。本指南由原 README 重命名保留；原本地目录为 `ExchangePrep_webinformationcollection`，发布到 GitHub 后项目文件位于仓库根目录。旧 `PATHLOOM_LLM_PROVIDER`、`PATHLOOM_SEARCH_PROVIDER` 仍兼容，新 `EXCHANGEPREP_*` 优先。密钥环境变量不变。
