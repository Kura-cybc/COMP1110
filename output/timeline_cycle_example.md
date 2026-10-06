# ExchangePrep timeline - Negative test case: circular prerequisite in a reference-letter workflow

- case: `reference-letter-cycle` / programme `demo-cycle`
- planning date (as_of): 2026-10-01
- feasibility: **infeasible**
- tasks: 4, source-grounded: 0 (0.0), blocking conflicts: 1, review items: 1
- AHP weights: {'impact': 0.2772, 'urgency': 0.4673, 'flexibility': 0.1601, 'effort': 0.0954} (CR=0.0115, accepted=True)

## Anchors

| anchor | date | state | label |
| --- | --- | --- | --- |
| `today` | 2026-10-01 | stated | Planning date |
| `submission_deadline` | 2027-03-01 | stated | Host application deadline |
| `departure` | 2027-08-22 | stated | Departure for the host country |

## Ordered plan

| # | what to do | category | recommended start | earliest start | latest start | deadline | slack | critical | driver chain | state | priority |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 向外校提交申请 (`host_application`) | host_application | - | - | - | 2027-03-01 | - |  |  | scheduled | 0.395 |
| 2 | 请老师写推荐信 (`reference_letter`) | hku_application | - | - | - | 2027-01-30 | - |  |  | infeasible | 0.509 |
| 3 | 选出想申请的学校名单 (`shortlist`) | hku_application | - | - | - | - | - |  |  | at_risk | 0.404 |
| 4 | 申请英国学生签证 (`visa_application`) | visa | - | - | - | 2027-08-01 | - |  |  | scheduled | 0.468 |

## Why each date was chosen

- `host_application`: 官网直接写明的截止日期
- `reference_letter`: 从「Host application deadline」(2027-03-01) 往前推 30 天
- `shortlist`: 官网没有写，需要你去确认
- `visa_application`: 从「Departure for the host country」(2027-08-22) 往前推 21 天

## Dependency edges

| predecessor | successor | kind | reason |
| --- | --- | --- | --- |
| `reference_letter` | `shortlist` | prerequisite | reference_letter must finish before shortlist |
| `shortlist` | `reference_letter` | prerequisite | shortlist must finish before reference_letter |
| `reference_letter` | `host_application` | prerequisite | reference_letter must finish before host_application |
| `host_application` | `visa_application` | prerequisite | host_application must finish before visa_application |

## Conflicts and review items

| blocking | kind | task | what it means | detail |
| --- | --- | --- | --- | --- |
| yes | dependency_cycle | `reference_letter` | 步骤之间的先后关系写成了循环，计划排不出来（步骤：请老师写推荐信）请确认这两步到底谁先谁后，改掉其中一条依赖关系。 | circular prerequisite chain: reference_letter -> shortlist -> reference_letter; the plan cannot be ordered until one edge is removed |
| no | unknown_deadline | `shortlist` | 找不到官方截止日期（步骤：选出想申请的学校名单）请到官网（或发邮件询问）确认截止日期，再回来更新。 | no grounded deadline: the plan places this task after its prerequisites and before the horizon, and flags it for manual confirmation |

## Sources per task

- `host_application`: no source quote attached - verify manually
- `reference_letter`: no source quote attached - verify manually
- `shortlist`: no source quote attached - verify manually
- `visa_application`: no source quote attached - verify manually

## Planner notes

- profile: synthetic profile C · Year 3 · Social Sciences · Semester 1, 2027/28
- planning horizon: 2027-08-22
- critical path basis: none
