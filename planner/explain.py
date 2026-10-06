"""Plain-language rendering of the planner's results.

The module's JSON is written for programs: it says
``validity_expires_before_departure``, ``latest start of successor host_offer``
and ``flexibility``.  A student preparing an exchange application should never
have to read that.  This module turns the same facts into short Chinese
sentences - "什么时间、要做什么、为什么是这个时间" - while the JSON keeps the
precise wording for the report and for other modules.

Two rules:

* **Explain, do not soften.**  A blocking problem stays blocking and keeps its
  concrete dates; only the wording becomes readable.
* **Never invent.**  Every sentence is a paraphrase of a field the scheduler
  already computed.  Nothing here adds a date or a requirement.
"""

from __future__ import annotations

import re
from datetime import date

from planner.schema import Conflict, PlanningTask, ValidityWindow

#: Friendly names for the planner's internal status values.
STATE_PLAIN: dict[str, str] = {
    "scheduled": "可以按计划进行",
    "tight": "时间比较紧",
    "at_risk": "有需要确认的地方",
    "infeasible": "现在做不到，需要先解决下面的问题",
    "unknown": "日期还不确定",
}

#: Friendly names for how a deadline was obtained.
DEADLINE_PLAIN: dict[str, str] = {
    "fixed deadline from source": "官网直接写明的截止日期",
    "latest acceptable date from source": "官网写明的最晚日期",
    "no fixed deadline stated": "官网没有写固定截止日期，只要赶在出发前完成",
    "deadline unknown": "官网没有写，需要你去确认",
    "relative deadline without an anchor": "只写了先后顺序，没有具体日期，需要你去确认",
}

#: What each conflict means in everyday language.
CONFLICT_PLAIN: dict[str, str] = {
    "dependency_cycle": "步骤之间的先后关系写成了循环，计划排不出来",
    "dependency_impossible": "前一步做完时，下一步的截止时间已经过了",
    "validity_expires_before_departure": "材料会在需要用到它之前就过期",
    "deadline_passed": "截止日期已经过去",
    "earliest_after_deadline": "时间不够了：即使今天就动手也已经来不及",
    "task_insufficient_lead_time": "按现在的时间安排，这项可能要重做一次",
    "unknown_deadline": "找不到官方截止日期",
    "unknown_anchor": "缺少一个关键日期，无法倒推安排",
    "missing_duration": "不知道这项要花多久，日期只能当作参考",
    "unknown_predecessor": "引用了一个不存在的步骤",
    "validity_unknown": "官方没有写明有效期，无法判断够不够用",
    "relative_deadline_unresolved": "只写了“之后”而没有具体日期，无法换算",
}

#: What the student should do about it.
CONFLICT_ADVICE: dict[str, str] = {
    "dependency_cycle": "请确认这两步到底谁先谁后，改掉其中一条依赖关系。",
    "dependency_impossible": "请把后一步的截止日期重新确认，或者提前开始前置步骤。",
    "validity_expires_before_departure": "请重新考一次或重新开具这份材料，并确保它在出发时仍然有效。",
    "deadline_passed": "请到官网查看是否还有下一轮或补交机会。",
    "earliest_after_deadline": "请到官网确认日期是否更新；若确实来不及，需要换一个项目或推迟一年。",
    "task_insufficient_lead_time": "请尽快确认是否必须重考，并预留出考试与出分的时间。",
    "unknown_deadline": "请到官网（或发邮件询问）确认截止日期，再回来更新。",
    "unknown_anchor": "请补上缺失的关键日期（例如出发日、外校开学日）。",
    "missing_duration": "请向学长学姐或负责老师了解大致需要多久，再更新估计值。",
    "unknown_predecessor": "请把这一步的前置步骤改成真实存在的步骤。",
    "validity_unknown": "请到官网确认这份材料的有效期是多久。",
    "relative_deadline_unresolved": "请向官方确认它相对哪个事件的多少天，并换算成具体日期。",
}


def plain_state(state: str) -> str:
    return STATE_PLAIN.get(state, "需要确认")


def plain_deadline_source(source: str) -> str:
    """Rewrite a scheduler bookkeeping string as a sentence a student can read."""

    if not source:
        return "来源未知"
    if source in DEADLINE_PLAIN:
        return DEADLINE_PLAIN[source]

    text = source

    # "-30 days from HKUWW Main Round application deadline (2026-12-11)"
    match = re.match(r"^(-?\d+) days from (.+?) \((\d{4}-\d{2}-\d{2})\)$", text)
    if match:
        offset, label, when = int(match.group(1)), match.group(2), match.group(3)
        direction = "往前推" if offset < 0 else "往后推"
        return f"从「{label}」({when}) {direction} {abs(offset)} 天"

    # "...; limited by <reason>"
    if "; limited by " in text:
        head, tail = text.split("; limited by ", 1)
        return f"{plain_deadline_source(head)}；同时受限于：{plain_deadline_source(tail)}"

    # "latest start of successor host_offer"
    match = re.match(r"^latest start of successor (.+)$", text)
    if match:
        return f"因为下一步「{match.group(1)}」要在那之后才能开始"

    if text.startswith("planning horizon"):
        return "以出发日为最后的期限"

    match = re.match(r"^anchor '(.+)' undated$", text)
    if match:
        return f"「{match.group(1)}」这个关键日期还没有确定"

    match = re.match(r"^unknown anchor '(.+)'$", text)
    if match:
        return f"找不到「{match.group(1)}」这个关键日期"

    # Last resort: keep the information but mark it as internal wording.
    return text


def plain_conflict(conflict: Conflict, task_titles: dict[str, str] | None = None) -> str:
    """One or two sentences: what is wrong, then what to do."""

    what = CONFLICT_PLAIN.get(conflict.kind, "有一个需要你确认的问题")
    advice = CONFLICT_ADVICE.get(conflict.kind, "")
    where = ""
    if conflict.task_id:
        titles = task_titles or {}
        where = f"（步骤：{titles.get(conflict.task_id, conflict.task_id)}）"
    parts = [f"{what}{where}"]
    if advice:
        parts.append(advice)
    return "".join(parts)


def plain_validity(window: ValidityWindow, anchor_label: str | None = None) -> str:
    """Explain one evidence-validity window without the scheduler's jargon."""

    where = f"到{anchor_label}时" if anchor_label else "到需要用到它时"
    if window.expiry_date is not None:
        return f"{window.label}：{window.expiry_date.isoformat()} 到期，必须{where}仍然有效"
    if window.taken_date is not None and window.valid_months is not None:
        return (
            f"{window.label}：从 {window.taken_date.isoformat()} 起算 {window.valid_months} 个月，"
            f"必须{where}仍然有效"
        )
    return f"{window.label}：有效期官网没有写明，需要确认"


def plain_note(note: str) -> str:
    """Rewrite the planner's internal note strings for a non-technical reader.

    The JSON keeps the original notes (they are the audit trail); this is only
    what the student-facing view shows.
    """

    text = note.strip()

    match = re.match(r"^profile:\s*(.+)$", text)
    if match:
        return f"这份计划对应的学生情况：{match.group(1)}"

    match = re.match(r"^planning horizon:\s*(.+)$", text)
    if match:
        return f"所有准备工作必须在 {match.group(1)} 之前完成"

    if text == "no dated horizon":
        return "没有找到可以当最后期限的日期"

    match = re.match(r"^critical path basis:\s*(\S+)$", text)
    if match:
        key = match.group(1)
        if key == "zero_slack":
            return "下面这些步骤完全没有缓冲，拖一天整体就往后一天"
        if key == "longest_dependency_chain":
            return "这个案例里每一步都还有余量，所以按“前后依赖最长的一条链”来提示重点"
        return "这个案例没有可提示的重点步骤"

    match = re.match(r"^(\S+) must still be valid for (\S+):(.*)$", text)
    if match:
        return f"「{match.group(1)}」在办理「{match.group(2)}」时必须仍然有效"

    match = re.match(r"^(\S+) must still be valid at (.+)$", text)
    if match:
        return f"「{match.group(1)}」必须有效到 {match.group(2)}"

    match = re.match(r"^task (\S+): (.*)$", text)
    if match:
        return f"关于「{match.group(1)}」：{match.group(2)}"

    return text


def plain_task(task: PlanningTask) -> str:
    """One-line description of a step, for the first column of the plan table."""

    return task.action_plain or task.title_plain or task.title


def plain_case_facts(
    tasks: int,
    blocking: int,
    review: int,
    first_start: date | None,
    last_finish: date | None,
) -> list[tuple[str, str]]:
    """The few numbers worth showing a student, already in words."""

    facts: list[tuple[str, str]] = [("一共几步", f"{tasks} 步")]
    if blocking:
        facts.append(("必须先解决", f"{blocking} 个问题"))
    if review:
        facts.append(("需要去确认", f"{review} 处"))
    if first_start is not None:
        facts.append(("最早哪天开始", first_start.isoformat()))
    if last_finish is not None:
        facts.append(("最晚哪天收尾", last_finish.isoformat()))
    return facts
