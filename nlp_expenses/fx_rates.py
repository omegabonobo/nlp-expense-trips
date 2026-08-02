from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FX_CACHE_FILE = ".nlp-expenses-fx-rates.json"
BANK_OF_CANADA_API_BASE = "https://www.bankofcanada.ca/valet/observations"
BANK_OF_CANADA_BACKGROUND_URL = (
    "https://www.bankofcanada.ca/rates/exchange/background-information-on-foreign-exchange-rates/"
)
QATAR_CENTRAL_BANK_PEG_URL = "https://www.qcb.gov.qa/en/Pages/MonetaryPolicyTools.aspx"
QAR_PER_USD = 3.64


class FxRateUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class WeeklyCadRate:
    currency: str
    week_start: str
    week_end: str
    cad_per_unit: float
    method: str
    route: str
    source: str
    source_urls: list[str]
    observations: list[dict[str, object]]
    fetched_at: str


FetchJson = Callable[[str], dict]


class WeeklyCadFxResolver:
    """Resolve and cache auditable weekly foreign-currency rates into CAD."""

    def __init__(self, trip_dir: Path, fetch_json: FetchJson | None = None):
        self.trip_dir = trip_dir.resolve()
        self.cache_path = self.trip_dir / FX_CACHE_FILE
        self.fetch_json = fetch_json or fetch_json_url
        self.cache = load_fx_cache(self.cache_path)

    def resolve(self, currency: str, transaction_date: str) -> WeeklyCadRate:
        normalized_currency = str(currency or "").upper()
        if len(normalized_currency) != 3:
            raise FxRateUnavailable("A valid three-letter purchase currency is required.")
        try:
            occurred_on = date.fromisoformat(transaction_date)
        except (TypeError, ValueError) as exc:
            raise FxRateUnavailable("A valid transaction date is required for weekly FX.") from exc
        week_start = occurred_on - timedelta(days=occurred_on.weekday())
        week_end = week_start + timedelta(days=6)
        if normalized_currency == "CAD":
            return WeeklyCadRate(
                currency="CAD",
                week_start=week_start.isoformat(),
                week_end=week_end.isoformat(),
                cad_per_unit=1.0,
                method="identity",
                route="CAD→CAD",
                source="Transaction currency is CAD",
                source_urls=[],
                observations=[],
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )

        key = cache_key(normalized_currency, week_start)
        cached = self.cache.get("rates", {}).get(key)
        if isinstance(cached, dict):
            try:
                return weekly_rate_from_dict(cached)
            except (KeyError, TypeError, ValueError):
                pass

        rate = self._fetch_rate(normalized_currency, week_start, week_end)
        self.cache.setdefault("rates", {})[key] = asdict(rate)
        save_fx_cache(self.cache_path, self.cache)
        return rate

    def _fetch_rate(self, currency: str, week_start: date, week_end: date) -> WeeklyCadRate:
        fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if currency == "QAR":
            usd_rate, observations, api_url = self._bank_of_canada_weekly_average(
                "USD",
                week_start,
                week_end,
            )
            return WeeklyCadRate(
                currency=currency,
                week_start=week_start.isoformat(),
                week_end=week_end.isoformat(),
                cad_per_unit=round(usd_rate / QAR_PER_USD, 10),
                method="weekly_average_official_peg",
                route="QAR→USD→CAD",
                source="Qatar Central Bank 3.64 QAR/USD peg + Bank of Canada weekly USD/CAD average",
                source_urls=[QATAR_CENTRAL_BANK_PEG_URL, api_url, BANK_OF_CANADA_BACKGROUND_URL],
                observations=observations,
                fetched_at=fetched_at,
            )

        rate, observations, api_url = self._bank_of_canada_weekly_average(
            currency,
            week_start,
            week_end,
        )
        return WeeklyCadRate(
            currency=currency,
            week_start=week_start.isoformat(),
            week_end=week_end.isoformat(),
            cad_per_unit=rate,
            method="weekly_average_direct",
            route=f"{currency}→CAD",
            source=f"Bank of Canada weekly {currency}/CAD average",
            source_urls=[api_url, BANK_OF_CANADA_BACKGROUND_URL],
            observations=observations,
            fetched_at=fetched_at,
        )

    def _bank_of_canada_weekly_average(
        self,
        currency: str,
        week_start: date,
        week_end: date,
    ) -> tuple[float, list[dict[str, object]], str]:
        series = f"FX{currency}CAD"
        api_url = (
            f"{BANK_OF_CANADA_API_BASE}/{series}/json"
            f"?start_date={week_start.isoformat()}&end_date={week_end.isoformat()}"
        )
        try:
            payload = self.fetch_json(api_url)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise FxRateUnavailable(
                f"Bank of Canada did not provide a weekly {currency}/CAD rate for "
                f"{week_start.isoformat()} to {week_end.isoformat()}."
            ) from exc
        observations: list[dict[str, object]] = []
        for item in payload.get("observations", []):
            value = item.get(series, {}).get("v") if isinstance(item, dict) else None
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                continue
            observations.append({"date": item.get("d"), "cad_per_unit": parsed})
        if not observations:
            raise FxRateUnavailable(
                f"Bank of Canada has no {currency}/CAD observations for "
                f"{week_start.isoformat()} to {week_end.isoformat()}."
            )
        average = sum(float(item["cad_per_unit"]) for item in observations) / len(observations)
        return round(average, 10), observations, api_url


def fetch_json_url(url: str) -> dict:
    request = Request(url, headers={"User-Agent": "nlp-expenses/0.1 weekly-cad-fx"})
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def cache_key(currency: str, week_start: date) -> str:
    return f"{currency}:{week_start.isoformat()}"


def load_fx_cache(path: Path) -> dict:
    if not path.is_file():
        return {"version": 1, "rates": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "rates": {}}
    if not isinstance(data, dict):
        return {"version": 1, "rates": {}}
    data.setdefault("version", 1)
    data.setdefault("rates", {})
    return data


def save_fx_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{FX_CACHE_FILE}.{uuid.uuid4().hex}.tmp"
    try:
        temporary.write_text(
            json.dumps(cache, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def weekly_rate_from_dict(value: dict) -> WeeklyCadRate:
    return WeeklyCadRate(
        currency=str(value["currency"]),
        week_start=str(value["week_start"]),
        week_end=str(value["week_end"]),
        cad_per_unit=float(value["cad_per_unit"]),
        method=str(value["method"]),
        route=str(value["route"]),
        source=str(value["source"]),
        source_urls=[str(item) for item in value.get("source_urls", [])],
        observations=[dict(item) for item in value.get("observations", [])],
        fetched_at=str(value["fetched_at"]),
    )
