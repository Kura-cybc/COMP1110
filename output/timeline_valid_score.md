# ExchangePrep timeline - HKUWW Main Round, Semester 1 2027/28 - same pathway, valid language score

- case: `hkuww-main-round-s1-2027-valid-score` / programme `uk-partner-s1-2027-valid`
- planning date (as_of): 2026-09-28
- feasibility: **at_risk**
- tasks: 13, source-grounded: 12 (0.9231), blocking conflicts: 0, review items: 0
- AHP weights: {'impact': 0.2772, 'urgency': 0.4673, 'flexibility': 0.1601, 'effort': 0.0954} (CR=0.0115, accepted=True)

## Anchors

| anchor | date | state | label |
| --- | --- | --- | --- |
| `today` | 2026-09-28 | stated | Planning date = official page snapshot |
| `submission_deadline` | 2026-12-11 | stated | HKUWW Main Round application deadline (S1 departure) |
| `result_release` | 2027-02-05 | stated | HKUWW Main Round result |
| `acceptance_deadline` | 2027-02-12 | stated | Accept the offer and pay the HK$2,000 deposit |
| `programme_start` | 2027-09-20 | inferred | Host Semester 1 starts (host academic calendar) |
| `departure` | 2027-06-20 | inferred | Departure for the host country (Semester 1) |

## Ordered plan

| # | what to do | category | recommended start | earliest start | latest start | deadline | slack | critical | driver chain | state | priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 先确认自己符不符合资格、能不能转学分 (`confirm_eligibility`) | hku_application | 2026-10-01 | 2026-09-28 | 2026-10-05 | 2026-10-12 | 7 |  |  | scheduled | 0.807 |
| 2 | 确认现有的雅思成绩还能用到什么时候 (`language_evidence_check`) | hku_application | 2026-10-22 | 2026-09-28 | 2026-11-06 | 2026-11-11 | 39 |  |  | scheduled | 0.579 |
| 3 | 申请 HKU 正式成绩单 (`transcript_order`) | hku_application | 2026-10-24 | 2026-09-28 | 2026-11-09 | 2026-11-27 | 42 |  | yes | scheduled | 0.561 |
| 4 | 写 100 字个人陈述、定下志愿顺序 (`personal_statement`) | hku_application | 2026-10-31 | 2026-09-28 | 2026-11-18 | 2026-12-01 | 51 |  |  | scheduled | 0.536 |
| 5 | 在 HKU 系统里提交交换申请 (`hkuww_submission`) | hku_application | 2026-11-22 | 2026-10-12 | 2026-12-07 | 2026-12-11 | 56 |  | yes | scheduled | 0.398 |
| 6 | 等 HKU 公布选拔结果 (`result_release`) | hku_application | 2026-12-26 | 2026-10-14 | 2027-01-08 | 2027-02-05 | 86 |  | yes | scheduled | 0.227 |
| 7 | 接受录取并交 HK$2,000 押金 (`accept_offer`) | offer_acceptance | 2026-12-24 | 2026-10-15 | 2027-01-09 | 2027-02-12 | 86 |  | yes | scheduled | 0.278 |
| 8 | 等国际事务处确认提名，并按外校要求操作 (`iao_nomination`) | nomination | 2026-12-30 | 2026-10-19 | 2027-01-16 | 2027-03-24 | 89 |  | yes | scheduled | 0.287 |
| 9 | 向外校提交申请 (`host_application`) | host_application | 2027-02-04 | 2026-11-18 | 2027-02-22 | 2027-05-23 | 96 |  | yes | scheduled | 0.276 |
| 10 | 等外校录取通知和录取信 (`host_offer`) | offer_acceptance | 2027-02-22 | 2026-11-30 | 2027-03-11 | 2027-07-22 | 101 |  | yes | scheduled | 0.248 |
| 11 | 申请英国学生签证 (`visa_application`) | visa | 2027-04-09 | 2027-01-14 | 2027-05-02 | 2027-05-30 | 108 |  | yes | scheduled | 0.317 |
| 12 | 安排住宿和保险 (`accommodation_insurance`) | accommodation | 2027-04-30 | 2027-01-14 | 2027-05-18 | 2027-06-06 | 124 |  |  | scheduled | 0.216 |
| 13 | 订机票、做出发前检查 (`pre_departure_travel`) | other | 2027-05-19 | 2027-02-04 | 2027-06-02 | 2027-06-10 | 118 |  | yes | scheduled | 0.175 |

## Why each date was chosen

- `confirm_eligibility`: 从「HKUWW Main Round application deadline (S1 departure)」(2026-12-11) 往前推 60 天
- `language_evidence_check`: 从「HKUWW Main Round application deadline (S1 departure)」(2026-12-11) 往前推 30 天
- `transcript_order`: 从「HKUWW Main Round application deadline (S1 departure)」(2026-12-11) 往前推 14 天
- `personal_statement`: 从「HKUWW Main Round application deadline (S1 departure)」(2026-12-11) 往前推 10 天
- `hkuww_submission`: 官网直接写明的截止日期
- `result_release`: 从「Accept the offer and pay the HK$2,000 deposit」(2027-02-12) 往前推 7 天；同时受限于：因为下一步「accept_offer」要在那之后才能开始
- `accept_offer`: 官网直接写明的截止日期；同时受限于：因为下一步「iao_nomination」要在那之后才能开始
- `iao_nomination`: 从「Host Semester 1 starts (host academic calendar)」(2027-09-20) 往前推 180 天；同时受限于：因为下一步「host_application」要在那之后才能开始
- `host_application`: 从「Host Semester 1 starts (host academic calendar)」(2027-09-20) 往前推 120 天；同时受限于：因为下一步「host_offer」要在那之后才能开始
- `host_offer`: 从「Host Semester 1 starts (host academic calendar)」(2027-09-20) 往前推 60 天；同时受限于：因为下一步「visa_application」要在那之后才能开始
- `visa_application`: 从「Departure for the host country (Semester 1)」(2027-06-20) 往前推 21 天
- `accommodation_insurance`: 从「Departure for the host country (Semester 1)」(2027-06-20) 往前推 14 天
- `pre_departure_travel`: 从「Departure for the host country (Semester 1)」(2027-06-20) 往前推 10 天

## Dependency edges

| predecessor | successor | kind | reason |
| --- | --- | --- | --- |
| `confirm_eligibility` | `hkuww_submission` | prerequisite | confirm_eligibility must finish before hkuww_submission |
| `language_evidence_check` | `hkuww_submission` | prerequisite | language_evidence_check must finish before hkuww_submission |
| `transcript_order` | `hkuww_submission` | prerequisite | transcript_order must finish before hkuww_submission |
| `personal_statement` | `hkuww_submission` | prerequisite | personal_statement must finish before hkuww_submission |
| `hkuww_submission` | `result_release` | prerequisite | hkuww_submission must finish before result_release |
| `result_release` | `accept_offer` | prerequisite | result_release must finish before accept_offer |
| `accept_offer` | `iao_nomination` | prerequisite | accept_offer must finish before iao_nomination |
| `iao_nomination` | `host_application` | prerequisite | iao_nomination must finish before host_application |
| `host_application` | `host_offer` | prerequisite | host_application must finish before host_offer |
| `host_offer` | `visa_application` | prerequisite | host_offer must finish before visa_application |
| `host_offer` | `accommodation_insurance` | prerequisite | host_offer must finish before accommodation_insurance |
| `visa_application` | `pre_departure_travel` | prerequisite | visa_application must finish before pre_departure_travel |
| `visa_application` | `pre_departure_travel` | valid_until | visa_application must still be valid for pre_departure_travel |

## Critical path

- basis: `longest_dependency_chain`
- chain: `transcript_order` -> `hkuww_submission` -> `result_release` -> `accept_offer` -> `iao_nomination` -> `host_application` -> `host_offer` -> `visa_application` -> `pre_departure_travel`

## Sources per task

- `confirm_eligibility`: [S3] "[申请前 FAQ](https://intlaffairs.hku.hk/backend/faqs/before-application/)要求先核对课程限制与学分匹配、及时提交语言证明，提交后不接受修改。"
- `language_evidence_check`: [S2] "校内准备：100-word personal statement、英语能力证明"; [S4] "Global 英语 IELTS 6.5、单项 ≥6，或 TOEFL 90 且 L22/W17/R21/S21；测试日期须 2025-01-08 或以后。"
- `transcript_order`: [S5] "正常 10 工作日，不含周末及公众假期"
- `personal_statement`: [S2] "校内准备：100-word personal statement、英语能力证明"
- `hkuww_submission`: [S1] "| Main Round：全年／S1／S2 | **2026-10-12** | **2026-12-11** | 2027-02-05 | **2027-02-12** |"
- `result_release`: [S1] "Main Round ... 结果 2027-02-05"
- `accept_offer`: [S7] "单交押金不算完成接受；学院／学系名额走其指定渠道。"
- `iao_nomination`: [S2] "接受后等待 IAO 确认提名，再按外校入口二次申请。官方概略窗口：S1／全年提名和外校申请为 2027 年 2–5 月、录取 5–8 月"
- `host_application`: [S2] "**提名不等于录取**；以外校确认收件／正式 offer 为准。"
- `host_offer`: [S2] "S1／全年提名和外校申请为 2027 年 2–5 月、录取 5–8 月"
- `visa_application`: [S9] "英国签证线索见知识库第 6.6 节；审理时间与材料须查 GOV.UK 当前页面。"
- `accommodation_insurance`: [S10] "登记支持包含资助考虑、HKU group travel insurance 及同项目学生联系；实际保险保障须另核保单。"
- `pre_departure_travel`: no source quote attached - verify manually

## Planner notes

- profile: synthetic profile B · Year 2 · Computer Science · Semester 1, 2027/28
- planning horizon: 2027-06-20
- critical path basis: longest_dependency_chain
- language_evidence_check/IELTS Academic score validity (24 months): valid until 2027-07-10, which covers Departure for the host country (Semester 1) (2027-06-20)
- visa_application must still be valid for pre_departure_travel: keep the evidence inside its validity window (usable until 2027-05-30, needed by 2027-06-10)
