"""Home Assistant integration for Hungarian transport services and card assets."""

from collections.abc import Awaitable, Callable
import logging
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import CoreState, HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util

from . import lovelace_resource, mav, mav_daily, realtime, timetable_notify, timetables
from .assets import asset_tag
from .cache import close_on_stop, hass_call_later
from .const import (
    CONF_API_KEY,
    CONF_DAILY_MAV,
    DEFAULT_DAILY_MAV,
    DOMAIN,
    SERVICE_ELVIRA_BETWEEN,
    SERVICE_FUTAR,
    SERVICE_REALTIME,
    SERVICE_REBUILD_MAV,
)
from .elvira import ElviraError, ElviraRateLimited, elvira_between, station_code_digits
from .futar import (
    FUTAR_PATHS,
    FutarAuthError,
    FutarBadRequest,
    FutarError,
    FutarProxy,
    FutarRateLimited,
)
from .responses import (
    ELVIRA_RATE_LIMITED,
    ELVIRA_UNAVAILABLE,
    FUTAR_AUTH,
    FUTAR_BAD_REQUEST,
    FUTAR_RATE_LIMITED,
    FUTAR_UNAVAILABLE,
    RT_UNAVAILABLE,
    error_answer,
)

_LOGGER = logging.getLogger(__name__)

# How far a planner's timed search may start from now: its day picker offers
# today and the next six days.
ELVIRA_AT_BACK = 86400
ELVIRA_AT_AHEAD = 8 * 86400


def _elvira_at(value: Any) -> int:
    """The window's start in epoch seconds, near enough to now to be a search."""
    message = "at must be within a day before and eight days after now"
    try:
        at = vol.Coerce(int)(value)
    except OverflowError:
        # Coerce passes it on: int() of a YAML .inf.
        raise vol.Invalid(message) from None
    now = dt_util.utcnow().timestamp()
    if not now - ELVIRA_AT_BACK <= at <= now + ELVIRA_AT_AHEAD:
        raise vol.Invalid(message)
    return at


ELVIRA_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("origin_code"): cv.string,
        vol.Required("dest_code"): cv.string,
        vol.Optional(
            "minutes_after", default=180
        ): vol.All(cv.positive_int, vol.Range(min=15, max=480)),
        # Copied into every row: a station name, not a payload.
        vol.Optional("dest_name", default=""): vol.All(cv.string, vol.Length(max=100)),
        vol.Optional("at"): _elvira_at,
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
# What the cards send for each departure row (src/lib/realtime.js); anything
# longer or larger is refused before a feed is touched.
REALTIME_TRIP_SCHEMA = vol.Schema(
    {
        vol.Required("key"): vol.All(cv.string, vol.Length(min=1, max=96)),
        vol.Optional("trip_id"): vol.All(cv.string, vol.Length(min=1, max=128)),
        vol.Optional("route"): vol.All(cv.string, vol.Length(min=1, max=32)),
        vol.Optional("start"): vol.All(vol.Coerce(int), vol.Range(min=0, max=2880)),
        vol.Optional("date"): vol.All(cv.string, vol.Match(r"^\d{8}$")),
        vol.Optional("train"): vol.All(cv.string, vol.Match(r"^\d{1,6}$")),
        vol.Optional("stops", default=list): vol.All(
            cv.ensure_list,
            vol.Length(max=realtime.MAX_STOPS),
            [vol.All(cv.string, vol.Length(min=1, max=64))],
        ),
        # Indices of the stops the trip calls at more than once (loops).
        vol.Optional("repeat"): vol.All(
            cv.ensure_list,
            vol.Length(max=realtime.MAX_STOPS),
            [vol.All(vol.Coerce(int), vol.Range(min=0, max=realtime.MAX_STOPS - 1))],
        ),
        vol.Optional("from"): vol.All(vol.Coerce(int), vol.Range(min=0, max=realtime.MAX_STOPS - 1)),
        vol.Optional("to"): vol.All(vol.Coerce(int), vol.Range(min=0, max=realtime.MAX_STOPS - 1)),
    }
)
REALTIME_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("feed"): vol.All(cv.string, vol.Match(realtime.FEED_ID_RE)),
        vol.Required("trips"): vol.All(
            cv.ensure_list, vol.Length(max=realtime.MAX_TRIPS), [REALTIME_TRIP_SCHEMA]
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
# The detail of a card's answer while the entry is reloading or starting.
NOT_READY = "Hungarian transport is starting or reloading"
PLATFORMS = [Platform.SENSOR]


def _entry_loaded(hass: HomeAssistant) -> bool:
    return any(
        entry.state is ConfigEntryState.LOADED
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


def _entry_coming_up(hass: HomeAssistant) -> bool:
    """An enabled entry on its way to LOADED: a reload (an options save, a
    reauth) holds its setup lock from the unload to the end of the setup,
    and at startup the services exist before the entry is set up."""
    return not _entry_loaded(hass) and any(
        entry.disabled_by is None
        and (
            entry.setup_lock.locked()
            or (entry.state is ConfigEntryState.NOT_LOADED and hass.state is not CoreState.running)
        )
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


def _require_entry(hass: HomeAssistant) -> None:
    """The services and the upload stay registered after the entry is
    unloaded (disabled); they refuse until it is loaded again."""
    if not _entry_loaded(hass):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")


async def _require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """What async_register_admin_service checks: a call without a user (an
    automation, a script) is allowed, a user must be an admin."""
    if not call.context.user_id:
        return
    user = await hass.auth.async_get_user(call.context.user_id)
    if user is None:
        raise UnknownUser(context=call.context)
    if not user.is_admin:
        raise Unauthorized(context=call.context)


CardHandler = Callable[[ServiceCall], Awaitable[dict[str, Any]]]


def _card_service(
    hass: HomeAssistant,
    empty: Callable[[ServiceCall], dict[str, Any]],
    errors: tuple[tuple[type[Exception], str], ...] = (),
    *,
    starting: str | None = None,
    starting_detail: str | None = None,
) -> Callable[[CardHandler], CardHandler]:
    """What every card service does around its handler (responses.py).

    `starting`: the code a card gets while the entry is starting or
    reloading (every open card polls through it: an answer, not an ERROR
    line per poll); such a service refuses while the entry is unloaded.
    None for a service that exists only while the entry is loaded.
    `errors`: an exception of the handler's, answered as its code (the
    first class that matches). `empty` is the answer's data part then."""

    def wrap(handler: CardHandler) -> CardHandler:
        async def handle(call: ServiceCall) -> dict[str, Any]:
            if starting is not None:
                if _entry_coming_up(hass):
                    return error_answer(empty(call), starting, starting_detail)
                _require_entry(hass)
            try:
                return await handler(call)
            except tuple(exc for exc, _code in errors) as err:
                code = next(code for exc, code in errors if isinstance(err, exc))
                return error_answer(empty(call), code, str(err))

        return handle

    return wrap


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

    # An outage is an answer (elvira.py logs it once), and so is the
    # integration's own request limit, which is not MÁV being down.
    @_card_service(
        hass,
        lambda _call: {"departures": []},
        ((ElviraRateLimited, ELVIRA_RATE_LIMITED), (ElviraError, ELVIRA_UNAVAILABLE)),
        starting=ELVIRA_UNAVAILABLE,
        starting_detail=NOT_READY,
    )
    async def handle_elvira_between(call: ServiceCall) -> dict[str, Any]:
        origin = _station_code(call, "origin_code")
        dest = _station_code(call, "dest_code")
        if origin == dest:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="same_station",
            )
        rows, dest_failure = await elvira_between(
            session,
            hass,
            origin,
            dest,
            call.data["minutes_after"],
            call.data["dest_name"],
            call.data.get("at"),
        )
        if dest_failure is None:
            return {"departures": rows}
        # The rows still come, but without the destination's arrivals their
        # destHit is not to be trusted.
        code = ELVIRA_RATE_LIMITED if isinstance(dest_failure, ElviraRateLimited) else ELVIRA_UNAVAILABLE
        return {"departures": rows, "dest_error": code}

    hass.services.async_register(
        DOMAIN,
        SERVICE_ELVIRA_BETWEEN,
        handle_elvira_between,
        schema=ELVIRA_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # GTFS-Realtime delays and positions (realtime.py): fetched only while a
    # card asks, shared by every card. Reading needs no admin, like the
    # timetables the cards read anyway.
    hub = realtime.RealtimeHub(
        session,
        executor=hass.async_add_executor_job,
        task_factory=lambda coro: hass.async_create_background_task(
            coro, "hungarian_transport_realtime", eager_start=False
        ),
    )

    # Feed outages are part of the hub's answer (rt_unavailable, rt_no_feed).
    @_card_service(hass, lambda call: {"feed": call.data["feed"], "trips": {}}, starting=RT_UNAVAILABLE)
    async def handle_realtime(call: ServiceCall) -> dict[str, Any]:
        return await hub.query(call.data["feed"], call.data["trips"])

    hass.services.async_register(
        DOMAIN,
        SERVICE_REALTIME,
        handle_realtime,
        schema=REALTIME_SERVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    # MÁV GTFS is registration-only, so the rail index is built here from the
    # feed the user supplies (see mav.py), and checked trip by trip.
    await mav.async_remove_leftovers(hass, www_path)
    hass.http.register_view(mav.MavGtfsUploadView(www_path, _entry_loaded))

    async def handle_rebuild_mav(call: ServiceCall) -> dict[str, Any]:
        await _require_admin(hass, call)
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
    # host): admins only, like the upload. Not async_register_admin_service:
    # it takes supports_response from Home Assistant 2025.6 only.
    hass.services.async_register(
        DOMAIN,
        SERVICE_REBUILD_MAV,
        handle_rebuild_mav,
        schema=vol.Schema({}),
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
    # The release version stays readable in the URL; the content hash makes a
    # rebuilt www/ (a source install keeps the last release's version) a new URL.
    tag = await hass.async_add_executor_job(asset_tag, www_path)
    card_url = f"{entry_url}?v={integration.version}" + (f"-{tag}" if tag else "")
    hass.data.setdefault(DOMAIN, {})["card_url"] = card_url
    frontend.add_extra_js_url(hass, card_url)

    # A page served before this point has no extra module at all: the same
    # URL as a Lovelace resource reaches it (lovelace_resource.py). Lovelace
    # is surely up once Home Assistant has started.
    async def _sync_resource(_hass: HomeAssistant) -> None:
        try:
            await lovelace_resource.async_sync_resource(hass, card_url)
        except Exception:  # noqa: BLE001 - the extra module still loads the cards
            _LOGGER.warning("Could not keep the cards as a Lovelace resource", exc_info=True)

    async_at_started(hass, _sync_resource)
    return True


def _entry_api_key(entry: ConfigEntry) -> str:
    # The key has only ever been stored in the options.
    return str(entry.options.get(CONF_API_KEY) or "").strip()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration entry."""
    frontend.add_extra_js_url(hass, hass.data[DOMAIN]["card_url"])
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    notifier = timetable_notify.UpdateNotifier(hass, entry.options)
    entry.async_on_unload(notifier.stop)
    # The shipped indexes' feeds are recorded at every setup; a change that
    # came with an update of the integration notifies (when turned on). Its
    # service call waits until Home Assistant has started: scripts and
    # notify.mobile_app_* register theirs later in the startup.
    try:
        integration = await async_get_integration(hass, DOMAIN)
        await notifier.async_check_shipped(WWW_PATH, str(integration.version))
        await notifier.async_restore()
    except Exception:  # noqa: BLE001 - never a reason not to set up
        _LOGGER.warning("Could not check the shipped timetables for changes", exc_info=True)
    updater: mav_daily.DailyIndexUpdater | None = None
    daily = bool(entry.options.get(CONF_DAILY_MAV, DEFAULT_DAILY_MAV))
    if daily:
        # The daily MÁV index from GitHub Pages (mav_daily.py), at the time
        # the options set; turned off in the options, no request is made.
        updater = mav_daily.DailyIndexUpdater(
            hass, WWW_PATH, notifier.daily_time, notifier.async_daily_result
        )
        entry.async_on_unload(updater.start())
    # The timetable sensors (sensor.py): read once now, then on every change
    # the integration makes and every 30 minutes (timetables.py).
    status = timetables.TimetableStatus(
        hass, WWW_PATH, daily_enabled=daily, next_run=(lambda: updater.next_run) if updater else None
    )
    await status.async_refresh()
    entry.async_on_unload(status.start())
    entry.runtime_data = status
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

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
        call_later=hass_call_later(hass),
    )
    entry.async_on_unload(proxy.close)
    entry.async_on_unload(close_on_stop(hass, proxy.close))

    # The proxy logs an outage or a rejected key once. A rate limit is the
    # integration's own or FUTÁR's for the key (HTTP 429), and a bad request
    # was never sent: neither is BKK being down.
    @_card_service(
        hass,
        lambda _call: {"data": None},
        (
            (FutarAuthError, FUTAR_AUTH),
            (FutarRateLimited, FUTAR_RATE_LIMITED),
            (FutarBadRequest, FUTAR_BAD_REQUEST),
            (FutarError, FUTAR_UNAVAILABLE),
        ),
    )
    async def handle_futar(call: ServiceCall) -> dict[str, Any]:
        try:
            data = await proxy.get(call.data["path"], call.data["params"])
        except FutarAuthError:
            # The stored key stopped working (revoked, expired): ask for a new
            # one in the UI instead of failing every card poll quietly. The
            # proxy fails fast for a while, or until a key saved in the
            # options reloads the entry.
            entry.async_start_reauth(hass)
            raise
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
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    card_url = hass.data[DOMAIN].get("card_url")
    if card_url is None:
        return True
    frontend.remove_extra_js_url(hass, card_url)
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The integration is removed: so is its Lovelace resource."""
    try:
        await lovelace_resource.async_remove_resource(hass)
    except Exception:  # noqa: BLE001 - removal goes on
        _LOGGER.warning("Could not remove the cards' Lovelace resource", exc_info=True)


CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)
