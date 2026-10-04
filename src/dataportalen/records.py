"""The shapes of the dicts this package hands out, for editors and type checkers.

Everything `Catalog` returns is a plain ``dict`` at run time; nothing here
changes that. These are :class:`typing.TypedDict` descriptions of those dicts,
so an editor completes ``dataset["`` and a checker catches ``"licence"``.

They are measured, not designed: every one of the 23,582 datasets in the
registry has exactly the keys of :class:`DatasetRecord`, every data service
those of :class:`DataServiceRecord`, and ``tests/test_records.py`` holds the
package to it -- keys and the shapes underneath them.

A key is always present. Where the publisher said nothing the value is
``None``, ``[]`` or ``{}``, never a missing key -- with three exceptions on a
distribution, which are there only when they have something to say:
``broken``, ``unverified`` and ``byte_size``.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, TypedDict

__all__ = [
    "LanguageMap",
    "KeywordMap",
    "LicenseRecord",
    "LinkMark",
    "ContactRecord",
    "TemporalRecord",
    "PublisherRecord",
    "Publisher",
    "PublisherDetail",
    "DistributionRecord",
    "DatasetRecord",
    "DataServiceRecord",
]


class LanguageMap(TypedDict, total=False):
    """Text as the publisher wrote it: Swedish, English, or both.

    A key is there only when that language is -- 53% of datasets are Swedish
    only, 36% carry both, 10% are English only -- so read it with
    :func:`dataportalen.text` rather than ``["sv"]``.
    """

    sv: str
    en: str


class KeywordMap(TypedDict, total=False):
    """Keywords per language; ``{}`` when a record has none."""

    sv: List[str]
    en: List[str]


class LicenseRecord(TypedDict):
    """A licence: what ``license=`` takes, what it is called, where to read it."""

    id: str
    label: LanguageMap
    uri: str


class LinkMark(TypedDict):
    """What the registry's nightly link check said about one file."""

    reason: Optional[str]
    checked: Optional[str]


class ContactRecord(TypedDict):
    uri: Optional[str]
    name: Optional[str]
    email: Optional[str]


class TemporalRecord(TypedDict):
    start: Optional[str]
    end: Optional[str]


class PublisherRecord(TypedDict):
    """The publisher nested in a dataset or data service.

    ``id`` is what ``publisher=`` takes and ``None`` on the 27 records that
    name no publisher. The other attributes are that record's own agent, as
    the registry has it.
    """

    id: Optional[str]
    uri: Optional[str]
    name: LanguageMap
    aliases: List[str]
    type: Optional[str]
    homepage: Optional[str]
    email: Optional[str]
    identifiers: List[str]


class Publisher(PublisherRecord):
    """A row of :meth:`Catalog.publishers`: who, and how much of theirs is here."""

    dataset_count: int
    data_service_count: int


class PublisherDetail(Publisher):
    """What :meth:`Catalog.publisher` returns: the row plus its dataset facets,
    as ``{filter: {value: count}}``."""

    facets: Dict[str, Dict[str, int]]


class _DistributionAlways(TypedDict):
    uri: Optional[str]
    title: LanguageMap
    description: LanguageMap
    access_url: Optional[str]
    download_url: Optional[str]
    format: Optional[str]
    license: Optional[LicenseRecord]
    status: Optional[str]
    availability: Optional[str]
    languages: List[str]
    issued: Optional[str]
    modified: Optional[str]
    access_service_uris: List[str]


class DistributionRecord(_DistributionAlways, total=False):
    """One file or access point of a dataset.

    ``broken`` is on a file the registry got an HTTP error for or found no host
    for, ``unverified`` on one its checker could not get through to, and ``byte_size`` on the 1.4%
    whose publisher states a size. Each is absent otherwise.
    """

    broken: LinkMark
    unverified: LinkMark
    byte_size: int


class DatasetRecord(TypedDict):
    """A dataset, as :meth:`Catalog.datasets` and :meth:`Catalog.get` return it."""

    uri: Optional[str]
    context_id: str
    entry_id: str
    type: Literal["dataset"]
    title: LanguageMap
    description: LanguageMap
    keywords: KeywordMap
    identifier: Optional[str]
    landing_page: Optional[str]
    publisher: PublisherRecord
    themes: List[str]
    license: Optional[LicenseRecord]
    access_rights: Optional[str]
    accrual_periodicity: Optional[str]
    languages: List[str]
    spatial: List[str]
    temporal: Optional[TemporalRecord]
    issued: Optional[str]
    modified: Optional[str]
    contact_points: List[ContactRecord]
    distributions: List[DistributionRecord]


class DataServiceRecord(TypedDict):
    """A data service -- an API rather than a file -- as
    :meth:`Catalog.data_services` returns it."""

    uri: Optional[str]
    context_id: str
    entry_id: str
    type: Literal["data_service"]
    title: LanguageMap
    description: LanguageMap
    keywords: KeywordMap
    service_type: Optional[str]
    endpoint_url: Optional[str]
    endpoint_description: Optional[str]
    serves_datasets: List[str]
    conforms_to: List[str]
    publisher: PublisherRecord
    themes: List[str]
    license: Optional[LicenseRecord]
    access_rights: Optional[str]
    landing_page: Optional[str]
    contact_points: List[ContactRecord]
