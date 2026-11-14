"""The validation investigator: runs only when a plan fails validation. It finds
the cause and proposes a plan change that it has already tested.

- The first message holds the plan, the validation report and the column names.
  It does not see the mapping agent's reasoning, so it is not biased by it.
- Three tools: get_failed_rows, get_sample_values (shared with the mapping
  agent) and simulate_plan_change.
- Code checks that the proposed change is exactly one the agent simulated.
  The customer then sees the diagnosis, the change and the simulated result,
  and decides whether to apply it.
"""

import json
from dataclasses import dataclass

from salesonboard.agents.mapping import sample_values_tool
from salesonboard.agents.tool_loop import InvalidOutputError, Tool, run_agent
from salesonboard.pipeline.executor import ExecutionResult, execute
from salesonboard.pipeline.source_file import SourceFile
from salesonboard.pipeline.validator import RuleResult, validate
from salesonboard.transform_plan import Operation, StrictModel, TransformPlan

AGENT_NAME = "investigator"
MAX_FAILED_ROWS = 20
FAILING_ROWS_IN_SUMMARY = 10

SYSTEM_PROMPT = """\
You investigate why a transform plan failed validation, and propose a fix.

You get the plan, the validation report and the file's column names.

How to work:
1. Look at failing rows with get_failed_rows (raw values next to transformed
   values), and at raw values with get_sample_values.
2. Find the cause, for example a wrong date format, a wrong decimal separator,
   extra spaces or empty cells.
3. Test a fix with simulate_plan_change. It runs the plan with your change on
   the whole file and returns the new validation report. Try again if needed.
4. Propose only a change you have simulated, exactly as you simulated it.

A change is a list of operations, in the same format as in the plan:
- an operation with the id of an existing operation replaces it;
- an operation with a new id is added at the end.
Do not add or change map_column operations: the customer approved the mapping.
Use "created_by": "investigator".

"diagnosis" is one plain sentence for the customer, for example:
"Dates are day first (25/01/2025), but the plan read them month first."
If you find no fix, give your diagnosis and an empty list of operations.
"""


class PlanChange(StrictModel):
    operations: list[Operation]


class Investigation(StrictModel):
    """The agent's final answer."""

    diagnosis: str
    change: PlanChange


@dataclass
class Proposal:
    """What the customer sees on the "Fix problems" tab."""

    diagnosis: str
    change: PlanChange
    new_plan: TransformPlan | None  # None when no fix was found
    simulated_results: list[RuleResult]  # empty when no fix was found


def run_investigator(
    batch_id: str,
    tenant_id: str,
    source: SourceFile,
    plan: TransformPlan,
    client=None,
) -> Proposal:
    """Investigate a failed plan and return a tested proposal.

    client is only passed by tests, to use a fake instead of the real API.
    """
    result = execute(source.data, plan, tenant_id=tenant_id, batch_id=batch_id)
    rule_results = validate(result)

    # Every change the agent simulates is saved here, with its result,
    # so we can check the final proposal against it.
    simulations: dict[str, list[RuleResult]] = {}

    tools = [
        failed_rows_tool(source, result, rule_results),
        sample_values_tool(source),
        simulate_tool(source, plan, tenant_id, batch_id, simulations),
    ]
    agent_result = run_agent(
        batch_id=batch_id,
        agent=AGENT_NAME,
        system_prompt=SYSTEM_PROMPT,
        user_message=first_message(plan, rule_results, list(source.data.columns)),
        tools=tools,
        output_model=Investigation,
        client=client,
    )
    investigation = agent_result.output
    change = investigation.change

    if not change.operations:
        return Proposal(investigation.diagnosis, change, new_plan=None, simulated_results=[])

    mark_as_investigator(change)
    key = change.model_dump_json()
    if key not in simulations:
        raise InvalidOutputError("The investigator proposed a change it did not simulate.")

    return Proposal(
        diagnosis=investigation.diagnosis,
        change=change,
        new_plan=apply_change(plan, change),
        simulated_results=simulations[key],
    )


def apply_change(plan: TransformPlan, change: PlanChange) -> TransformPlan:
    """A new plan with the change applied. The old plan is not modified.

    Same id as an existing operation: replace it. New id: add it at the end.
    Building a new TransformPlan runs all its checks again.
    """
    operations = list(plan.operations)
    for new_operation in change.operations:
        ids = [operation.id for operation in operations]
        if new_operation.id in ids:
            operations[ids.index(new_operation.id)] = new_operation
        else:
            operations.append(new_operation)
    return TransformPlan(columns=plan.columns, operations=operations)


def mark_as_investigator(change: PlanChange):
    # Who wrote the change is a fact we know, so code sets it, not the model.
    for operation in change.operations:
        operation.created_by = AGENT_NAME


def summarize(rule_results: list[RuleResult]) -> list[dict]:
    """A short validation report for the model: counts and the first failing rows."""
    summary = []
    for rule in rule_results:
        summary.append(
            {
                "rule": rule.rule,
                "check": rule.check,
                "passed": rule.passed,
                "failing_row_count": len(rule.failing_rows),
                "first_failing_rows": rule.failing_rows[:FAILING_ROWS_IN_SUMMARY],
                "message": rule.message,
            }
        )
    return summary


def first_message(plan: TransformPlan, rule_results: list[RuleResult], columns: list[str]) -> str:
    return "\n".join(
        [
            "File columns: " + ", ".join(columns),
            "",
            "Current plan:",
            plan.model_dump_json(indent=2),
            "",
            "Validation report:",
            json.dumps(summarize(rule_results), indent=2),
        ]
    )


# --- Tools ------------------------------------------------------------------
# Each function below builds one tool. The inner function can use the
# variables of the outer one (the file, the results ...).


def failed_rows_tool(
    source: SourceFile, result: ExecutionResult, rule_results: list[RuleResult]
) -> Tool:
    """The get_failed_rows tool: shows the agent what went wrong on failing rows.

    source is the uploaded file, result is the failed plan's output, and
    rule_results says which rows failed each rule.
    """

    def get_failed_rows(rule: str, n: int = MAX_FAILED_ROWS) -> list[dict]:
        # Find the failing row numbers of the rule the agent asked for, e.g. "R2".
        failing_rows = None
        for rule_result in rule_results:
            if rule_result.rule == rule:
                failing_rows = rule_result.failing_rows
        if failing_rows is None:
            # run_tool sends this message back to the model, so it can try again.
            raise ValueError(f"No rule called {rule!r}")

        # For each failing row (at most 20), put the values from the file next
        # to the values after the plan ran. Example for a wrong date format:
        #   raw: {"Date": "25/01/2025", ...}  transformed: {"order_date": None, ...}
        rows = []
        for source_row in failing_rows[: min(n, MAX_FAILED_ROWS)]:
            # source_row starts at 1, but iloc positions start at 0.
            raw = source.data.iloc[source_row - 1].to_dict()
            # The executor's output keeps source_row as a column; pick that row.
            transformed = result.rows[result.rows["source_row"] == source_row]
            rows.append(
                {
                    "source_row": source_row,
                    "raw": raw,
                    "transformed": transformed.iloc[0].to_dict(),
                }
            )
        return rows

    # The name, description and JSON schema tell the model what the tool does
    # and which arguments to send.
    return Tool(
        name="get_failed_rows",
        description=(
            f"Up to {MAX_FAILED_ROWS} rows that fail one rule: the raw values from "
            "the file next to the values after the plan ran."
        ),
        parameters={
            "type": "object",
            "properties": {
                "rule": {"type": "string", "description": "Rule id, e.g. R2"},
                "n": {"type": "integer", "description": f"How many, at most {MAX_FAILED_ROWS}"},
            },
            "required": ["rule"],
        },
        function=get_failed_rows,
    )


def simulate_tool(
    source: SourceFile,
    plan: TransformPlan,
    tenant_id: str,
    batch_id: str,
    simulations: dict[str, list[RuleResult]],
) -> Tool:
    """The simulate_plan_change tool: lets the agent test a fix before proposing it.

    plan is the failed plan. simulations is the dict run_investigator uses to
    check the final proposal; every simulated change is saved in it.
    """

    def simulate_plan_change(operations: list[dict]) -> list[dict]:
        # Check the operations from the model. Bad ones raise a ValidationError,
        # which run_tool sends back to the model as an error message.
        change = PlanChange(operations=operations)
        mark_as_investigator(change)

        # A new plan with the change; the failed plan itself is not modified.
        new_plan = apply_change(plan, change)

        # Run the new plan on the whole file, the same way a real run would.
        # Nothing is written to the database.
        result = execute(source.data, new_plan, tenant_id=tenant_id, batch_id=batch_id)
        rule_results = validate(result)

        # Remember this change and its result. The change as JSON text is the
        # key, so the final proposal can be compared with it exactly.
        simulations[change.model_dump_json()] = rule_results

        # The agent gets the short report: pass/fail and failing row counts.
        return summarize(rule_results)

    # The name, description and JSON schema tell the model what the tool does
    # and which arguments to send.
    return Tool(
        name="simulate_plan_change",
        description=(
            "Run the plan with a change on the whole file and return the new "
            "validation report. Nothing is saved."
        ),
        parameters={
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "description": "Operations in the same format as in the plan",
                    "items": {"type": "object"},
                }
            },
            "required": ["operations"],
        },
        function=simulate_plan_change,
    )
