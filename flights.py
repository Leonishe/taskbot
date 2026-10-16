"""Цены на авиабилеты через Travelpayouts (данные Aviasales). Цены из кэша за последние дни."""
import httpx

BASE = "https://api.travelpayouts.com/aviasales/v3"


class Flights:
    def __init__(self, token: str, currency: str = "usd"):
        self.token = token
        self.currency = currency

    async def _get(self, path: str, params: dict) -> dict:
        params = {k: v for k, v in params.items() if v not in (None, "")}
        params.setdefault("currency", self.currency)
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/{path}", params=params, headers={"X-Access-Token": self.token})
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _fmt(t: dict) -> dict:
        out = {
            "price": t.get("price"),
            "airline": t.get("airline"),
            "flight_number": t.get("flight_number"),
            "origin_airport": t.get("origin_airport") or t.get("origin"),
            "destination_airport": t.get("destination_airport") or t.get("destination"),
            "departure_at": t.get("departure_at"),
            "transfers": t.get("transfers"),
            "duration_to_min": t.get("duration_to") or t.get("duration"),
        }
        if t.get("return_at"):
            out.update(return_at=t.get("return_at"), return_transfers=t.get("return_transfers"),
                       duration_back_min=t.get("duration_back"))
        if t.get("link"):
            out["link"] = "https://www.aviasales.com" + t["link"]
        return out

    async def search(self, origin: str, destination: str, departure_at: str,
                     return_at: str | None = None, direct: bool = False, limit: int = 10) -> dict:
        data = await self._get("prices_for_dates", {
            "origin": origin, "destination": destination,
            "departure_at": departure_at, "return_at": return_at,
            "one_way": "false" if return_at else "true",
            "direct": str(bool(direct)).lower(), "sorting": "price",
            "unique": "false", "limit": limit, "page": 1,
        })
        if not data.get("success", True):
            return {"error": data.get("error") or "ошибка API"}
        return {"currency": self.currency.upper(), "results": [self._fmt(t) for t in data.get("data", [])]}

    async def cheapest_days(self, origin: str, destination: str, month: str,
                            min_days: int | None = None, max_days: int | None = None,
                            direct: bool = False) -> dict:
        data = await self._get("grouped_prices", {
            "origin": origin, "destination": destination, "departure_at": month,
            "group_by": "departure_at", "direct": str(bool(direct)).lower(),
            "min_trip_duration": min_days, "max_trip_duration": max_days,
        })
        if not data.get("success", True):
            return {"error": data.get("error") or "ошибка API"}
        days = data.get("data") or {}
        items = sorted((self._fmt(v) for v in days.values()), key=lambda x: x["price"] or 1e9)
        return {"currency": self.currency.upper(), "by_day_cheapest_first": items[:20]}
