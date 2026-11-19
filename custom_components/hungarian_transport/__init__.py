"""Home Assistant integration for Hungarian transport services and card assets."""

from collections.abc import Awaitable, Callable
from functools import partial
import logging
from pathlib import Path
import time
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, persistent_notification
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import CoreState, HomeAssistant, ServiceCall, SupportsResponse, callback
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

from . import (
    lovelace_resource,
    mav,
    mav_daily,
    mavplusz,
    mavplusz_board,
    realtime,
    storage,
    timetable_notify,
    timetables,
    weekly_timetables,
)
from .assets import asset_tag
from .cache import close_on_stop, hass_call_later
from .config_flow import async_end_reauth
from .const import (
    CONF_API_KEY,
    CONF_DAILY_MAV,
    CONF_PAGES_TIMETABLES,
    DEFAULT_DAILY_MAV,
    DEFAULT_PAGES_TIMETABLES,
    DOMAIN,
    SERVICE_ELVIRA_BETWEEN,
    SERVICE_FUTAR,
    SERVICE_REALTIME,
    SERVICE_REBUILD_MAV,
)
from .elvira import (
    ElviraBoards,
    ElviraError,
    ElviraRateLimited,
    fetch_station,
    station_code_digits,
)
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


def _finite_int(value: Any) -> int:
    """vol.Coerce(int), with a YAML .inf invalid too: Coerce passes on the
    OverflowError of int(inf)."""
    try:
        return vol.Coerce(int)(value)
    except OverflowError:
        raise vol.Invalid("expected a finite integer") from None


def _elvira_at(value: Any) -> int:
    """The window's start in epoch seconds, near enough to now to be a search."""
    message = "at must be within a day before and eight days after now"
    try:
        at = _finite_int(value)
    except vol.Invalid:
        raise vol.Invalid(message) from None
    now = dt_util.utcnow().timestamp()
    if not now - ELVIRA_AT_BACK <= at <= now + ELVIRA_AT_AHEAD:
        raise vol.Invalid(message)
    return at


# The longest dest_name elvira_between takes; the card cuts it to the same
# length (src/lib/board.js ELVIRA_DEST_NAME_MAX, kept equal by a test).
ELVIRA_DEST_NAME_MAX = 100
ELVIRA_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("origin_code"): cv.string,
        vol.Required("dest_code"): cv.string,
        vol.Optional(
            "minutes_after", default=180
        ): vol.All(_finite_int, vol.Range(min=15, max=480)),
        # Copied into every row: a station name, not a payload.
        vol.Optional("dest_name", default=""): vol.All(cv.string, vol.Length(max=ELVIRA_DEST_NAME_MAX)),
        vol.Optional("at"): _elvira_at,
    }
)


def _futar_value(value: Any) -> str:
    """A FUTÁR query value as a string; a YAML true is "true" as the cards
    send it, not Python's "True"."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return cv.string(value)


FUTAR_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required("path"): vol.In(FUTAR_PATHS),
        vol.Optional("params", default=dict): vol.Schema({cv.string: _futar_value}),
    }
)
# What the cards send for each departure row (src/lib/realtime.js rtRequest,
# its RT_KEY_MAX, RT_TRIP_ID_MAX, RT_ROUTE_MAX, RT_START_MAX and
# RT_STOP_ID_MAX); anything longer or larger is refused before a feed is
# touched. The start is the trip's first time in minutes from its service
# day's midnight: GTFS times run past 24:00, so up to two days.
REALTIME_KEY_MAX = 96
REALTIME_TRIP_ID_MAX = 128
REALTIME_ROUTE_MAX = 32
REALTIME_START_MAX = 2880
REALTIME_STOP_ID_MAX = 64


REALTIME_TRIP_SCHEMA = vol.Schema(
    {
        vol.Required("key"): vol.All(cv.string, vol.Length(min=1, max=REALTIME_KEY_MAX)),
        vol.Optional("trip_id"): vol.All(cv.string, vol.Length(min=1, max=REALTIME_TRIP_ID_MAX)),
        vol.Optional("route"): vol.All(cv.string, vol.Length(min=1, max=REALTIME_ROUTE_MAX)),
        vol.Optional("start"): vol.All(_finite_int, vol.Range(min=0, max=REALTIME_START_MAX)),
        # Eight digits; one that is no calendar day (20261131) is that row's
        # business alone: the hub never asks the planner for it.
        vol.Optional("date"): vol.All(cv.string, vol.Match(r"^\d{8}$")),
        vol.Optional("train"): vol.All(cv.string, vol.Match(r"^\d{1,6}$")),
        vol.Optional("stops", default=list): vol.All(
            cv.ensure_list,
            vol.Length(max=realtime.MAX_STOPS),
            [vol.All(cv.string, vol.Length(min=1, max=REALTIME_STOP_ID_MAX))],
        ),
        # Indices of the stops the trip calls at more than once (loops).
        vol.Optional("repeat"): vol.All(
            cv.ensure_list,
            vol.Length(max=realtime.MAX_STOPS),
            [vol.All(_finite_int, vol.Range(min=0, max=realtime.MAX_STOPS - 1))],
        ),
        vol.Optional("from"): vol.All(_finite_int, vol.Range(min=0, max=realtime.MAX_STOPS - 1)),
        vol.Optional("to"): vol.All(_finite_int, vol.Range(min=0, max=realtime.MAX_STOPS - 1)),
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
    # ELVIRA's boards (elvira.py), shared by every card for Home Assistant's
    # lifetime: the services stay registered while the entry is unloaded.
    # Boards are decoded, and their rows settled, in HA's executor: a busy
    # station's day is megabytes of JSON.
    boards = ElviraBoards(
        partial(fetch_station, session, executor=hass.async_add_executor_job),
        task_factory=lambda coro: hass.async_create_background_task(
            coro, "hungarian_transport_elvira", eager_start=False
        ),
        call_later=hass_call_later(hass),
        now=dt_util.now,
        monotonic=time.monotonic,
        executor=hass.async_add_executor_job,
    )
    close_on_stop(hass, boards.close)

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
        rows, dest_failure = await boards.between(
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
    # call_later: decoded feeds no card asks for leave memory on a timer,
    # also when no card asks at all; the timer stops with HA.
    hub = realtime.RealtimeHub(
        session,
        planner_limiter=_planner_limiter(hass),
        executor=hass.async_add_executor_job,
        task_factory=lambda coro: hass.async_create_background_task(
            coro, "hungarian_transport_realtime", eager_start=False
        ),
        call_later=hass_call_later(hass),
    )
    close_on_stop(hass, hub.close)

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


def _planner_limiter(hass: HomeAssistant) -> mavplusz.PlannerLimiter:
    """Home Assistant's one rate limit and backoff for mavplusz.hu, shared by
    the realtime hub and every entry's BKK boards: one server, one limit."""
    data = hass.data.setdefault(DOMAIN, {})
    limiter = data.get("planner_limiter")
    if limiter is None:
        limiter = data["planner_limiter"] = mavplusz.PlannerLimiter()
    return limiter


def _entry_api_key(entry: ConfigEntry) -> str:
    # The key has only ever been stored in the options.
    return str(entry.options.get(CONF_API_KEY) or "").strip()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration entry."""
    frontend.add_extra_js_url(hass, hass.data[DOMAIN]["card_url"])
    if hass.is_running:
        # Added after the start (again, after a removal took the resource
        # away): async_setup's sync at the start has run already.
        try:
            await lovelace_resource.async_sync_resource(hass, hass.data[DOMAIN]["card_url"])
        except Exception:  # noqa: BLE001 - the extra module still loads the cards
            _LOGGER.warning("Could not keep the cards as a Lovelace resource", exc_info=True)
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
    # Retired shapes folders go also with the weekly timetables off (a
    # Pages install, or the local updater, left them for the open dashboards).
    try:
        await hass.async_add_executor_job(weekly_timetables.prune_all, WWW_PATH)
    except Exception:  # noqa: BLE001 - never a reason not to set up
        _LOGGER.warning("Could not prune the old shapes folders", exc_info=True)
    updater: mav_daily.DailyIndexUpdater | None = None
    daily = bool(entry.options.get(CONF_DAILY_MAV, DEFAULT_DAILY_MAV))
    pages = bool(entry.options.get(CONF_PAGES_TIMETABLES, DEFAULT_PAGES_TIMETABLES))
    if daily or pages:
        # The daily MÁV index from GitHub Pages (mav_daily.py), at the time
        # the options set, and after it in the same rounds the weekly Volán
        # and city indexes (weekly_timetables.py); each turned off in the
        # options makes no request.
        weekly = (
            weekly_timetables.PagesTimetables(hass, WWW_PATH, notifier.async_pages_result) if pages else None
        )
        updater = mav_daily.DailyIndexUpdater(
            hass,
            WWW_PATH,
            notifier.daily_time,
            notifier.async_daily_result,
            mav=daily,
            timetables=weekly,
        )
        entry.async_on_unload(updater.start())
    # The timetable sensors (sensor.py): read once now, then on every change
    # the integration makes and every 30 minutes (timetables.py).
    status = timetables.TimetableStatus(
        hass,
        WWW_PATH,
        daily_enabled=daily,
        pages_enabled=pages,
        next_run=(lambda: updater.next_run) if updater else None,
    )
    await status.async_refresh()
    entry.async_on_unload(status.start())
    entry.runtime_data = status
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # A card with its own (deprecated) key is a Repairs issue: checked once
    # Lovelace is up, and again whenever a dashboard is saved.
    card_keys = lovelace_resource.CardKeyCheck(hass)
    entry.async_on_unload(card_keys.close)
    entry.async_on_unload(
        hass.bus.async_listen(lovelace_resource.EVENT_LOVELACE_UPDATED, card_keys.schedule)
    )
    entry.async_on_unload(async_at_started(hass, card_keys.schedule))

    api_key = _entry_api_key(entry)
    if not api_key:
        return True

    @callback
    def _key_accepted() -> None:
        # The key works: a reauth a rejection started goes, whether this
        # proxy saw the rejection (a mistake, an upstream hiccup) or the one
        # a reload replaced did. Home Assistant 2024.10 keeps a reauth open
        # across a reload. Left open, answering it empty removes the key.
        async_end_reauth(hass, entry.entry_id)

    # The card switches to this service when it exists, so it is registered
    # only while a key is configured. Without one the cards read BKK from
    # Transitous; the planner below stands in for FUTÁR's departure boards
    # and trip details only (docs/realtime.md "BKK boards when FUTÁR fails"),
    # not for the searches, routes and plans a card with the service asks for.
    def task_factory(coro: Any) -> Any:
        return hass.async_create_background_task(coro, "hungarian_transport_futar", eager_start=False)

    # The poles of FUTÁR's combined stations learnt before (storage.py), so
    # their boards have a fallback right after a restart too.
    learnt = False

    @callback
    def _stations_changed() -> None:
        nonlocal learnt
        learnt = True
        storage.async_save_stations_later(hass, planner.stations_data)

    planner = mavplusz_board.PlannerBoards(
        async_get_clientsession(hass),
        task_factory=task_factory,
        call_later=hass_call_later(hass),
        limiter=_planner_limiter(hass),
        stations=await storage.async_load_stations(hass),
        on_stations_changed=_stations_changed,
    )

    async def _save_planner() -> None:
        # Unloaded (a reload, the entry removed): what the planner learnt is
        # written now. The delayed write left pending would be replaced by
        # the next planner's (a reload: up to STATIONS_SAVE_DELAY of poles
        # lost), or write the store again after the entry's removal deleted
        # it. Registered first, so it runs after proxy.close, which closes
        # the planner (its one owner): it learns nothing more by then (a
        # FUTÁR request still under way lands later).
        if learnt:
            await storage.async_save_stations(hass, planner.stations_data())

    entry.async_on_unload(_save_planner)
    proxy = FutarProxy(
        async_get_clientsession(hass),
        api_key,
        task_factory=task_factory,
        call_later=hass_call_later(hass),
        on_accepted=_key_accepted,
        # A parent station's schedule-for-stop.json runs to megabytes: it
        # is decoded in HA's executor, like ELVIRA's boards.
        executor=hass.async_add_executor_job,
        # FUTÁR down, refusing the key or the call (HTTP 429): the boards
        # and trip details come from mavplusz.hu; the proxy closes it.
        fallback=planner,
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
            # options reloads the entry; a key FUTÁR accepts again later
            # ends the reauth (_key_accepted).
            entry.async_start_reauth(hass)
            raise
        if proxy.key_rejected:
            # The planner answered for a rejected key: a new one is still due.
            entry.async_start_reauth(hass)
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
    """The integration is removed: so are its Lovelace resource, its
    notifications, its Repairs issues, its record in .storage (storage.py)
    and the copy of it in memory, and the BKK stations learnt from FUTÁR. There is one entry at most
    (its unique_id is the domain), so nothing is shared with another.

    Only what installed the indexes stays (storage.MAV_INSTALL,
    storage.TIMETABLES_INSTALL): the indexes stay in www/ and the cards keep
    drawing from them, so an entry added again would otherwise call them a
    release's. The sensors match a record to the served file (feed and
    size), so a record of an index replaced since says nothing."""
    try:
        await lovelace_resource.async_remove_resource(hass)
    except Exception:  # noqa: BLE001 - removal goes on
        _LOGGER.warning("Could not remove the cards' Lovelace resource", exc_info=True)
    # A notice about timetables or a failed MÁV build would outlive it.
    for notification_id in (
        timetable_notify.NOTIFICATION_SHIPPED,
        timetable_notify.NOTIFICATION_MAV_DAILY,
        timetable_notify.NOTIFICATION_DAILY_FAILED,
        timetable_notify.NOTIFICATION_PAGES,
        mav.NOTIFICATION_ID,
    ):
        persistent_notification.async_dismiss(hass, notification_id)
    # So would its Repairs issue: nothing builds the index any more to end it.
    ir.async_delete_issue(hass, DOMAIN, mav.ISSUE_BUILD_FAILED)
    ir.async_delete_issue(hass, DOMAIN, lovelace_resource.ISSUE_CARD_API_KEY)
    try:
        state = await storage.async_load(hass)
        kept = {
            key: state[key]
            for key in (storage.MAV_INSTALL, storage.TIMETABLES_INSTALL)
            if isinstance(state.get(key), dict)
        }
        if kept:
            await storage.async_save(hass, kept)
        else:
            await storage.store(hass).async_remove()
    except Exception:  # noqa: BLE001 - removal goes on
        _LOGGER.warning("Could not remove the timetable update record", exc_info=True)
    hass.data.get(DOMAIN, {}).pop("update_state", None)
    # The BKK stations learnt from FUTÁR went with the entry's key.
    try:
        await storage.stations_store(hass).async_remove()
    except Exception:  # noqa: BLE001 - removal goes on
        _LOGGER.warning("Could not remove the learnt BKK stations", exc_info=True)


CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)
