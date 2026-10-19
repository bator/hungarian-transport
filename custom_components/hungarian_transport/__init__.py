"""Home Assistant integration for Hungarian transport services and card assets."""

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import mav
from .const import CONF_API_KEY, DOMAIN, SERVICE_ELVIRA_BETWEEN, SERVICE_FUTAR, SERVICE_REBUILD_MAV
from .elvira import ElviraError, elvira_between, station_code_digits
from .futar import FUTAR_PATHS, FutarAuthError, FutarError, FutarProxy

_LOGGER = logging.getLogger(__name__)

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
FUTAR_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("path"): vol.In(FUTAR_PATHS),
        vol.Optional("params", default=dict): vol.Schema(
            {cv.string: vol.Any(cv.string, int, float, bool)}
        ),
    }
)
CARD_URL = "/hungarian-transport/hungarian-transport.js"
# Imports the bundle and retries a failed download; Home Assistant never
# retries an extra module, so a dropped download would leave every card on
# "Configuration error" until a full reload.
LOADER_FILE = "hungarian-transport-loader.js"
LOADER_URL = f"/hungarian-transport/{LOADER_FILE}"
WWW_PATH = Path(__file__).parent / "www"
ISSUE_MISSING_ASSETS = "missing_assets"


def _entry_loaded(hass: HomeAssistant) -> bool:
    return any(
        entry.state is ConfigEntryState.LOADED
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


def _require_entry(hass: HomeAssistant) -> None:
    """The services and the upload stay registered after the entry is
    unloaded (disabled); they refuse until it is loaded again."""
    if not _entry_loaded(hass):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")


def _station_code(call: ServiceCall, field: str) -> str:
    code = station_code_digits(call.data[field])
    if len(code) != 9:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_station_code",
            translation_placeholders={"field": field, "value": str(call.data[field])},
        )
    return code


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the card asset path and ELVIRA service."""
    www_path = WWW_PATH
    if not await hass.async_add_executor_job(www_path.is_dir):
        # A git checkout (or a broken HACS download) has no www/: the release
        # zip ships it. Say so in Repairs instead of a traceback at startup.
        _LOGGER.error(
            "Hungarian transport is missing its frontend assets at %s; "
            "install the release zip, not the source tree",
            www_path,
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_MISSING_ASSETS,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_MISSING_ASSETS,
            translation_placeholders={"path": str(www_path)},
        )
        return False
    ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING_ASSETS)

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                url_path="/hungarian-transport",
                path=str(www_path),
                cache_headers=False,
            )
        ]
    )
    session = async_get_clientsession(hass)

    async def handle_elvira_between(call: ServiceCall) -> dict[str, Any]:
        _require_entry(hass)
        origin = _station_code(call, "origin_code")
        dest = _station_code(call, "dest_code")
        if origin == dest:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="same_station",
            )
        try:
            rows = await elvira_between(
                session,
                hass,
                origin,
                dest,
                call.data["minutes_after"],
                call.data["dest_name"],
            )
        except ElviraError as err:
            # An outage, not an empty board, but no exception either: the
            # websocket API logs every failed call at ERROR, and every card
            # polls every 45 s. elvira.py logs the outage once.
            return {"departures": [], "error": "elvira_unavailable", "detail": str(err)}
        return {"departures": rows}

    hass.services.async_register(
        DOMAIN,
        SERVICE_ELVIRA_BETWEEN,
        handle_elvira_between,
        schema=ELVIRA_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # MÁV GTFS is registration-only, so the rail index is built here from the
    # feed the user supplies (see mav.py), and checked trip by trip.
    await mav.async_remove_stale_uploads(hass)
    hass.http.register_view(mav.MavGtfsUploadView(www_path, _entry_loaded))

    async def handle_rebuild_mav(call: ServiceCall) -> dict[str, Any]:
        _require_entry(hass)
        try:
            stats = await mav.async_build(hass, www_path, force=True)
        except mav.MavSourceMissing as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="mav_source_missing",
                translation_placeholders={"path": str(mav.source_path(hass))},
            ) from err
        except Exception as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mav_build_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        return stats or {}

    # A rebuild reads and checks the whole feed (a minute of CPU on a small
    # host): admins only, like the upload.
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_REBUILD_MAV,
        handle_rebuild_mav,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _build_mav_at_start(_hass: HomeAssistant) -> None:
        hass.async_create_background_task(
            mav.async_build_if_needed(hass, www_path), "hungarian_transport_mav_index"
        )

    async_at_started(hass, _build_mav_at_start)

    integration = await async_get_integration(hass, DOMAIN)
    has_loader = await hass.async_add_executor_job((www_path / LOADER_FILE).is_file)
    entry_url = LOADER_URL if has_loader else CARD_URL
    card_url = f"{entry_url}?v={integration.version}"
    hass.data.setdefault(DOMAIN, {})["card_url"] = card_url
    frontend.add_extra_js_url(hass, card_url)
    return True


def _entry_api_key(entry: ConfigEntry) -> str:
    # The key has only ever been stored in the options.
    return str(entry.options.get(CONF_API_KEY) or "").strip()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration entry."""
    frontend.add_extra_js_url(hass, hass.data[DOMAIN]["card_url"])
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    api_key = _entry_api_key(entry)
    if not api_key:
        return True
    # The card switches to this service when it exists, so it is registered
    # only while a key is configured.
    proxy = FutarProxy(
        async_get_clientsession(hass),
        api_key,
        task_factory=lambda coro: hass.async_create_background_task(
            coro, "hungarian_transport_futar", eager_start=False
        ),
    )

    async def handle_futar(call: ServiceCall) -> dict[str, Any]:
        # Errors are part of the response, not exceptions: the websocket API
        # logs every failed call at ERROR, and cards poll every 15 s. The
        # proxy logs an outage or a rejected key once.
        try:
            data = await proxy.get(call.data["path"], call.data["params"])
        except FutarAuthError as err:
            # The stored key stopped working (revoked, expired): ask for a new
            # one in the UI instead of failing every card poll quietly. The
            # proxy fails fast for a while, or until a key saved in the
            # options reloads the entry.
            entry.async_start_reauth(hass)
            return {"data": None, "error": "futar_auth", "detail": str(err)}
        except FutarError as err:
            return {"data": None, "error": "futar_unavailable", "detail": str(err)}
        return {"data": data}

    hass.services.async_register(
        DOMAIN,
        SERVICE_FUTAR,
        handle_futar,
        schema=FUTAR_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    entry.async_on_unload(lambda: hass.services.async_remove(DOMAIN, SERVICE_FUTAR))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    card_url = hass.data[DOMAIN].get("card_url")
    if card_url is None:
        return True
    frontend.remove_extra_js_url(hass, card_url)
    return True


CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)
