import requests
from datetime import datetime
import logging

_LOGGER = logging.getLogger(__name__)


class FrankEnergie:
    """
    Eenvoudige client voor de Frank GraphQL API.
    - Login mutation levert authToken + refreshToken.
    - Alle vervolgqueries gebruiken Bearer <authToken>.
    """

    DATA_URL = "https://frank-graphql-prod.graphcdn.app/"

    def __init__(self, auth_token=None, refresh_token=None):
        # Gebruik dezelfde keys als de GraphQL-respons
        self.auth = (
            {"authToken": auth_token, "refreshToken": refresh_token}
            if auth_token or refresh_token
            else None
        )

    def _headers(self):
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Python/FrankV1",
        }
        token = None
        if self.auth:
            # Support zowel camelCase als snake_case voor achterwaartse compatibiliteit
            token = self.auth.get("authToken") or self.auth.get("auth_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def query(self, query_data: dict) -> dict:
        resp = requests.post(self.DATA_URL, json=query_data, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Heldere fout als auth vereist is
        if isinstance(data, dict) and "errors" in data:
            for err in data.get("errors") or []:
                msg = err.get("message")
                if msg == "user-error:auth-not-authorised":
                    raise Exception("Authentication required")
            # Log andere GraphQL errors
            _LOGGER.debug("GraphQL errors: %s", data["errors"])
        return data

    # ---------- Auth ----------
    def login(self, username: str, password: str) -> dict:
        payload = {
            "query": """
                mutation Login($email: String!, $password: String!) {
                  login(email: $email, password: $password) {
                    authToken
                    refreshToken
                  }
                }
            """,
            "operationName": "Login",
            "variables": {"email": username, "password": password},
        }
        data = self.query(payload)
        if not data or "data" not in data or not data["data"].get("login"):
            # Toon eerste error indien aanwezig
            if data and data.get("errors"):
                raise Exception(data["errors"][0].get("message", "Login failed"))
            raise Exception("Login failed: empty response")
        self.auth = data["data"]["login"]
        return self.auth

    # ---------- Queries ----------
    def get_smart_batteries(self) -> dict:
        if not self.auth:
            raise Exception("Authentication required")
        payload = {
            "query": """
                query SmartBatteries {
                  smartBatteries { id }
                }
            """,
            "operationName": "SmartBatteries",
        }
        return self.query(payload)

    def get_smart_battery_summary(self, device_id: str) -> dict:
        if not self.auth:
            raise Exception("Authentication required")
        payload = {
            "query": """
                query SmartBatterySummary($deviceId: String!) {
                  smartBatterySummary(deviceId: $deviceId) {
                    lastKnownStateOfCharge
                    lastKnownStatus
                    lastUpdate
                    totalResult
                  }
                }
            """,
            "operationName": "SmartBatterySummary",
            "variables": {"deviceId": device_id},
        }
        return self.query(payload)

    def get_smart_battery(self, device_id: str) -> dict:
        if not self.auth:
            raise Exception("Authentication required")
        payload = {
            "query": """
                query SmartBattery($deviceId: String!) {
                  smartBattery(deviceId: $deviceId) {
                    brand
                    capacity
                    id
                    settings {
                      batteryMode
                      imbalanceTradingStrategy
                      selfConsumptionTradingAllowed
                    }
                  }
                }
            """,
            "operationName": "SmartBattery",
            "variables": {"deviceId": device_id},
        }
        return self.query(payload)

    def get_smart_battery_sessions(self, device_id, start_date, end_date) -> dict:
        """
        Nieuwe schema: 'cumulativeResult' en 'result' i.p.v. 'cumulativeTradingResult' en 'tradingResult'.
        """
        if not self.auth:
            raise Exception("Authentication required")
        payload = {
            "query": """
                query SmartBatterySessions($startDate: String!, $endDate: String!, $deviceId: String!) {
                  smartBatterySessions(
                    startDate: $startDate
                    endDate: $endDate
                    deviceId: $deviceId
                  ) {
                    deviceId
                    fairUsePolicyVerified
                    periodStartDate
                    periodEndDate
                    periodEpexResult
                    periodFrankSlim
                    periodImbalanceResult
                    periodTotalResult
                    periodTradeIndex
                    periodTradingResult
                    sessions {
                      cumulativeResult
                      date
                      result
                      status
                      tradeIndex
                    }
                  }
                }
            """,
            "operationName": "SmartBatterySessions",
            "variables": {
                "deviceId": device_id,
                "startDate": start_date.strftime("%Y-%m-%d"),
                "endDate": end_date.strftime("%Y-%m-%d"),
            },
        }
        return self.query(payload)

    # Handige helper
    def is_authenticated(self) -> bool:
        return bool(self.auth and (self.auth.get("authToken") or self.auth.get("auth_token")))