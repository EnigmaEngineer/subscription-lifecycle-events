"""The Snowflake module, and the attributes the provider calls optional.

Same two sided shape as the connector rules. Each one passes on the committed
configuration and refuses a planted defect that is still valid HCL.
"""

from subevents import warehouse_rules as rules


def test_the_module_reads():
    parts = rules.read_all()
    assert set(parts) == set(rules.FILES)
    assert all(text.strip() for text in parts.values())


def test_every_rule_passes_on_the_committed_configuration():
    failures = {f.rule: f.detail for f in rules.run() if not f.ok}
    assert failures == {}


def test_every_rule_has_a_planted_defect():
    assert {rule.__name__ for rule in rules.RULES} == set(rules.PLANTED)


def test_every_planted_defect_is_refused():
    base = rules.read_all()
    missed = {}
    for name, (filename, mutate) in rules.PLANTED.items():
        parts = dict(base)
        parts[filename] = mutate(base[filename])
        finding = [f for f in rules.run(parts) if f.rule == name][0]
        if finding.ok:
            missed[name] = finding.detail
    assert missed == {}


def test_a_planted_defect_actually_changes_the_file():
    """A mutation that matches nothing leaves the file alone and the rule passes.

    Then the test above reads as coverage of a rule that was never exercised.
    """
    base = rules.read_all()
    for name, (filename, mutate) in rules.PLANTED.items():
        assert mutate(base[filename]) != base[filename], name


def test_the_block_reader_walks_past_a_nested_block():
    """A lazy match to the next closing brace stops inside on_schema_object.

    Every grant in this module has a nested block, so a reader that stopped at
    the first closing brace would return bodies with the interesting half
    missing and most rules would pass on nothing.
    """
    parts = rules.read_all()
    grants = [
        b
        for b in rules.blocks(rules.strip_comments(parts["grants.tf"]))
        if b.kind == rules.GRANT_RESOURCE
    ]
    assert len(grants) >= 9
    future = [b for b in grants if "future" in b.body]
    assert len(future) == 1
    assert "object_type_plural" in future[0].body


def test_comment_stripping_leaves_the_code():
    parts = rules.read_all()
    stripped = rules.strip_comments(parts["grants.tf"])
    assert "strict_privilege_management" in stripped
    assert "A grant left at the default" not in stripped


FIXTURE = '''
resource "snowflake_grant_privileges_to_account_role" "one" {
  account_role_name = "A"
  privileges        = ["USAGE"]

  on_schema_object {
    future {
      object_type_plural = "TABLES"
    }
  }
}

variable "two" {
  type = string
}
'''


def test_the_block_reader_returns_each_body_whole():
    """The walker is what every rule in this module rests on.

    Seven of its twelve mutation sites survived the first pass, because the
    rules kept answering the same way on bodies that were wrong. So the walker
    gets graded directly on a fixture with a known shape rather than through
    the rules that read it.
    """
    found = rules.blocks(FIXTURE)
    assert [(b.kind, b.label) for b in found] == [
        ("snowflake_grant_privileges_to_account_role", "one"),
        ("variable", "two"),
    ]
    grant, variable = found
    assert 'account_role_name = "A"' in grant.body
    assert "object_type_plural" in grant.body
    # It stopped at its own closing brace rather than running into the next
    # block, and it did not stop at the first one it met inside.
    assert "variable" not in grant.body
    assert grant.body.count("{") == grant.body.count("}")
    assert variable.body.strip() == "type = string"


def test_the_block_reader_finds_nothing_in_a_file_with_no_blocks():
    assert rules.blocks("# just a comment\n") == []


def test_a_block_is_frozen():
    """Every rule reads these objects and one that mutated them in place would
    change what the rest see. A guard about an answer nothing asks for is
    graded directly rather than deleted.
    """
    import dataclasses

    block = rules.blocks(FIXTURE)[0]
    try:
        block.kind = "something else"
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("a block has to be immutable")
