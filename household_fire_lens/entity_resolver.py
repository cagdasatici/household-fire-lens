from __future__ import annotations

import json
import re
import sqlite3
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from .database import json_dumps, json_loads


WIKIDATA_SEARCH_URL = "https://www.wikidata.org/w/api.php"
USER_AGENT = "HouseholdFIRELens/0.1 (local personal finance entity classification)"
LOOKUP_TIMEOUT_SECONDS = 5

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


def wikidata_search(merchant_name: str, fetch_json: Optional[Callable[[str], Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
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
    fetch_json: Optional[Callable[[str], Dict[str, Any]]] = None,
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
    try:
        results = wikidata_search(merchant_name, fetch_json=fetch_json)
    except Exception as exc:  # pragma: no cover - network boundary
        store_entity_result(conn, key, merchant_name, "error", None, {"error": str(exc)})
        return {"status": "error", "lookup_key": key, "error": str(exc)}
    hint = classify_from_public_entity(results)
    if hint:
        store_entity_result(conn, key, merchant_name, "resolved", hint, {"search": results})
        return {
            "status": "resolved",
            "lookup_key": key,
            "category": hint.category,
            "subcategory": hint.subcategory,
            "confidence": hint.confidence,
            "source": hint.source,
        }
    store_entity_result(conn, key, merchant_name, "unresolved", None, {"search": results})
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
            hint.source if hint else "wikidata",
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


def enrich_candidate_merchants(conn: sqlite3.Connection, limit: int = 50) -> Dict[str, Any]:
    merchants = candidate_merchants_for_enrichment(conn, limit)
    summary = {"candidates": len(merchants), "resolved": 0, "cached": 0, "unresolved": 0, "skipped": 0, "error": 0}
    for merchant in merchants:
        result = resolve_merchant(conn, merchant)
        status = result["status"]
        summary[status] = summary.get(status, 0) + 1
    conn.commit()
    return summary
