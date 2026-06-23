"""
tests/test_template_utils.py — template_utils.py: the sandboxed formula
evaluator and {{field}} substitution engine that powers message rendering
in both routers/requests.py:send_message and approvals.py stage
instructions (Workflow #9 / Messaging #3).
"""
import pytest

from template_utils import (
    safe_eval_formula,
    resolve_template_variables,
    render_template,
    FormulaError,
)


# ── safe_eval_formula: arithmetic-only sandboxed evaluator ────────────────────

class TestSafeEvalFormula:
    def test_simple_addition(self):
        assert safe_eval_formula("amount + 10", {"amount": 100}) == 110

    def test_simple_multiplication(self):
        assert safe_eval_formula("amount * 0.18", {"amount": 1000}) == 180.0

    def test_subtraction(self):
        assert safe_eval_formula("amount - tax", {"amount": 100, "tax": 18}) == 82

    def test_division(self):
        assert safe_eval_formula("amount / 4", {"amount": 100}) == 25.0

    def test_modulo(self):
        assert safe_eval_formula("amount % 3", {"amount": 10}) == 1

    def test_power(self):
        assert safe_eval_formula("base ** 2", {"base": 5}) == 25

    def test_unary_negative(self):
        assert safe_eval_formula("-amount", {"amount": 50}) == -50

    def test_unary_positive(self):
        assert safe_eval_formula("+amount", {"amount": 50}) == 50

    def test_parentheses_precedence(self):
        assert safe_eval_formula("(amount + tax) * 2", {"amount": 10, "tax": 5}) == 30

    def test_chained_derived_variable(self):
        """A formula can reference a variable produced by an earlier formula
        in the same resolution pass (see resolve_template_variables)."""
        variables = {"amount": 1000, "tax": 180}
        assert safe_eval_formula("amount + tax", variables) == 1180

    def test_unknown_variable_raises(self):
        with pytest.raises(FormulaError):
            safe_eval_formula("unknown_field * 2", {"amount": 100})

    def test_non_numeric_variable_raises(self):
        with pytest.raises(FormulaError):
            safe_eval_formula("title + 1", {"title": "Invoice"})

    def test_function_call_rejected(self):
        with pytest.raises(FormulaError):
            safe_eval_formula("abs(amount)", {"amount": -100})

    def test_attribute_access_rejected(self):
        with pytest.raises(FormulaError):
            safe_eval_formula("amount.__class__", {"amount": 100})

    def test_string_literal_rejected(self):
        with pytest.raises(FormulaError):
            safe_eval_formula("'hello'", {})

    def test_subscript_rejected(self):
        with pytest.raises(FormulaError):
            safe_eval_formula("amount[0]", {"amount": 100})

    def test_invalid_syntax_raises(self):
        with pytest.raises(FormulaError):
            safe_eval_formula("amount + * 2", {"amount": 100})

    def test_import_rejected(self):
        with pytest.raises(FormulaError):
            safe_eval_formula("__import__('os')", {})

    def test_comparison_rejected(self):
        """Comparisons aren't in the allowed BinOp/UnaryOp set, so they
        should fall through to the catch-all 'unsupported expression' raise."""
        with pytest.raises(FormulaError):
            safe_eval_formula("amount > 100", {"amount": 200})


# ── resolve_template_variables: request + metadata + derived formulas ─────────

class _FakeRequest:
    def __init__(self, amount=None, title=None, department=None,
                 request_type=None, document_name=None, document_type=None,
                 folder_path=None, request_metadata=None):
        self.amount = amount
        self.title = title
        self.department = department
        self.request_type = request_type
        self.document_name = document_name
        self.document_type = document_type
        self.folder_path = folder_path
        self.request_metadata = request_metadata


class _FakeWorkflow:
    def __init__(self, message_variables=None):
        self.message_variables = message_variables


class TestResolveTemplateVariables:
    def test_base_fields_included(self):
        req = _FakeRequest(amount=500.0, title="Invoice A", department="Finance")
        variables = resolve_template_variables(req, None)
        assert variables["amount"] == 500.0
        assert variables["title"] == "Invoice A"
        assert variables["department"] == "Finance"

    def test_none_fields_excluded(self):
        req = _FakeRequest(amount=None, title="Invoice A")
        variables = resolve_template_variables(req, None)
        assert "amount" not in variables
        assert variables["title"] == "Invoice A"

    def test_request_metadata_merged(self):
        req = _FakeRequest(title="Invoice A", request_metadata={"po_number": "PO-123", "qty": 5})
        variables = resolve_template_variables(req, None)
        assert variables["po_number"] == "PO-123"
        assert variables["qty"] == 5

    def test_non_dict_metadata_ignored(self):
        req = _FakeRequest(title="Invoice A", request_metadata="not-a-dict")
        variables = resolve_template_variables(req, None)
        assert "po_number" not in variables
        assert variables["title"] == "Invoice A"

    def test_no_workflow_skips_formulas(self):
        req = _FakeRequest(amount=1000.0)
        variables = resolve_template_variables(req, None)
        assert variables["amount"] == 1000.0
        assert len(variables) == 1

    def test_single_derived_formula(self):
        req = _FakeRequest(amount=1000.0)
        wf = _FakeWorkflow(message_variables=[{"name": "tax", "formula": "amount * 0.18"}])
        variables = resolve_template_variables(req, wf)
        assert variables["tax"] == 180.0

    def test_chained_derived_formulas_evaluate_in_order(self):
        req = _FakeRequest(amount=1000.0)
        wf = _FakeWorkflow(message_variables=[
            {"name": "tax", "formula": "amount * 0.18"},
            {"name": "total", "formula": "amount + tax"},
        ])
        variables = resolve_template_variables(req, wf)
        assert variables["tax"] == 180.0
        assert variables["total"] == 1180.0

    def test_bad_formula_skipped_silently(self):
        """A formula referencing an unknown/non-numeric variable must not
        break resolution for the rest of the request — it's just omitted."""
        req = _FakeRequest(amount=1000.0, title="Invoice A")
        wf = _FakeWorkflow(message_variables=[
            {"name": "bad", "formula": "title * 2"},
            {"name": "tax", "formula": "amount * 0.18"},
        ])
        variables = resolve_template_variables(req, wf)
        assert "bad" not in variables
        assert variables["tax"] == 180.0

    def test_formula_missing_name_or_formula_skipped(self):
        req = _FakeRequest(amount=1000.0)
        wf = _FakeWorkflow(message_variables=[
            {"name": "", "formula": "amount * 0.18"},
            {"name": "tax"},
        ])
        variables = resolve_template_variables(req, wf)
        assert "tax" not in variables

    def test_empty_message_variables_list(self):
        req = _FakeRequest(amount=1000.0)
        wf = _FakeWorkflow(message_variables=[])
        variables = resolve_template_variables(req, wf)
        assert variables["amount"] == 1000.0

    def test_metadata_can_feed_a_formula(self):
        req = _FakeRequest(amount=1000.0, request_metadata={"discount_pct": 10})
        wf = _FakeWorkflow(message_variables=[
            {"name": "discounted", "formula": "amount - (amount * discount_pct / 100)"}
        ])
        variables = resolve_template_variables(req, wf)
        assert variables["discounted"] == 900.0


# ── render_template: {{field}} substitution ────────────────────────────────────

class TestRenderTemplate:
    def test_single_placeholder(self):
        assert render_template("Hello {{name}}", {"name": "Jane"}) == "Hello Jane"

    def test_multiple_placeholders(self):
        result = render_template(
            "{{title}}: amount {{amount}}",
            {"title": "Invoice A", "amount": 500},
        )
        assert result == "Invoice A: amount 500"

    def test_unknown_placeholder_left_untouched(self):
        result = render_template("Hello {{ghost}}", {"name": "Jane"})
        assert result == "Hello {{ghost}}"

    def test_none_text_returns_none(self):
        assert render_template(None, {"name": "Jane"}) is None

    def test_empty_string_returns_empty(self):
        assert render_template("", {"name": "Jane"}) == ""

    def test_no_placeholders_returned_unchanged(self):
        assert render_template("Plain text, no placeholders.", {}) == "Plain text, no placeholders."

    def test_numeric_value_stringified(self):
        assert render_template("Total: {{total}}", {"total": 1180.0}) == "Total: 1180.0"

    def test_whitespace_inside_braces_tolerated(self):
        assert render_template("Hi {{ name }}", {"name": "Jane"}) == "Hi Jane"

    def test_repeated_placeholder_substituted_each_occurrence(self):
        result = render_template("{{x}} + {{x}} = double", {"x": 5})
        assert result == "5 + 5 = double"


# ── End-to-end: resolve + render together, the actual send_message path ───────

class TestResolveAndRenderTogether:
    def test_full_pipeline_with_derived_formula(self):
        req = _FakeRequest(amount=2000.0, title="Q3 Invoice")
        wf = _FakeWorkflow(message_variables=[{"name": "tax", "formula": "amount * 0.18"}])
        variables = resolve_template_variables(req, wf)
        rendered = render_template(
            "Reminder: {{title}} for {{amount}}, tax due {{tax}}", variables
        )
        assert rendered == "Reminder: Q3 Invoice for 2000.0, tax due 360.0"
