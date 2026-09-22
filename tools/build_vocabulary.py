"""Regenerate ``src/dataportalen/vocabulary.json``.

DCAT-AP-SE describes datasets with controlled-vocabulary URIs -- themes,
file types, frequencies, languages, licences -- and the registry serves those
URIs bare. The upstream documentation is explicit that resolving them to
human labels is the client's job:

    "Implementatörer förväntas ha information om dessa URI:er, t.ex. för att
    kunna presentera lablar istället för URI:er."

This script closes that gap ahead of time, so the package can ship labels
instead of making every caller dereference URIs at runtime. It:

1. reads DIGG's own DCAT-AP-SE form templates, which carry sv/en labels for
   the vocabularies the Swedish profile actually uses;
2. asks the live registry which vocabulary URIs publishers really use, so
   coverage is measured against reality rather than against the spec;
3. dereferences whatever is still unlabelled, following the URI to its
   authority table (EU Publications Office, INSPIRE, ...) and reading
   ``skos:prefLabel`` / ``rdfs:label``;
4. writes one sorted JSON file.

Run it from the repository root::

    python tools/build_vocabulary.py

It is a maintenance tool, not part of the installed package: nothing at
runtime hits the network.
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import io
import json
import os
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from typing import Dict, Iterable, Optional, Set

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dataportalen import Dataportal, Q, predicate_field  # noqa: E402
from dataportalen.namespaces import DCAT as _DCAT  # noqa: E402

DCAT_DATASET = _DCAT.Dataset

OUTPUT = os.path.join(
    os.path.dirname(__file__), "..", "src", "dataportalen", "vocabulary.json"
)

DIGG_TREE = "https://api.github.com/repos/diggsweden/DCAT-AP-SE/git/trees/HEAD?recursive=1"
DIGG_RAW = "https://raw.githubusercontent.com/diggsweden/DCAT-AP-SE/HEAD/"

#: Languages we keep labels for.
LANGUAGES = ("sv", "en")

#: Parallel fetches. The authority servers are slow but tolerate this; keep
#: it modest so the script stays a good citizen.
WORKERS = 12

#: Predicates whose objects are controlled-vocabulary URIs. Each becomes a
#: facet query against the live index to discover the values in real use.
VOCABULARY_PREDICATES = [
    "dcat:theme",
    "dcterms:format",
    "dcat:mediaType",
    "dcterms:license",
    "dcterms:accessRights",
    "dcterms:accrualPeriodicity",
    "dcterms:language",
    "dcterms:type",
    "dcterms:subject",
    "dcterms:spatial",
    "dcterms:conformsTo",
    "adms:status",
    "dcatap:availability",
    "dcatap:hvdCategory",
    "dcatap:applicableLegislation",
]

SKOS_PREF_LABEL = "{http://www.w3.org/2004/02/skos/core#}prefLabel"
RDFS_LABEL = "{http://www.w3.org/2000/01/rdf-schema#}label"
DCTERMS_TITLE = "{http://purl.org/dc/terms/}title"
RDF_ABOUT = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

#: Preferred first; a later predicate never overwrites an earlier one.
LABEL_PREDICATES = (SKOS_PREF_LABEL, RDFS_LABEL, DCTERMS_TITLE)

#: GeoNames stopped serving RDF and its API needs an account, so place names
#: come from the CC BY bulk exports instead. SE covers Swedish places (which
#: is nearly all of them here); countryInfo covers the rest.
GEONAMES_DUMPS = [
    ("https://download.geonames.org/export/dump/SE.zip", "SE.txt", 0, 1),
    ("https://download.geonames.org/export/dump/countryInfo.txt", None, 16, 4),
]
GEONAMES_ID_RE = re.compile(r"geonames\.org/(\d+)")

#: EU authority tables tag labels with three-letter codes.
LANG_ALIASES = {"swe": "sv", "eng": "en", "sv": "sv", "en": "en"}


def fetch(url: str, accept: Optional[str] = None, timeout: int = 30) -> Optional[bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "dataportal-se-vocab-builder"})
    if accept:
        request.add_header("Accept", accept)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, OSError) as exc:
        print("    ! %s -> %s" % (url, exc))
        return None


# --- 1. DIGG's DCAT-AP-SE templates -----------------------------------------


def from_digg_templates() -> Dict[str, Dict[str, str]]:
    """``{uri: {lang: label}}`` for every choice list in the Swedish profile."""
    print("[1/4] reading DIGG DCAT-AP-SE templates")
    body = fetch(DIGG_TREE)
    if body is None:
        raise SystemExit("cannot reach the DIGG repository")
    tree = json.loads(body)
    paths = [
        node["path"]
        for node in tree["tree"]
        if node["type"] == "blob" and node["path"].startswith("templates/")
    ]
    out: Dict[str, Dict[str, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        bodies = list(pool.map(
            lambda path: fetch(DIGG_RAW + urllib.parse.quote(path)), paths))
    for body in bodies:
        if body is None:
            continue
        try:
            doc = json.loads(body)
        except ValueError:
            continue
        for choice in doc.get("choices") or []:
            value, label = choice.get("value"), choice.get("label")
            if not value or not isinstance(label, dict):
                continue
            kept = {k: v for k, v in label.items() if k in LANGUAGES and isinstance(v, str)}
            if kept:
                out.setdefault(value, {}).update(kept)
    print("      %d templates -> %d labelled URIs" % (len(paths), len(out)))
    return out


# --- 2. what publishers actually use ----------------------------------------


def uris_in_use() -> Set[str]:
    """Every vocabulary URI the live registry currently serves."""
    print("[2/4] facetting the live registry for vocabulary URIs in use")
    found: Set[str] = set()
    with Dataportal() as client:
        for predicate in VOCABULARY_PREDICATES:
            facet = client.facet(predicate_field(predicate, "uri"), limit=1000)
            values = [v.name for v in facet.values if v.name.startswith("http")]
            found.update(values)
            print("      %-28s %4d distinct" % (predicate, len(values)))
    print("      %d distinct URIs in use" % len(found))
    return found


# --- 3. dereference whatever is still unlabelled ----------------------------


def labels_from_rdf(body: bytes, subject: str) -> Dict[str, str]:
    """Pull sv/en labels for ``subject`` out of an RDF/XML document.

    Descriptions are not always direct children of the root -- INSPIRE nests
    them -- so the whole tree is walked, and http/https plus trailing-slash
    variants of the subject all count as a match.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return {}

    bare = subject.rstrip("/")
    swapped = bare.replace("https://", "http://", 1) if bare.startswith("https://") \
        else bare.replace("http://", "https://", 1)
    wanted = {bare, bare + "/", swapped, swapped + "/"}

    out: Dict[str, str] = {}
    for description in root.iter():
        if description.get(RDF_ABOUT) not in wanted:
            continue
        for predicate in LABEL_PREDICATES:
            for child in description:
                if child.tag != predicate:
                    continue
                tag = (child.get(XML_LANG) or "").lower().split("-")[0]
                lang = LANG_ALIASES.get(tag)
                text = (child.text or "").strip()
                if lang and text and lang not in out:
                    out[lang] = text
    return out


def geonames_gazetteer() -> Dict[str, str]:
    """``{geonameId: name}`` from the CC BY bulk exports."""
    print("[3a] downloading GeoNames extracts", flush=True)
    names: Dict[str, str] = {}
    for url, member, id_column, name_column in GEONAMES_DUMPS:
        body = fetch(url, timeout=180)
        if body is None:
            continue
        if member:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                raw = archive.read(member)
        else:
            raw = body
        added = 0
        for line in raw.decode("utf-8", "replace").splitlines():
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) <= max(id_column, name_column):
                continue
            key, value = parts[id_column].strip(), parts[name_column].strip()
            if key.isdigit() and value and key not in names:
                names[key] = value
                added += 1
        print("      %-62s %6d names" % (url.rsplit("/", 1)[-1], added), flush=True)
    print("      %d places total" % len(names), flush=True)
    return names


def _resolve_one(uri: str) -> Dict[str, str]:
    body = fetch(uri, accept="application/rdf+xml", timeout=15)
    return labels_from_rdf(body, uri) if body else {}


def dereference(uris: Iterable[str]) -> Dict[str, Dict[str, str]]:
    print("[3/4] dereferencing URIs with no label yet", flush=True)
    pending = sorted(uris)
    out: Dict[str, Dict[str, str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(_resolve_one, pending))
    for uri, labels in zip(pending, results):
        if labels:
            out[uri] = labels
    print("      resolved %d of %d" % (len(out), len(pending)), flush=True)
    return out


# --- 4. write ---------------------------------------------------------------


#: Fields whose values are genuine controlled vocabularies. dcterms:conformsTo
#: is deliberately absent: it holds arbitrary specification URLs.
COVERAGE_FIELDS = ("themes", "languages", "spatial", "subjects", "hvd_categories")
COVERAGE_SINGLE = ("license", "access_rights", "accrual_periodicity")


def sample_datasets(client, sample: int, seed: int) -> list:
    """A reproducible random sample of datasets from across the corpus.

    The index has no random sort (``sort=random_N asc`` is rejected), so this
    draws random offsets against a stable ``created asc`` sort and reads a
    small page at each. Many small windows rather than one contiguous block,
    because datasets harvested together share a publisher and would otherwise
    bias the result.
    """
    total = client.count(Q.rdf_type(DCAT_DATASET))
    if sample >= total:
        print("      sampling all %d datasets" % total, flush=True)
        return list(client.iter_datasets(sort="created asc"))

    rng = random.Random(seed)
    window = 25
    seen = {}
    attempts = 0
    max_attempts = (sample // window) * 6 + 50
    while len(seen) < sample and attempts < max_attempts:
        offsets = [rng.randrange(0, max(total - window, 1))
                   for _ in range(min(WORKERS, 1 + (sample - len(seen)) // window))]
        with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
            pages = list(pool.map(
                lambda off: client.datasets(
                    limit=window, offset=off, sort="created asc"),
                offsets))
        attempts += len(offsets)
        for page in pages:
            for entry in page:
                if entry.entry_uri and entry.entry_uri not in seen:
                    seen[entry.entry_uri] = entry
        print("      %d/%d sampled" % (min(len(seen), sample), sample), flush=True)
    out = list(seen.values())[:sample]
    print("      %d datasets from %d random positions (seed %d)"
          % (len(out), attempts, seed), flush=True)
    return out


def report_usage_coverage(sample: int = 5000, seed: int = 20260922) -> None:
    """Print label coverage weighted by how often values really occur."""
    import importlib

    from dataportalen import vocab as vocab_module

    importlib.reload(vocab_module)  # pick up the file just written

    print()
    print("[5/5] coverage over a random sample of %s datasets"
          % ("all" if sample >= 10 ** 9 else sample), flush=True)

    total = collections.Counter()
    labelled = collections.Counter()
    unresolved = collections.Counter()
    with Dataportal() as client:
        datasets = sample_datasets(client, sample, seed)
        for entry in datasets:
            doc = entry.to_dict(distributions=False)
            for field in COVERAGE_FIELDS:
                for value in doc.get(field) or []:
                    total[field] += 1
                    if value["label"]:
                        labelled[field] += 1
                    else:
                        unresolved[value["uri"].rsplit("/", 1)[0]] += 1
            for field in COVERAGE_SINGLE:
                value = doc.get(field)
                if value:
                    total[field] += 1
                    if value["label"]:
                        labelled[field] += 1
                    else:
                        unresolved[value["uri"].rsplit("/", 1)[0]] += 1

    grand = sum(total.values())
    good = sum(labelled.values())
    print()
    for field in sorted(total, key=lambda f: -total[f]):
        print("      %-22s %6d/%-6d %3.0f%%" % (
            field, labelled[field], total[field],
            100.0 * labelled[field] / max(total[field], 1)))
    print("      %-22s %6d/%-6d %3.0f%%" % (
        "TOTAL", good, grand, 100.0 * good / max(grand, 1)))
    print()
    print("      n = %d datasets, %d vocabulary values" % (len(datasets), grand))
    if unresolved:
        print("      unlabelled, by vocabulary:")
        for group, count in unresolved.most_common(8):
            print("        %6d  %s" % (count, group))


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate src/dataportalen/vocabulary.json.")
    parser.add_argument(
        "--sample", default="5000",
        help="datasets to measure coverage over: a number, or 'all' for the "
             "whole corpus (exact, but a full crawl). Default 5000.")
    parser.add_argument(
        "--seed", type=int, default=20260922,
        help="seed for the random sample, so a run is reproducible.")
    parser.add_argument(
        "--skip-build", action="store_true",
        help="only re-measure coverage; do not rebuild the table.")
    args = parser.parse_args(argv)
    sample = 10 ** 9 if args.sample == "all" else int(args.sample)

    if args.skip_build:
        report_usage_coverage(sample=sample, seed=args.seed)
        return 0

    vocabulary = from_digg_templates()
    used = uris_in_use()

    missing = {uri for uri in used if uri not in vocabulary}

    # Place names first: they are a bulk lookup, not 600 HTTP round trips.
    geonames = {uri for uri in missing if GEONAMES_ID_RE.search(uri)}
    if geonames:
        places = geonames_gazetteer()
        resolved = 0
        for uri in geonames:
            match = GEONAMES_ID_RE.search(uri)
            name = places.get(match.group(1)) if match else None
            if name:
                # The gazetteer carries one endonym, not per-language labels.
                vocabulary[uri] = {"sv": name, "en": name}
                resolved += 1
        print("      matched %d of %d GeoNames URIs" % (resolved, len(geonames)), flush=True)
        missing -= set(vocabulary)

    vocabulary.update(dereference(missing))

    still_missing = sorted(uri for uri in used if uri not in vocabulary)

    print("[4/4] writing %s" % os.path.normpath(OUTPUT))
    payload = {
        "_comment": (
            "Generated by tools/build_vocabulary.py from DIGG's DCAT-AP-SE "
            "templates and the authority tables the URIs dereference to. "
            "Do not edit by hand; re-run the script instead."
        ),
        "labels": {uri: vocabulary[uri] for uri in sorted(vocabulary)},
    }
    with open(OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=False)
        handle.write("\n")

    covered = len(used) - len(still_missing)
    print()
    print("terms shipped        : %d" % len(vocabulary))
    print("distinct URIs seen   : %d" % len(used))
    print("distinct covered     : %d (%.1f%%)" % (covered, 100.0 * covered / max(len(used), 1)))
    print()
    print("Distinct-URI coverage understates real coverage: dcterms:conformsTo")
    print("holds arbitrary specification URLs, not a vocabulary, and its long")
    print("tail can never be labelled. What matters is coverage weighted by")
    print("how often values actually appear -- see report_usage_coverage().")

    if still_missing:
        print()
        print("unresolved           : %d" % len(still_missing))
        by_host = collections.Counter(urllib.parse.urlparse(u).netloc for u in still_missing)
        for host, count in by_host.most_common(12):
            print("    %-42s %d" % (host, count))

    report_usage_coverage(sample=sample, seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
