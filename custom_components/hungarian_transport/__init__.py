"""Home Assistant integration for Hungarian transport services and card assets."""

from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_ELVIRA_BETWEEN
from .elvira import elvira_between

ELVIRA_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("origin_code"): cv.string,
        vol.Required("dest_code"): cv.string,
        vol.Optional(
            "minutes_after", default=180
        ): vol.All(cv.positive_int, vol.Range(min=15, max=480)),
        vol.Optional("dest_name", default=""): cv.string,
    }
)
CARD_URL = "/hungarian-transport/hungarian-transport.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the card asset path and ELVIRA service."""
    www_path = Path(__file__).parent / "www"
    if not www_path.is_dir():
        raise FileNotFoundError(
            f"Hungarian transport release is missing its frontend assets: {www_path}"
        )

    if hasattr(hass.http, "async_register_static_paths"):
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    url_path="/hungarian-transport",
                    path=str(www_path),
                    cache_headers=False,
                )
            ]
        )
    else:
        hass.http.register_static_path(
            "/hungarian-transport", str(www_path), cache_headers=False
        )
    session = async_get_clientsession(hass)

    async def handle_elvira_between(
        call: ServiceCall,
    ) -> dict[str, list[dict[str, Any]]]:
        rows = await elvira_between(
            session,
            hass,
            call.data["origin_code"],
            call.data["dest_code"],
            call.data["minutes_after"],
            call.data["dest_name"],
        )
        return {"departures": rows}

    hass.services.async_register(
        DOMAIN,
        SERVICE_ELVIRA_BETWEEN,
        handle_elvira_between,
        schema=ELVIRA_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load the card module."""
    frontend.add_extra_js_url(hass, CARD_URL)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    if hasattr(frontend, "remove_extra_js_url"):
        frontend.remove_extra_js_url(hass, CARD_URL)
    else:
        hass.data[frontend.DATA_EXTRA_MODULE_URL].remove(CARD_URL)
    return True


CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)
