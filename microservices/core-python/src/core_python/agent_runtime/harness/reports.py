"""Reports from handed-down tasks (delegation), as the run's model reads them. Pure."""

from __future__ import annotations

from pydantic import BaseModel

#: The tool whose result the EmployeeRun workflow fills with these reports.
WAIT_FOR_REPORTS = "wait_for_reports"
DELEGATE_TASK = "delegate_task"


class ChildReport(BaseModel):
    """One finished sub-task, as task.child_finished tells it."""

    child_task_id: str
    employee_id: str
    title: str = ""
    status: str  # completed | failed | cancelled
    stop_reason: str = ""
    output: str = ""


def render_reports(children: list[ChildReport], *, still_open: int) -> str:
    if not children and not still_open:
        return "No new reports: everything you handed down has already reported."
    lines = [f"{len(children)} handed-down task(s) reported:"] if children else []
    for c in children:
        head = f'- {c.employee_id}, "{c.title}" ({c.child_task_id}): {c.status}'
        if c.status != "completed" and c.stop_reason:
            head += f" ({c.stop_reason})"
        lines.append(head)
        if c.output.strip():
            lines.append("  " + c.output.strip().replace("\n", "\n  "))
    if still_open:
        lines.append(f"{still_open} task(s) are still working and have not reported yet.")
    return "\n".join(lines)
