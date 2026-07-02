from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .database import json_dumps, json_loads


NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
WIKIDATA_SEARCH_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = "HouseholdFIRELens/0.1 (local personal finance entity classification)"
LOOKUP_TIMEOUT_SECONDS = 5
NOMINATIM_DELAY_SECONDS = 1.1

GENERIC_LOOKUP_NAMES = {
    "",
    "SEPA",
    "SEPA OVERBOEKING",
    "TRANSFER",
    "TRANSACTION",
    "OVERSCHRIJVING",
    "INCASSO",
    "BETALING",
    "ONLINE BETALING",
    "CARD",
    "MASTERCARD",
    "VISA",
}
UNSAFE_LOOKUP_PATTERNS = (
    r"\bAPPLE PAY\b",
    r"\bGOOGLE PAY\b",
    r"\bPAY\.NL\b",
    r"\bPAS[A-Z0-9]*\b",
    r"\bNR\b",
    r"\bBEA\b",
    r"\bVALUE DATE\b",
    r"\bTRANSACTION\b",
    r"\bSEPA\b",
    r"\bIBAN\b",
    r"\bBIC\b",
    r"\b\d{1,2}[.:]\d{2}\b",
)

CATEGORY_SIGNALS: List[Tuple[Tuple[str, ...], str, str, str, float]] = [
    (("dentist", "dental", "orthodont", "hospital", "pharmacy", "healthcare", "medical clinic"), "household_spend", "Health", "", 0.74),
    (("restaurant", "cafe", "coffeehouse", "bar", "fast food", "pizzeria"), "household_spend", "Eating Out", "", 0.72),
    (("supermarket", "grocery", "food retailer", "hypermarket"), "household_spend", "Groceries", "", 0.72),
    (("hotel", "hostel", "airline", "travel agency", "booking website", "tour operator"), "household_spend", "Holiday", "", 0.72),
    (("public transport", "railway", "taxi", "parking", "fuel station", "gas station"), "household_spend", "Transportation", "", 0.70),
    (("retailer", "department store", "clothing", "webshop", "e-commerce", "online marketplace"), "household_spend", "Shopping", "", 0.68),
    (("streaming", "subscription", "software company", "cloud computing"), "household_spend", "Subscriptions", "", 0.66),
    (("energy company", "electric utility", "telecommunications", "internet service provider", "water company"), "household_spend", "Housing", "Utilities", 0.70),
    (("insurance company", "insurer"), "household_spend", "Housing", "Insurance", 0.70),
    (("tax authority", "municipality", "government agency", "public body"), "household_spend", "Taxes and Government", "", 0.70),
]

OSM_TAG_RULES: List[Tuple[Tuple[str, ...], Tuple[str, ...], str, str, str, float]] = [
    (("amenity", "healthcare"), ("dentist", "clinic", "doctors", "hospital", "pharmacy", "physiotherapist", "orthodontist"), "household_spend", "Health", "", 0.86),
    (("amenity",), ("restaurant", "cafe", "bar", "pub", "fast_food", "ice_cream", "food_court"), "household_spend", "Eating Out", "", 0.84),
    (("shop",), ("supermarket", "convenience", "greengrocer", "bakery", "butcher", "deli", "cheese", "seafood"), "household_spend", "Groceries", "", 0.84),
    (("tourism", "aeroway"), ("hotel", "hostel", "apartment", "guest_house", "camp_site", "travel_agency", "terminal"), "household_spend", "Holiday", "", 0.84),
    (("amenity", "railway", "public_transport"), ("fuel", "parking", "taxi", "bicycle_rental", "station", "tram_stop", "bus_station", "platform"), "household_spend", "Transportation", "", 0.80),
    (("shop",), ("clothes", "shoes", "department_store", "furniture", "electronics", "doityourself", "hardware", "books", "gift", "sports", "mall"), "household_spend", "Shopping", "", 0.80),
    (("office", "amenity"), ("insurance", "bank", "financial_advice"), "household_spend", "Banking and Fees", "", 0.70),
    (("office", "amenity"), ("government", "townhall", "courthouse", "post_office"), "household_spend", "Taxes and Government", "", 0.74),
    (("craft",), ("*",), "household_spend", "Other", "Local Service", 0.68),
    (("shop", "amenity"), ("*",), "household_spend", "Other", "Local Merchant", 0.62),
]


@dataclass
class EntityHint:
    economic_class: str
    category: str
    subcategory: str = ""
    confidence: float = 0.0
    label: str = ""
    description: str = ""
    source: str = "wikidata"
    source_url: str = ""
    raw: Optional[Dict[str, Any]] = None


def lookup_key(merchant_name: str) -> str:
    return re.sub(r"\s+", " ", (merchant_name or "").strip().upper())


def is_lookup_safe(merchant_name: str) -> bool:
    key = lookup_key(merchant_name)
    if key in GENERIC_LOOKUP_NAMES:
        return False
    if len(key) < 4 or len(key) > 80:
        return False
    if re.search(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b", key):
        return False
    if re.search(r"\d{5,}", key):
        return False
    if sum(char.isdigit() for char in key) >= 4:
        return False
    if any(re.search(pattern, key) for pattern in UNSAFE_LOOKUP_PATTERNS):
        return False
    if not re.search(r"[A-Z]{3}", key):
        return False
    return True


def load_entity_hints(conn: sqlite3.Connection) -> Dict[str, EntityHint]:
    rows = conn.execute(
        """
        SELECT *
        FROM entity_enrichment_cache
        WHERE status = 'resolved'
          AND category IS NOT NULL
          AND category != ''
        """
    ).fetchall()
    hints = {}
    for row in rows:
        hints[row["lookup_key"]] = EntityHint(
            economic_class=row["economic_class"] or "household_spend",
            category=row["category"],
            subcategory=row["subcategory"] or "",
            confidence=float(row["confidence"] or 0.0),
            label=row["label"] or "",
            description=row["description"] or "",
            source=row["source"] or "wikidata",
            source_url=row["source_url"] or "",
            raw=json_loads(row["raw_json"], None),
        )
    return hints


def cached_hint_for_merchant(hints: Dict[str, EntityHint], merchant_name: str) -> Optional[EntityHint]:
    return hints.get(lookup_key(merchant_name))


def classify_from_public_entity(search_results: Iterable[Dict[str, Any]]) -> Optional[EntityHint]:
    for result in search_results:
        label = str(result.get("label") or "")
        description = str(result.get("description") or "")
        aliases = " ".join(str(alias) for alias in result.get("aliases") or [])
        haystack = f"{label} {description} {aliases}".lower()
        for signals, economic_class, category, subcategory, confidence in CATEGORY_SIGNALS:
            if any(signal in haystack for signal in signals):
                entity_id = str(result.get("id") or "")
                source_url = f"https://www.wikidata.org/wiki/{entity_id}" if entity_id else "https://www.wikidata.org/"
                return EntityHint(
                    economic_class=economic_class,
                    category=category,
                    subcategory=subcategory,
                    confidence=confidence,
                    label=label,
                    description=description,
                    source="wikidata",
                    source_url=source_url,
                    raw=result,
                )
    return None


def classify_from_osm_place(search_results: Iterable[Dict[str, Any]]) -> Optional[EntityHint]:
    for result in search_results:
        tag_pairs = osm_tag_pairs(result)
        for tag_key, tag_value in tag_pairs:
            for key_signals, value_signals, economic_class, category, subcategory, confidence in OSM_TAG_RULES:
                if tag_key not in key_signals:
                    continue
                if "*" not in value_signals and tag_value not in value_signals:
                    continue
                osm_type = str(result.get("osm_type") or "").strip()
                osm_id = str(result.get("osm_id") or "").strip()
                source_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_type and osm_id else "https://www.openstreetmap.org/"
                label = str(result.get("name") or result.get("display_name") or "")
                description = f"OSM {tag_key}={tag_value}"
                return EntityHint(
                    economic_class=economic_class,
                    category=category,
                    subcategory=subcategory,
                    confidence=confidence,
                    label=label,
                    description=description,
                    source="openstreetmap_nominatim",
                    source_url=source_url,
                    raw=result,
                )
    return None


def osm_tag_pairs(result: Dict[str, Any]) -> List[Tuple[str, str]]:
    pairs = []
    for key in ("category", "class", "addresstype"):
        value = str(result.get(key) or "").lower()
        type_value = str(result.get("type") or "").lower()
        if value and type_value:
            pairs.append((value, type_value))
    for container_name in ("extratags", "address", "namedetails"):
        container = result.get(container_name) or {}
        if not isinstance(container, dict):
            continue
        for key, value in container.items():
            if not isinstance(value, str):
                continue
            pairs.append((str(key).lower(), value.lower()))
    return pairs


def nominatim_search(merchant_name: str, fetch_json: Optional[Callable[[str], Any]] = None) -> List[Dict[str, Any]]:
    params = urllib.parse.urlencode(
        {
            "q": f"{lookup_key(merchant_name)}, Netherlands",
            "format": "jsonv2",
            "limit": "5",
            "countrycodes": "nl",
            "layer": "poi",
            "addressdetails": "1",
            "extratags": "1",
            "namedetails": "1",
            "accept-language": "nl,en",
        }
    )
    url = f"{NOMINATIM_SEARCH_URL}?{params}"
    payload = fetch_json(url) if fetch_json else fetch_url_json(url)
    return payload if isinstance(payload, list) else []


def wikidata_search(merchant_name: str, fetch_json: Optional[Callable[[str], Any]] = None) -> List[Dict[str, Any]]:
    search = lookup_key(merchant_name)
    results: List[Dict[str, Any]] = []
    seen = set()
    for language in ("nl", "en"):
        params = urllib.parse.urlencode(
            {
                "action": "wbsearchentities",
                "format": "json",
                "language": language,
                "uselang": "en",
                "type": "item",
                "limit": "5",
                "search": search,
            }
        )
        url = f"{WIKIDATA_SEARCH_URL}?{params}"
        payload = fetch_json(url) if fetch_json else fetch_url_json(url)
        for result in payload.get("search", []):
            entity_id = result.get("id")
            if entity_id in seen:
                continue
            seen.add(entity_id)
            results.append(result)
    return results


def fetch_url_json(url: str) -> Dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=LOOKUP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def resolve_merchant(
    conn: sqlite3.Connection,
    merchant_name: str,
    fetch_json: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    key = lookup_key(merchant_name)
    existing = conn.execute(
        "SELECT * FROM entity_enrichment_cache WHERE lookup_key = ?",
        (key,),
    ).fetchone()
    if existing:
        return {"status": "cached", "lookup_key": key, "category": existing["category"], "source": existing["source"]}
    if not is_lookup_safe(merchant_name):
        store_entity_result(conn, key, merchant_name, "skipped", None, None)
        return {"status": "skipped", "lookup_key": key}
    raw: Dict[str, Any] = {}
    try:
        osm_results = nominatim_search(merchant_name, fetch_json=fetch_json)
        raw["openstreetmap_nominatim"] = osm_results
        hint = classify_from_osm_place(osm_results)
    except Exception as exc:  # pragma: no cover - network boundary
        raw["openstreetmap_nominatim_error"] = str(exc)
        hint = None
    if not hint:
        try:
            wikidata_results = wikidata_search(merchant_name, fetch_json=fetch_json)
            raw["wikidata"] = wikidata_results
            hint = classify_from_public_entity(wikidata_results)
        except Exception as exc:  # pragma: no cover - network boundary
            raw["wikidata_error"] = str(exc)
    if hint:
        store_entity_result(conn, key, merchant_name, "resolved", hint, raw)
        return {
            "status": "resolved",
            "lookup_key": key,
            "category": hint.category,
            "subcategory": hint.subcategory,
            "confidence": hint.confidence,
            "source": hint.source,
        }
    if "openstreetmap_nominatim_error" in raw and "wikidata_error" in raw:
        store_entity_result(conn, key, merchant_name, "error", None, raw)
        return {"status": "error", "lookup_key": key, "error": "; ".join(str(value) for value in raw.values())}
    store_entity_result(conn, key, merchant_name, "unresolved", None, raw)
    return {"status": "unresolved", "lookup_key": key}


def store_entity_result(
    conn: sqlite3.Connection,
    key: str,
    merchant_name: str,
    status: str,
    hint: Optional[EntityHint],
    raw: Optional[Dict[str, Any]],
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO entity_enrichment_cache (
            lookup_key, merchant_name, source, source_url, label, description,
            economic_class, category, subcategory, confidence, status, raw_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            key,
            merchant_name,
            hint.source if hint else "provider_chain",
            hint.source_url if hint else "",
            hint.label if hint else "",
            hint.description if hint else "",
            hint.economic_class if hint else "",
            hint.category if hint else "",
            hint.subcategory if hint else "",
            hint.confidence if hint else 0.0,
            status,
            json_dumps(raw or {}),
        ),
    )


def candidate_merchants_for_enrichment(conn: sqlite3.Connection, limit: int = 50) -> List[str]:
    rows = conn.execute(
        """
        SELECT nt.normalized_merchant, SUM(ABS(nt.amount)) AS materiality, COUNT(*) AS count
        FROM normalized_transactions nt
        JOIN transaction_annotations ta ON ta.transaction_id = nt.id
        LEFT JOIN entity_enrichment_cache eec ON eec.lookup_key = nt.normalized_merchant
        WHERE nt.is_duplicate = 0
          AND nt.normalized_merchant IS NOT NULL
          AND nt.normalized_merchant != ''
          AND eec.lookup_key IS NULL
          AND (
            ta.economic_class = 'needs_review'
            OR (ta.category = 'Uncategorized' AND ABS(nt.amount) >= 50)
          )
        GROUP BY nt.normalized_merchant
        ORDER BY materiality DESC, count DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [row["normalized_merchant"] for row in rows if is_lookup_safe(row["normalized_merchant"])]


def enrich_candidate_merchants(conn: sqlite3.Connection, limit: int = 10) -> Dict[str, Any]:
    merchants = candidate_merchants_for_enrichment(conn, limit)
    summary = {"candidates": len(merchants), "resolved": 0, "cached": 0, "unresolved": 0, "skipped": 0, "error": 0}
    for index, merchant in enumerate(merchants):
        if index > 0:
            time.sleep(NOMINATIM_DELAY_SECONDS)
        result = resolve_merchant(conn, merchant)
        status = result["status"]
        summary[status] = summary.get(status, 0) + 1
    conn.commit()
    return summary
