"""Download the whole catalogue to a local JSONL file.

One line per dataset, each line a self-contained JSON object with its
distributions, publisher and contact points nested::

    from dataportalen import download_catalog

    download_catalog("catalog.jsonl")
    download_catalog("catalog.jsonl.gz")     # gzip inferred from the suffix

The naive way to do this would be one ``recursive=True`` fetch per dataset,
which is 23,000+ requests. Instead each entity type is bulk-crawled once and
the references are joined locally, which is roughly 650 requests: the whole
catalogue in a couple of minutes.

The nightly ``all.rdf`` dump would be a single request, but it has been
observed lagging the registry by a week, so this crawls the search index --
slower, and current.
"""

from __future__ import annotations

import concurrent.futures
import gzip
import io
import json
import os
import threading
import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from .models import Agent, ContactPoint, Dataset, Distribution, Entry
from .namespaces import DCAT, FOAF, VCARD
from .query import Q
from .rdf import DEFAULT_LANGUAGES

__all__ = ["download_catalog", "CatalogSummary"]

#: Entries per request. The registry caps this at 100.
PAGE_SIZE = 100

#: A stable sort, so concurrent pages tile the corpus without overlap.
#: `modified desc` (the client default) shifts under a nightly re-harvest.
STABLE_SORT = "created asc"


class CatalogSummary:
    """What a :func:`download_catalog` run produced."""

    __slots__ = ("path", "datasets", "distributions", "bytes_written", "elapsed", "requests")

    def __init__(
        self,
        path: str,
        datasets: int,
        distributions: int,
        bytes_written: int,
        elapsed: float,
        requests: int,
    ) -> None:
        self.path = path
        self.datasets = datasets
        self.distributions = distributions
        self.bytes_written = bytes_written
        self.elapsed = elapsed
        self.requests = requests

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "datasets": self.datasets,
            "distributions": self.distributions,
            "bytes_written": self.bytes_written,
            "elapsed": round(self.elapsed, 1),
            "requests": self.requests,
        }

    def __repr__(self) -> str:
        return "<CatalogSummary %s: %d datasets, %d distributions, %.1f MiB in %.0fs>" % (
            os.path.basename(self.path),
            self.datasets,
            self.distributions,
            self.bytes_written / (1 << 20),
            self.elapsed,
        )


class _Counter:
    """A thread-safe request tally."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def add(self, n: int = 1) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        return self._value


def _open_output(path: str):
    """Open ``path`` for writing text, gzipping when the name says so."""
    if path.endswith(".gz"):
        return gzip.open(path, "wt", encoding="utf-8", newline="\n")
    return io.open(path, "w", encoding="utf-8", newline="\n")


def _pages(total: int, page_size: int = PAGE_SIZE) -> List[int]:
    """Offsets covering ``total`` entries."""
    return list(range(0, total, page_size))


def _crawl(
    client: Any,
    query: Q,
    model: Optional[type],
    total: int,
    workers: int,
    counter: _Counter,
    on_page: Optional[Callable[[int], None]] = None,
) -> Iterator[Entry]:
    """Fetch every page of a query concurrently, yielding entries.

    Pages are independent because the offsets are known up front and the sort
    is stable, so they can be fetched in parallel and stitched back together.
    """
    offsets = _pages(total)
    if not offsets:
        return

    def fetch(offset: int) -> List[Entry]:
        page = client.search(
            query, model=model, limit=PAGE_SIZE, offset=offset, sort=STABLE_SORT
        )
        counter.add()
        return list(page.entries)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for entries in pool.map(fetch, offsets):
            if on_page is not None:
                on_page(len(entries))
            for entry in entries:
                yield entry


def _index_by_uri(entries: Iterator[Entry]) -> Dict[str, Dict[str, Any]]:
    """``{resource URI: to_dict()}`` for a crawled entity type."""
    out: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        uri = entry.resource_uri
        if uri:
            out[uri] = entry.to_dict()
    return out


def _take(entries, count):
    """The first ``count`` entries of a lazy crawl."""
    for index, entry in enumerate(entries):
        if index >= count:
            return
        yield entry


def _bulk_indexes(client, workers, counter):
    """One crawl per referenced type -- the right trade for a full export."""
    distribution_total = client.count(Q.rdf_type(DCAT.Distribution))
    agent_total = client.count(Q.rdf_type(FOAF.Agent))
    contact_query = Q.rdf_type(
        VCARD.Organization, VCARD.Organisation, VCARD.Individual, VCARD.Kind
    )
    contact_total = client.count(contact_query)
    counter.add(3)

    distributions = _index_by_uri(_crawl(
        client, Q.rdf_type(DCAT.Distribution), Distribution,
        distribution_total, workers, counter))
    agents = _index_by_uri(_crawl(
        client, Q.rdf_type(FOAF.Agent), Agent, agent_total, workers, counter))
    contacts = _index_by_uri(_crawl(
        client, contact_query, ContactPoint, contact_total, workers, counter))
    return distributions, agents, contacts


def _targeted_indexes(client, datasets, workers, counter):
    """Resolve only the URIs this batch of datasets actually references."""
    distribution_uris = {}
    agent_uris = {}
    contact_uris = {}
    for dataset in datasets:
        for uri in dataset.distribution_uris:
            distribution_uris.setdefault(uri, None)
        if dataset.publisher_uri:
            agent_uris.setdefault(dataset.publisher_uri, None)
        for uri in dataset.contact_point_uris:
            contact_uris.setdefault(uri, None)

    def resolve(uris, model):
        if not uris:
            return {}
        found = client.lookup_many(list(uris), model=model)
        counter.add(max(1, (len(uris) + 19) // 20))
        return {e.resource_uri: e.to_dict() for e in found if e.resource_uri}

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(workers, 3)) as pool:
        futures = [
            pool.submit(resolve, distribution_uris, Distribution),
            pool.submit(resolve, agent_uris, Agent),
            pool.submit(resolve, contact_uris, ContactPoint),
        ]
        return tuple(future.result() for future in futures)


def download_catalog(
    path: str,
    *,
    languages: Sequence[str] = DEFAULT_LANGUAGES,
    workers: int = 8,
    limit: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    client: Any = None,
    base_url: Optional[str] = None,
) -> CatalogSummary:
    """Download every dataset to ``path`` as JSONL and return a summary.

    Each line is one dataset in the same shape as
    :meth:`~dataportalen.models.Dataset.to_dict`, with its distributions,
    publisher and contact points resolved and nested -- so a line stands on
    its own with no further lookups.

    :param path: where to write; a ``.gz`` suffix gzips the output.
    :param languages: preferred language order for localized values.
    :param workers: parallel requests. The registry tolerates 8 comfortably.
    :param limit: stop after this many datasets, for smoke tests.
    :param progress: called as ``progress(done, total)`` as datasets are written.
    :param client: an existing :class:`~dataportalen.Dataportal` to reuse.
    :param base_url: registry root, when not passing ``client``.
    """
    from .client import Dataportal  # local import: client imports this module

    owned = client is None
    if owned:
        kwargs = {"languages": languages}
        if base_url:
            kwargs["base_url"] = base_url
        client = Dataportal(**kwargs)

    started = time.time()
    counter = _Counter()
    written = 0
    bytes_written = 0
    distributions_written = 0

    try:
        dataset_total = client.count(Q.rdf_type(DCAT.Dataset))
        counter.add()
        if limit is not None:
            dataset_total = min(dataset_total, limit)

        datasets = _crawl(
            client, Q.rdf_type(DCAT.Dataset), Dataset, dataset_total, workers, counter
        )
        missing: Dict[str, None] = {}

        if limit is None:
            # Full export: every distribution is needed anyway, so one bulk
            # crawl per referenced type beats resolving dataset by dataset.
            indexes = _bulk_indexes(client, workers, counter)
        else:
            # Partial export: crawling 35k distributions to serve a few
            # hundred datasets would dwarf the actual work, so resolve only
            # what this batch references.
            datasets = list(_take(datasets, limit))
            indexes = _targeted_indexes(client, datasets, workers, counter)

        distributions, agents, contacts = indexes

        with _open_output(path) as handle:
            for dataset in datasets:
                record, used = _assemble(
                    dataset, distributions, agents, contacts, missing
                )
                line = json.dumps(record, ensure_ascii=False) + "\n"
                handle.write(line)
                written += 1
                bytes_written += len(line.encode("utf-8"))
                distributions_written += used
                if progress is not None:
                    progress(written, dataset_total)

        if missing:
            # Anything the crawl did not cover -- a distribution in a
            # non-public context, say. Reported, never silently dropped.
            _report_missing(missing)


    finally:
        if owned:
            client.close()

    return CatalogSummary(
        path=path,
        datasets=written,
        distributions=distributions_written,
        bytes_written=bytes_written,
        elapsed=time.time() - started,
        requests=counter.value,
    )


def _assemble(
    dataset: Dataset,
    distributions: Dict[str, Dict[str, Any]],
    agents: Dict[str, Dict[str, Any]],
    contacts: Dict[str, Dict[str, Any]],
    missing: Dict[str, None],
) -> Tuple[Dict[str, Any], int]:
    """One dataset dict with its references spliced in from the indexes."""
    # `distributions=False`: a search hit has none inline, and we are about to
    # supply better ones from the index.
    record = dataset.to_dict(distributions=False)

    nested = []
    for uri in dataset.distribution_uris:
        found = distributions.get(uri)
        if found is not None:
            nested.append(found)
        else:
            missing.setdefault(uri, None)
    record["distributions"] = nested

    publisher_uri = dataset.publisher_uri
    if publisher_uri:
        found = agents.get(publisher_uri)
        if found is not None:
            record["publisher"] = found
        elif record.get("publisher") is None:
            record["publisher"] = {"uri": publisher_uri, "name": None}

    if not record.get("contact_points"):
        resolved = [contacts[u] for u in dataset.contact_point_uris if u in contacts]
        if resolved:
            record["contact_points"] = resolved

    return record, len(nested)


def _report_missing(missing: Dict[str, None]) -> None:
    import warnings

    sample = list(missing)[:3]
    warnings.warn(
        "%d referenced URIs were not found in the bulk crawl and are listed by "
        "URI only (e.g. %s)" % (len(missing), ", ".join(sample)),
        stacklevel=2,
    )
