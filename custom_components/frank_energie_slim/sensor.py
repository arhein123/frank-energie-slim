from __future__ import annotations

import logging
from datetime import timedelta, datetime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, CoordinatorEntity
from homeassistant.const import CONF_USERNAME, CONF_PASSWORD
from homeassistant.config_entries import ConfigEntry

from .api import FrankEnergie

_LOGGER = logging.getLogger(__name__)

DOMAIN = "frank_energie_slim"
SCAN_INTERVAL = timedelta(minutes=15)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up sensors from a config entry."""
    username = entry.data.get(CONF_USERNAME)
    password = entry.data.get(CONF_PASSWORD)

    client = FrankEnergie()

    def _login_blocking():
        # login in executor om I/O te vermijden in event loop
        return client.login(username, password)

    def _fetch_batteries_blocking():
        return client.get_smart_batteries()

    def _fetch_summary_blocking(device_id: str):
        return client.get_smart_battery_summary(device_id)

    def _fetch_sessions_blocking(device_id: str, start: datetime, end: datetime):
        return client.get_smart_battery_sessions(device_id, start, end)

    # Login (blocking in executor)
    auth = await hass.async_add_executor_job(_login_blocking)
    if not auth:
        _LOGGER.error("Login mislukt, stop setup")
        return

    # Haal batterijen
    batteries_resp = await hass.async_add_executor_job(_fetch_batteries_blocking)
    device_ids = []
    try:
        device_ids = [b["id"] for b in (batteries_resp.get("data") or {}).get("smartBatteries", [])]
    except Exception as e:
        _LOGGER.error("Kon smartBatteries niet parsen: %s, resp=%r", e, batteries_resp)
        return

    if not device_ids:
        _LOGGER.warning("Geen smartBatteries gevonden voor deze account")
        return

    # Zet per batterij een coordinator + entity op
    entities = []
    for dev_id in device_ids:
        coordinator = FrankBatteryCoordinator(
            hass=hass,
            logger=_LOGGER,
            client=client,
            device_id=dev_id,
        )
        await coordinator.async_config_entry_first_refresh()
        entities.append(FrankBatterySummarySensor(coordinator, dev_id))
        entities.append(FrankBatteryTradingResultSensor(coordinator, dev_id))

    async_add_entities(entities)


class FrankBatteryCoordinator(DataUpdateCoordinator):
    """Coordinator die summary en recente sessions ophaalt."""

    def __init__(self, hass: HomeAssistant, logger: logging.Logger, client: FrankEnergie, device_id: str):
        super().__init__(hass, logger, name=f"{DOMAIN}_{device_id}", update_interval=SCAN_INTERVAL)
        self._client = client
        self.device_id = device_id
        self.summary = None
        self.sessions_node = None

    async def _async_update_data(self):
        def _fetch():
            # Summary
            summary = self._client.get_smart_battery_summary(self.device_id)

            # Sessions van de laatste 7 dagen
            end = datetime.utcnow()
            start = end - timedelta(days=7)
            sessions = self._client.get_smart_battery_sessions(self.device_id, start, end)

            return {"summary": summary, "sessions": sessions}

        data = await self.hass.async_add_executor_job(_fetch)

        # Defensief parsen
        self.summary = ((data.get("summary") or {}).get("data") or {}).get("smartBatterySummary")
        node = ((data.get("sessions") or {}).get("data") or {}).get("smartBatterySessions")

        if not node:
            _LOGGER.warning("Geen smartBatterySessions in response voor %s", self.device_id)
            self.sessions_node = None
            return data

        # Nieuwe veldnamen: cumulativeResult/result
        sessions = (node.get("sessions") or []) if isinstance(node, dict) else []
        self.sessions_node = {
            "periodTradingResult": node.get("periodTradingResult"),
            "sessions": sessions,
        }
        return data


class FrankBatterySummarySensor(CoordinatorEntity,):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FrankBatteryCoordinator, device_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_name = f"Frank Smart Battery {device_id} Status"
        self._attr_unique_id = f"{DOMAIN}_{device_id}_status"

    @property
    def state(self):
        summary = self.coordinator.summary or {}
        return summary.get("lastKnownStatus")

    @property
    def extra_state_attributes(self):
        s = self.coordinator.summary or {}
        return {
            "soc": s.get("lastKnownStateOfCharge"),
            "last_update": s.get("lastUpdate"),
            "total_result": s.get("totalResult"),
        }


class FrankBatteryTradingResultSensor(CoordinatorEntity,):
    _attr_has_entity_name = True

    def __init__(self, coordinator: FrankBatteryCoordinator, device_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_name = f"Frank Smart Battery {device_id} Trading Result"
        self._attr_unique_id = f"{DOMAIN}_{device_id}_trading_result"

    @property
    def state(self):
        node = self.coordinator.sessions_node or {}
        # Fallback: laatste cumulatief indien periodTradingResult ontbreekt
        if node.get("periodTradingResult") is not None:
            return node["periodTradingResult"]
        sessions = node.get("sessions") or []
        if sessions:
            last = sessions[-1]
            return last.get("cumulativeResult")
        return None

    @property
    def extra_state_attributes(self):
        node = self.coordinator.sessions_node or {}
        sessions = node.get("sessions") or []
        return {
            "sessions_count": len(sessions),
            "last_session_result": (sessions[-1].get("result") if sessions else None),
            "last_session_status": (sessions[-1].get("status") if sessions else None),
        }
