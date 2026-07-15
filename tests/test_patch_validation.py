from code_intelligence_agent.tools.patch_validation import (
    allow_signature_change_for_rules,
    validate_function_patch,
    validate_module_patch,
)


def test_patch_validation_rejects_scope_expanding_patch():
    original = "def f(value):\n    return value + 1\n"
    fixed = (
        "def f(value):\n"
        "    return value + 1\n\n"
        "def injected():\n"
        "    return 0\n"
    )

    validation = validate_function_patch(original, fixed)

    assert validation.valid is False
    assert "invalid_python_ast" in validation.reasons


def test_patch_validation_rejects_decorator_removal():
    original = "@cached\n" "def f(value):\n" "    return value + 1\n"
    fixed = "def f(value):\n    return value + 1\n"

    validation = validate_function_patch(original, fixed)

    assert validation.valid is False
    assert "decorator_changed" in validation.reasons


def test_patch_validation_rejects_unapproved_signature_change():
    original = "def f(value):\n    return value + 1\n"
    fixed = "def f(value, default=0):\n    return value + default\n"

    validation = validate_function_patch(original, fixed)

    assert validation.valid is False
    assert "signature_changed" in validation.reasons


def test_patch_validation_allows_rule_required_signature_change():
    original = (
        "def append_item(items=[]):\n"
        "    items.append(1)\n"
        "    return items\n"
    )
    fixed = (
        "def append_item(items=None):\n"
        "    if items is None:\n"
        "        items = []\n"
        "    items.append(1)\n"
        "    return items\n"
    )

    validation = validate_function_patch(
        original,
        fixed,
        allow_signature_change=allow_signature_change_for_rules(
            ["mutable_default_arg"]
        ),
    )

    assert validation.valid is True
    assert validation.signature_changed is True
    assert validation.signature_change_allowed is True
    assert validation.changed_lines > 0


def test_module_patch_validation_rejects_existing_method_signature_change():
    original = (
        "ENABLED = False\n\n"
        "class Service:\n"
        "    def run(self, value):\n"
        "        return value\n"
    )
    fixed = (
        "ENABLED = True\n\n"
        "class Service:\n"
        "    def run(self, value, mode='safe'):\n"
        "        return value\n"
    )

    validation = validate_module_patch(original, fixed)

    assert validation.valid is False
    assert validation.signature_changed is True
    assert "signature_changed" in validation.reasons


def test_module_patch_validation_rejects_existing_callable_removal():
    original = "def public_api(value):\n    return value\n\nENABLED = False\n"
    fixed = "ENABLED = True\n"

    validation = validate_module_patch(original, fixed)

    assert validation.valid is False
    assert "module_callable_removed" in validation.reasons
