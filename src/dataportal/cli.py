"""Command-line access to the registry.

    dataportal search "öppna data" --limit 5
    dataportal dataset https://example.org/dataset/1 --json
    dataportal organisations --top 10
    dataportal harvest --failed
    dataportal stats
    dataportal links --failed
    dataportal quality
    dataportal dump ./all.rdf

Every subcommand takes ``--json`` to print raw JSON instead of a table.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .client import DEFAULT_BASE_URL, DUMP_URL, Dataportal
from .exceptions import DataportalError
from .models import Entry
from .query import Q

__all__ = ["main"]


def _out(text: str = "") -> None:
    try:
        print(text)
    except UnicodeEncodeError:  # pragma: no cover - narrow console encodings
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, "replace").decode(encoding))


def _truncate(text: Optional[str], width: int) -> str:
    value = (text or "").replace("\n", " ").strip()
    if len(value) <= width:
        return value
    return value[: width - 1] + "…"


def _table(rows: Sequence[Sequence[Any]], headers: Sequence[str]) -> str:
    cells = [[str(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], len(cell))
    line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    out = [line, "  ".join("-" * w for w in widths)]
    for row in cells:
        out.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(out)


def _entry_row(entry: Entry) -> List[str]:
    return [
        "%s/%s" % (entry.context_id, entry.entry_id),
        _truncate(entry.title, 60),
        _truncate(entry.resource_uri, 60),
    ]


def _dump_json(payload: Any) -> None:
    _out(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


# --- commands ---------------------------------------------------------------


def cmd_search(client: Dataportal, args: argparse.Namespace) -> int:
    filters: Dict[str, Any] = {}
    if args.text:
        filters["text"] = args.text
    if args.title:
        filters["title"] = args.title
    if args.keyword:
        filters["keyword"] = args.keyword
    if args.publisher:
        filters["publisher"] = args.publisher
    if args.theme:
        filters["theme"] = args.theme
    if args.format:
        filters["format"] = args.format
    if args.context:
        filters["context"] = args.context
    if args.modified_after:
        filters["modified_after"] = args.modified_after
    if args.query:
        filters["query"] = Q.raw(args.query)

    page = client.datasets(limit=args.limit, offset=args.offset, **filters)
    if args.json:
        _dump_json(page.raw)
        return 0
    _out("~%d matching datasets (showing %d from offset %d)" % (page.total, len(page), page.offset))
    if page:
        _out(_table([_entry_row(e) for e in page], ["id", "title", "uri"]))
    return 0


def cmd_dataset(client: Dataportal, args: argparse.Namespace) -> int:
    if args.id:
        context_id, _, entry_id = args.id.partition("/")
        if not entry_id:
            _out("error: --id must look like CONTEXT/ENTRY, e.g. 547/28672")
            return 2
        dataset = client.dataset(context_id=context_id, entry_id=entry_id)
    else:
        dataset = client.dataset(uri=args.uri)
    if dataset is None:
        _out("no dataset found")
        return 1
    if args.json:
        _dump_json(dataset.to_json())
        return 0
    _out("title:       %s" % dataset.title)
    _out("uri:         %s" % dataset.resource_uri)
    _out("id:          %s/%s" % (dataset.context_id, dataset.entry_id))
    _out("publisher:   %s" % dataset.publisher_uri)
    _out("license:     %s" % dataset.license)
    _out("issued:      %s" % dataset.issued)
    _out("modified:    %s" % dataset.modified_date)
    _out("keywords:    %s" % ", ".join(dataset.keywords))
    _out("themes:      %s" % ", ".join(dataset.theme_uris))
    _out("landing:     %s" % dataset.landing_page)
    description = dataset.description
    if description:
        _out("")
        _out(_truncate(description, 600))
    distributions = dataset.distributions()
    if distributions:
        _out("")
        _out("distributions:")
        _out(
            _table(
                [
                    [
                        _truncate(d.title, 40),
                        _truncate(d.format or d.media_type, 40),
                        _truncate(d.download_url or d.access_url, 70),
                    ]
                    for d in distributions
                ],
                ["title", "format", "url"],
            )
        )
    contacts = dataset.fetch_contact_points()
    if contacts:
        _out("")
        _out("contacts: %s" % ", ".join(
            "%s <%s>" % (c.name or "?", c.email or "") for c in contacts
        ))
    return 0


def cmd_organisations(client: Dataportal, args: argparse.Namespace) -> int:
    orgs = client.organisations()
    if args.json:
        _dump_json([o.to_dict() for o in orgs])
        return 0
    summary = client.organisation_summary()
    _out("%d publishers, %d datasets, %d independent data services" % (
        summary["publishers"], summary["datasets"], summary["independent_data_services"]
    ))
    _out("")
    _out(
        _table(
            [[o.dataset_count, _truncate(o.name, 55), _truncate(o.uri, 55)]
             for o in orgs[: args.top]],
            ["datasets", "organisation", "uri"],
        )
    )
    return 0


def cmd_harvest(client: Dataportal, args: argparse.Namespace) -> int:
    reports = client.harvest_reports(
        public_sector_only=args.psi, limit=args.limit
    )
    if args.failed:
        reports = [r for r in reports if r.all_succeeded is not True]
    if args.json:
        _dump_json([r.to_json() for r in reports])
        return 0
    if not reports:
        _out("no harvest reports matched")
        return 0
    rows = [
        [
            r.context_id or "",
            _truncate(r.title, 45),
            "ok" if r.all_succeeded else "FAILED",
            r.main_resource_count if r.main_resource_count is not None else "",
            "+%s ~%s -%s" % (r.added, r.updated, r.removed),
            r.validation_errors if r.validation_errors is not None else "",
        ]
        for r in reports
    ]
    _out(_table(rows, ["ctx", "source", "status", "datasets", "changes", "errors"]))
    return 0


def cmd_links(client: Dataportal, args: argparse.Namespace) -> int:
    reports = client.link_check_reports(limit=args.limit, failing_only=args.failed)
    if args.json:
        _dump_json([
            {
                "context_id": r.context_id,
                "checked": r.checked,
                "failed": r.failed,
                "excluded": r.excluded,
                "run_at": str(r.run_at),
            }
            for r in reports
        ])
        return 0
    if not reports:
        _out("no link check reports matched")
        return 0
    reports = sorted(reports, key=lambda r: r.failed or 0, reverse=True)
    _out(
        _table(
            [[r.context_id or "", r.checked, r.failed, r.excluded, str(r.run_at)]
             for r in reports[: args.top]],
            ["ctx", "checked", "failed", "excluded", "run at"],
        )
    )
    return 0


def cmd_quality(client: Dataportal, args: argparse.Namespace) -> int:
    scores = client.metadata_quality(limit=args.limit)
    if args.json:
        _dump_json([
            {
                "context_id": s.context_id,
                "title": s.title,
                "score": s.score,
                "percentage": s.percentage,
                "rating": s.rating,
                "total": s.is_total,
            }
            for s in scores
        ])
        return 0
    if not scores:
        _out("no quality assessments available")
        return 1
    scores = sorted(scores, key=lambda s: s.percentage or 0, reverse=True)
    _out(
        _table(
            [[s.context_id or "", _truncate(s.title, 50), s.score, s.percentage, s.rating]
             for s in scores[: args.top]],
            ["ctx", "catalog", "score", "percent", "rating"],
        )
    )
    return 0


def cmd_stats(client: Dataportal, args: argparse.Namespace) -> int:
    snapshots = client.catalog_statistics(limit=args.days)
    if args.json:
        _dump_json([
            {
                "date": str(s.date),
                "datasets": s.dataset_count,
                "public_datasets": s.public_dataset_count,
                "psi_datasets": s.psi_dataset_count,
                "other_datasets": s.other_dataset_count,
                "contexts": s.datasets_per_context,
            }
            for s in snapshots
        ])
        return 0
    if not snapshots:
        _out("no statistics available")
        return 1
    _out(
        _table(
            [
                [
                    str(s.date),
                    s.dataset_count,
                    s.public_dataset_count,
                    s.psi_dataset_count,
                    s.other_dataset_count,
                    len(s.datasets_per_context),
                ]
                for s in snapshots
            ],
            ["date", "datasets", "public", "psi", "other", "contexts"],
        )
    )
    return 0


def cmd_contexts(client: Dataportal, args: argparse.Namespace) -> int:
    rows = client.datasets_per_organisation(day=args.day)
    if args.json:
        _dump_json([{"context_id": c, "name": n, "datasets": d} for c, n, d in rows])
        return 0
    _out(_table([[c, d, _truncate(n, 60)] for c, n, d in rows[: args.top]],
                ["ctx", "datasets", "organisation"]))
    return 0


def cmd_facet(client: Dataportal, args: argparse.Namespace) -> int:
    query = Q.raw(args.query) if args.query else None
    facet = client.facet(args.field, query, limit=args.limit)
    if args.json:
        _dump_json(facet.as_dict())
        return 0
    _out(_table([[v.count, _truncate(v.name, 90)] for v in facet.values],
                ["count", args.field]))
    return 0


def cmd_raw(client: Dataportal, args: argparse.Namespace) -> int:
    """Run an arbitrary Solr query and print the raw JSON response."""
    data = client.search_raw(
        Q.raw(args.query) if args.query else None,
        limit=args.limit,
        offset=args.offset,
        sort=args.sort,
    )
    _dump_json(data)
    return 0


def cmd_dump(client: Dataportal, args: argparse.Namespace) -> int:
    written = [0]

    def progress(total: int) -> None:
        if total - written[0] >= 8 << 20:
            written[0] = total
            sys.stderr.write("\r%.1f MiB" % (total / (1 << 20)))
            sys.stderr.flush()

    client.download_dump(args.destination, url=args.url, progress=progress)
    sys.stderr.write("\r")
    sys.stderr.flush()
    _out("wrote %s" % args.destination)
    return 0


# --- argument parsing -------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dataportal",
        description="Query the Sveriges dataportal registry API.",
    )
    parser.add_argument("--version", action="version", version="dataportal %s" % __version__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="registry root URL")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--lang", default="sv,en",
        help="comma-separated language preference for localized values",
    )
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="search datasets")
    search.add_argument("text", nargs="?", help="free-text search")
    search.add_argument("--title")
    search.add_argument("--keyword", action="append")
    search.add_argument("--publisher", action="append")
    search.add_argument("--theme", action="append")
    search.add_argument("--format", action="append", dest="format")
    search.add_argument("--context", action="append")
    search.add_argument("--modified-after", dest="modified_after")
    search.add_argument("--query", help="raw Solr query fragment, ANDed with the rest")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--offset", type=int, default=0)
    search.set_defaults(func=cmd_search)

    dataset = sub.add_parser("dataset", help="show one dataset")
    dataset.add_argument("uri", nargs="?", help="the dataset's own URI")
    dataset.add_argument("--id", help="registry ids as CONTEXT/ENTRY, e.g. 547/28672")
    dataset.set_defaults(func=cmd_dataset)

    orgs = sub.add_parser("organisations", help="dataset counts per publisher")
    orgs.add_argument("--top", type=int, default=25)
    orgs.set_defaults(func=cmd_organisations)

    harvest = sub.add_parser("harvest", help="latest harvest report per source")
    harvest.add_argument("--psi", action="store_true", help="public sector only")
    harvest.add_argument("--failed", action="store_true", help="only runs that did not fully succeed")
    harvest.add_argument("--limit", type=int, default=100)
    harvest.set_defaults(func=cmd_harvest)

    stats = sub.add_parser("stats", help="nightly registry-wide statistics")
    stats.add_argument("--days", type=int, default=1, help="how many snapshots to show")
    stats.set_defaults(func=cmd_stats)

    links = sub.add_parser("links", help="nightly link check per catalog")
    links.add_argument("--failed", action="store_true", help="only catalogs with dead links")
    links.add_argument("--limit", type=int, default=100)
    links.add_argument("--top", type=int, default=25)
    links.set_defaults(func=cmd_links)

    quality = sub.add_parser("quality", help="DCAT-AP metadata quality (MQA) scores")
    quality.add_argument("--limit", type=int, default=100)
    quality.add_argument("--top", type=int, default=25)
    quality.set_defaults(func=cmd_quality)

    contexts = sub.add_parser("contexts", help="datasets per catalog context, with names")
    contexts.add_argument("--day", type=int, default=0, help="0 = newest snapshot")
    contexts.add_argument("--top", type=int, default=25)
    contexts.set_defaults(func=cmd_contexts)

    facet = sub.add_parser("facet", help="count values of an indexed field")
    facet.add_argument("field", help="e.g. rdfType, lang, tag.literal")
    facet.add_argument("--query", help="raw Solr query to restrict the facet")
    facet.add_argument("--limit", type=int, default=25)
    facet.set_defaults(func=cmd_facet)

    raw = sub.add_parser("raw", help="run a raw Solr query")
    raw.add_argument("query")
    raw.add_argument("--limit", type=int, default=10)
    raw.add_argument("--offset", type=int, default=0)
    raw.add_argument("--sort", default=None)
    raw.set_defaults(func=cmd_raw)

    dump = sub.add_parser("dump", help="download the nightly RDF/XML dump")
    dump.add_argument("destination")
    dump.add_argument("--url", default=DUMP_URL)
    dump.set_defaults(func=cmd_dump)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    languages = [part.strip() for part in args.lang.split(",") if part.strip()]
    try:
        with Dataportal(
            args.base_url, timeout=args.timeout, languages=languages or ("sv", "en")
        ) as client:
            return int(args.func(client, args) or 0)
    except DataportalError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
