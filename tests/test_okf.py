"""Slice 2: OKF parsing, validation, index/log generation, search."""

from conftest import write

from brainforge import okf

CONCEPT = """---
type: Decision
title: Go live in March
description: The go-live date was set.
x-custom: {keep: me}
generated: { by: human:alice, at: 2026-10-01T09:00:00Z }
sources:
  - id: steerco
    resource: ../sources/steerco.md
---

Go-live is 2026-03-01.[^steerco] Owner: [Alice](../entities/alice.md).

[^steerco]: SteerCo minutes
"""


def bundle(tmp_path):
    root = tmp_path / "knowledge"
    write(root / "decisions" / "go-live.md", CONCEPT)
    write(root / "sources" / "steerco.md", "---\ntype: Source Document\ntitle: SteerCo\n---\nMinutes.\n")
    write(root / "entities" / "alice.md", "---\ntype: Person\ntitle: Alice\ntags: [team]\n---\nPM.\n")
    return root


def test_roundtrip_keeps_unknown_keys_and_timestamps(tmp_path):
    root = bundle(tmp_path)
    c = okf.read(root / "decisions" / "go-live.md")
    assert c.meta["generated"]["at"] == "2026-10-01T09:00:00Z"  # not turned into a datetime
    okf.write(c)
    again = okf.read(root / "decisions" / "go-live.md")
    assert again.meta == c.meta and again.meta["x-custom"] == {"keep": "me"}
    assert okf.validate_bundle(root) == []


def test_validation_errors(tmp_path):
    root = bundle(tmp_path)
    write(root / "topics" / "no-type.md", "---\ntitle: x\n---\nbody\n")
    write(root / "topics" / "bad-footnote.md", "---\ntype: Topic\n---\nClaim.[^nope]\n")
    write(root / "topics" / "bad-time.md", "---\ntype: Topic\ngenerated: {by: x, at: yesterday}\n---\n")
    write(root / "topics" / "no-frontmatter.md", "just text\n")
    write(root / "topics" / "index.md", "---\nokf_version: '0.2'\n---\n")
    write(root / "log.md", "# Log\n\n## last week\n")
    errors = {(i.path, i.message.split(" ")[0]) for i in okf.validate_bundle(root) if i.level == "error"}
    assert errors == {
        ("topics/no-type.md", "missing"),
        ("topics/bad-footnote.md", "footnote"),
        ("topics/bad-time.md", "'generated.at'"),
        ("topics/no-frontmatter.md", "missing"),
        ("topics/index.md", "index.md"),
        ("log.md", "log"),
    }


def test_broken_links_are_warnings_only(tmp_path):
    root = bundle(tmp_path)
    write(root / "topics" / "linky.md", "---\ntype: Topic\n---\nSee [x](../topics/missing.md).\n")
    issues = okf.validate_bundle(root)
    assert [i.level for i in issues if i.path == "topics/linky.md"] == ["warning"]


def test_indexes_are_deterministic_and_describe_concepts(tmp_path):
    root = bundle(tmp_path)
    assert okf.write_indexes(root)
    assert okf.write_indexes(root) == []  # second run changes nothing
    assert "* [Go live in March](go-live.md) - The go-live date was set." in (root / "decisions" / "index.md").read_text()
    top = (root / "index.md").read_text()
    assert top.startswith('---\nokf_version: "0.2"\n---') and "[decisions](decisions/index.md)" in top


def test_log_is_newest_first(tmp_path):
    root = tmp_path / "k"
    root.mkdir()
    okf.append_log(root, "first", date="2026-10-01")
    okf.append_log(root, "second", date="2026-10-02")
    okf.append_log(root, "third", date="2026-10-02")
    text = (root / "log.md").read_text()
    assert text.index("## 2026-10-02") < text.index("## 2026-10-01")
    assert text.index("third") < text.index("second")


def test_search_by_text_metadata_and_links(tmp_path):
    root = bundle(tmp_path)
    ids = lambda hits: [okf.concept_id(root, c.path) for _, c in hits]  # noqa: E731
    assert ids(okf.search(root, "go-live 2026")) == ["decisions/go-live"]
    assert ids(okf.search(root, type="person")) == ["entities/alice"]
    assert ids(okf.search(root, tag="team")) == ["entities/alice"]
    assert ids(okf.search(root, links_to="entities/alice")) == ["decisions/go-live"]
    assert ids(okf.search(root, linked_from="knowledge/decisions/go-live.md")) == ["entities/alice"]
    assert okf.search(root, "nothing-matches-this") == []


def test_graph_issues_find_isolated_concepts_and_likely_duplicates(tmp_path):
    k = tmp_path / "knowledge"
    write(k / "sources/doc.md", "---\ntype: Source Document\n---\n[a](../topics/a.md) [lonely](../topics/lonely.md)\n")
    write(k / "topics/a.md", "---\ntype: Topic\ntitle: Vendor B\n---\nSee [b](../entities/b.md).\n")
    write(k / "entities/b.md", "---\ntype: Organization\ntitle: vendor  b\n---\nx\n")
    write(k / "topics/lonely.md", "---\ntype: Topic\ntitle: Lonely\n---\nx\n")
    write(k / "topics/gone.md", "---\ntype: Topic\ntitle: Gone\napproval: discarded\n---\nx\n")
    found = sorted((i.path, i.message.split(":")[0]) for i in okf.graph_issues(k))
    assert found == [("entities/b.md", "same title as topics/a.md"), ("topics/a.md", "same title as entities/b.md"),
                     ("topics/lonely.md", "isolated")]
