"""The mapping agent: reads the profile of an uploaded file and writes the
TransformPlan (what each column means + the cleaning steps).

To keep it simple and cheap:
- The field definitions and the whole file profile go in the first message.
  The profile is small (top values only), so the model needs no tool for it.
- One tool, get_sample_values, for when the profile is not enough.
- Unclear columns get status "ambiguous" in the plan, with candidates and a
  reason. Those are the questions the customer answers on the web page.

The agent only writes the plan. The executor runs it, and the validator checks
the result.
"""

from salesonboard import schema
from salesonboard.agents.tool_loop import InvalidOutputError, Tool, run_agent
from salesonboard.pipeline.profiler import FileProfile, profile_file
from salesonboard.pipeline.source_file import SourceFile
from salesonboard.transform_plan import TransformPlan

AGENT_NAME = "mapping_agent"
MAX_SAMPLE_VALUES = 20

SYSTEM_PROMPT = """\
You map one customer's sales file to our canonical sales table.

You get the canonical fields with their definitions, and a profile of every
column in the file: type, share of empty cells, most common values, min/max,
date order, decimal separator and the number of values with extra spaces.
Call get_sample_values when the profile is not enough to decide.

Your answer is a transform plan with two parts.

1. "columns": exactly one entry for every column in the file, with a status:
   - "mapped": the column fills a canonical field. Set "target".
   - "ambiguous": it could be two or more fields and the data does not settle
     it. List the "candidates" and explain in "reason". A person decides.
   - "unmapped": sales data that no canonical field fits. Give a "reason".
   - "ignored": not needed, for example a sales rep or customer name. Give a "reason".
   Each canonical field can be the target of at most one column.

2. "operations": the steps that turn the text into clean values, in order.
   - First, one "map_column" per mapped column (source = file column, target = field).
   - After that, refer to the canonical field name in "column":
     - "trim" for a text field whose values have extra spaces.
     - "drop_rows" with "when": "empty" for a required field that has empty cells.
     - "parse_date" for every date field, with a Python strptime "format", e.g. "%d/%m/%Y".
     - "cast_number" for every numeric field. Use "decimal_separator": "," when
       the file uses decimal commas.
   Give every operation a unique id ("op1", "op2", ...), a short reason, and
   "created_by": "mapping_agent".

Rules:
- A ship, delivery or invoice date is never order_date.
- unit_price is the price per unit, excluding tax.
- If the date order is "unknown", choose the most likely format and say in the
  reason that it is a guess.
- Do not invent columns. Use the exact column names from the profile.
"""


def run_mapping_agent(batch_id: str, source: SourceFile, client=None) -> TransformPlan:
    """Profile the file, let the agent write a plan, and check it covers every column.

    client is only passed by tests, to use a fake instead of the real API.
    """
    profile = profile_file(source)

    result = run_agent(
        batch_id=batch_id,
        agent=AGENT_NAME,
        system_prompt=SYSTEM_PROMPT,
        user_message=first_message(profile),
        tools=[sample_values_tool(source)],
        output_model=TransformPlan,
        client=client,
    )
    plan = result.output

    check_every_column_has_a_decision(plan, list(source.data.columns))

    # Who wrote the plan is a fact we know, so code sets it, not the model.
    for operation in plan.operations:
        operation.created_by = AGENT_NAME
    return plan


def first_message(profile: FileProfile) -> str:
    """The canonical fields and the file profile, as text for the model."""
    lines = ["Canonical fields:"]
    for field in schema.mappable_fields():
        required = "required" if field.required else "optional"
        lines.append(f"- {field.name} ({field.type}, {required}): {field.definition}")

    lines.append("")
    lines.append("File profile:")
    lines.append(profile.model_dump_json(indent=2))
    return "\n".join(lines)


def sample_values_tool(source: SourceFile) -> Tool:
    """The get_sample_values tool, reading from this one uploaded file."""

    # A function defined inside another function can use its variables;
    # here get_sample_values can read `source`.
    def get_sample_values(column: str, n: int = MAX_SAMPLE_VALUES) -> list[str]:
        if column not in source.data.columns:
            raise ValueError(f"No column called {column!r}")
        n = min(n, MAX_SAMPLE_VALUES)

        # Distinct values exactly as written, so extra spaces stay visible.
        samples = []
        for value in source.data[column]:
            if value not in samples:
                samples.append(value)
            if len(samples) == n:
                break
        return samples

    return Tool(
        name="get_sample_values",
        description=(
            f"Up to {MAX_SAMPLE_VALUES} distinct raw values from one column, "
            "exactly as written in the file."
        ),
        parameters={
            "type": "object",
            "properties": {
                "column": {"type": "string", "description": "Column name from the profile"},
                "n": {"type": "integer", "description": f"How many, at most {MAX_SAMPLE_VALUES}"},
            },
            "required": ["column"],
        },
        function=get_sample_values,
    )


def check_every_column_has_a_decision(plan: TransformPlan, column_names: list[str]):
    """Every file column must appear in the plan, and no other columns."""
    decided = [column.source for column in plan.columns]
    missing = [name for name in column_names if name not in decided]
    unknown = [name for name in decided if name not in column_names]
    if missing or unknown:
        raise InvalidOutputError(
            f"The plan does not match the file. Missing columns: {missing}. "
            f"Columns not in the file: {unknown}."
        )
