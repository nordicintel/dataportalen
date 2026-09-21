"""The command-line interface, driven through a fake transport."""

from __future__ import annotations

import json

import pytest
from conftest import FakeTransport, load_fixture

from dataportal import cli


@pytest.fixture
def run(monkeypatch):
    """Run the CLI with a transport we control; returns (exit code, stdout)."""

    def runner(argv, transport):
        import dataportal.cli as cli_module

        original = cli_module.Dataportal

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            kwargs.setdefault("max_retries", 0)
            return original(*args, **kwargs)

        monkeypatch.setattr(cli_module, "Dataportal", factory)
        return cli.main(argv)

    return runner


def test_search_prints_a_table(run, capsys):
    transport = FakeTransport()
    transport.push(load_fixture("search_datasets.json"))
    assert run(["search", "cykel", "--limit", "2"], transport) == 0
    out = capsys.readouterr().out
    assert "matching datasets" in out
    assert "title" in out


def test_search_json_prints_the_raw_response(run, capsys):
    transport = FakeTransport()
    payload = load_fixture("search_datasets.json")
    transport.push(payload)
    assert run(["--json", "search", "--limit", "1"], transport) == 0
    assert json.loads(capsys.readouterr().out)["results"] == payload["results"]


def test_search_passes_filters_through(run, capsys):
    transport = FakeTransport()
    transport.push(load_fixture("search_datasets.json"))
    run(["search", "--keyword", "cykel", "--publisher", "http://org/1"], transport)
    assert "tag.literal" in transport.requests[-1]


def test_dataset_by_id_renders_details(run, capsys):
    transport = FakeTransport()
    transport.push(load_fixture("dataset_recursive.json"))
    transport.push(load_fixture("dataset_entry.json"))
    assert run(["dataset", "--id", "547/28672"], transport) == 0
    out = capsys.readouterr().out
    assert "title:" in out
    assert "distributions:" in out


def test_dataset_rejects_a_malformed_id(run, capsys):
    assert run(["dataset", "--id", "547"], FakeTransport()) == 2
    assert "CONTEXT/ENTRY" in capsys.readouterr().out


def test_dataset_reports_a_miss(run, capsys):
    transport = FakeTransport()
    transport.push({"results": 0, "offset": 0, "limit": 1, "resource": {"children": []}})
    assert run(["dataset", "http://example.org/nope"], transport) == 1
    assert "no dataset found" in capsys.readouterr().out


def test_organisations_lists_top_publishers(run, capsys):
    transport = FakeTransport({"/charts/orgData.json": load_fixture("org_data.json")})
    assert run(["organisations", "--top", "3"], transport) == 0
    out = capsys.readouterr().out
    assert "publishers" in out
    assert "organisation" in out


def test_harvest_filters_failures(run, capsys):
    transport = FakeTransport()
    transport.push(load_fixture("harvest_report.json"))
    transport.push({"results": 1, "offset": 1, "limit": 100, "resource": {"children": []}})
    code = run(["harvest", "--failed", "--limit", "1"], transport)
    assert code == 0


def test_stats_renders_a_snapshot_table(run, capsys):
    transport = FakeTransport()
    transport.push(load_fixture("catalog_statistics.json"))
    assert run(["stats"], transport) == 0
    out = capsys.readouterr().out
    assert "datasets" in out and "psi" in out


def test_facet_prints_counts(run, capsys):
    transport = FakeTransport()
    payload = load_fixture("search_datasets.json")
    payload = dict(payload, facetFields=[
        {"name": "rdfType", "values": [{"name": "dcat:Dataset", "count": 7}]}
    ])
    transport.push(payload)
    assert run(["facet", "rdfType"], transport) == 0
    assert "7" in capsys.readouterr().out


def test_raw_prints_json(run, capsys):
    transport = FakeTransport()
    transport.push(load_fixture("search_datasets.json"))
    assert run(["raw", "title:x"], transport) == 0
    assert "results" in json.loads(capsys.readouterr().out)


def test_dump_writes_the_file(run, capsys, tmp_path):
    transport = FakeTransport()
    transport.push(b"<rdf:RDF/>", content_type="application/rdf+xml")
    destination = tmp_path / "all.rdf"
    assert run(["dump", str(destination)], transport) == 0
    assert destination.read_bytes() == b"<rdf:RDF/>"


def test_http_errors_become_exit_code_1(run, capsys):
    transport = FakeTransport()
    transport.push({"error": "nope"}, status=500)
    assert run(["search"], transport) == 1
    assert "error:" in capsys.readouterr().err


def test_version_flag_exits_cleanly():
    with pytest.raises(SystemExit) as info:
        cli.main(["--version"])
    assert info.value.code == 0


def test_links_command_renders_a_table(run, capsys):
    transport = FakeTransport()
    payload = load_fixture("link_check_report.json")
    transport.push(payload)
    transport.push({"results": 2, "offset": 2, "limit": 100, "resource": {"children": []}})
    assert run(["links", "--limit", "100"], transport) == 0
    assert "checked" in capsys.readouterr().out


def test_quality_command_renders_a_table(run, capsys):
    transport = FakeTransport()
    payload = load_fixture("metadata_quality.json")
    transport.push(payload)
    transport.push({"results": 2, "offset": 2, "limit": 100, "resource": {"children": []}})
    assert run(["quality"], transport) == 0
    assert "percent" in capsys.readouterr().out
