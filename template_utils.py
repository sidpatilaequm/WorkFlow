"""
Lightweight, sandboxed template + derived-variable engine for messages.

Two pieces:
  - resolve_template_variables(request, workflow): builds a flat name->value
    dict from the request's own fields and its request_metadata (the
    "table data" a request was submitted with), then evaluates any
    workflow-level derived formulas (Workflow.message_variables) on top of
    it — e.g. {"name": "tax_amount", "formula": "amount * 0.18"}.
  - render_template(text, variables): replaces {{name}} placeholders in a
    string with the resolved values. Unknown placeholders are left as-is
    rather than raising, so a typo in a template doesn't break notifications.

Formulas are evaluated with a tiny AST-walking evaluator — NOT Python's
eval()/exec() — restricted to numeric literals, the four arithmetic
operators, **, unary +/-, parentheses, and variable lookups. Anything else
(function calls, attribute access, subscripts, comprehensions, etc.) raises
and the formula is skipped rather than applied.
"""
import ast
import operator
import re
from typing import Any, Optional

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_PLACEHOLDER_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


class FormulaError(Exception):
    pass


def _eval_node(node, variables: dict):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, variables)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise FormulaError(f"Unsupported constant: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise FormulaError(f"Unknown variable: {node.id}")
        val = variables[node.id]
        if not isinstance(val, (int, float)):
            raise FormulaError(f"Variable '{node.id}' is not numeric")
        return val
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        return _ALLOWED_BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand, variables))
    raise FormulaError(f"Unsupported expression: {ast.dump(node)}")


def safe_eval_formula(formula: str, variables: dict) -> float:
    """Evaluate a restricted arithmetic expression against `variables`."""
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"Could not parse formula '{formula}': {exc}")
    return _eval_node(tree.body if isinstance(tree, ast.Expression) else tree, variables)


_BASE_REQUEST_FIELDS = (
    "amount", "title", "department", "request_type",
    "document_name", "document_type", "folder_path",
)


def resolve_template_variables(request, workflow) -> dict:
    """
    Build the variable namespace available to message templates:
      - base request fields (amount, title, department, ...)
      - every key in request.request_metadata (the submitted "table data")
      - derived variables from workflow.message_variables, evaluated in
        order so later formulas may reference earlier derived names.
    Non-numeric / missing values are kept (for plain {{field}} substitution)
    but excluded from formula evaluation (which requires numeric operands).
    """
    variables: dict = {}
    for field in _BASE_REQUEST_FIELDS:
        val = getattr(request, field, None)
        if val is not None:
            variables[field] = val

    metadata = getattr(request, "request_metadata", None)
    if isinstance(metadata, dict):
        variables.update(metadata)

    formulas = getattr(workflow, "message_variables", None) if workflow else None
    if formulas:
        for spec in formulas:
            name = spec.get("name")
            formula = spec.get("formula")
            if not name or not formula:
                continue
            try:
                variables[name] = safe_eval_formula(formula, variables)
            except FormulaError:
                # Skip silently — a bad/incompatible formula should never
                # break message sending, just omit that one derived value.
                continue
    return variables


def render_template(text: Optional[str], variables: dict) -> Optional[str]:
    """Replace {{name}} placeholders in `text` using `variables`. Unknown
    placeholders are left untouched rather than raising."""
    if not text:
        return text

    def _sub(match: "re.Match") -> str:
        name = match.group(1)
        if name in variables:
            return str(variables[name])
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_sub, text)
