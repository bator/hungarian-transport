"""Config flow for Hungarian transport."""

import unicodedata
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)

from .const import (
    CONF_API_KEY,
    CONF_DAILY_MAV,
    CONF_DAILY_TIME,
    CONF_NOTIFY_DAILY,
    CONF_NOTIFY_SERVICE,
    CONF_NOTIFY_UPDATES,
    DEFAULT_DAILY_MAV,
    DEFAULT_DAILY_TIME,
    DEFAULT_NOTIFY_DAILY,
    DEFAULT_NOTIFY_UPDATES,
    DOMAIN,
)
from .futar import FutarAuthError, FutarError, FutarUpstreamRateLimited, futar_get
from .timetable_notify import check_service, parse_daily_time

KEY_URL = "https://opendata.bkk.hu/data-sources"
# Options flow: an empty key field keeps the stored key, this removes it.
CONF_REMOVE_KEY = "remove_key"
# The service list's "none" entry: persistent notifications only, saved as "".
# Not "": the frontend's combo box ignores picking an option whose value is
# empty, so a stored service could not be unset from the list. A service is
# always domain.service, so this never names one. Only this exact value
# means none: a typed "None" is refused as any name without a domain.
NO_SERVICE = "none"
# Appended to the label of a stored service Home Assistant no longer has.
MISSING = {"en": "missing", "hu": "hiányzik"}
# Services the list leaves out: notify.* ones that need a target or are not
# one device, and the script domain's own services.
SKIP_SERVICES = {
    "notify": {"notify", "send_message", "persistent_notification"},
    "script": {"reload", "turn_on", "turn_off", "toggle"},
}


async def _validate_key(hass: HomeAssistant, api_key: str) -> str | None:
    """Return an error key, or None when FUTÁR accepts the key."""
    try:
        await futar_get(
            async_get_clientsession(hass), api_key, "search.json", {"query": "."}
        )
    except FutarAuthError:
        return "invalid_auth"
    except FutarUpstreamRateLimited:
        # HTTP 429: FUTÁR answers, but refuses this key for a while; not an
        # outage, and the key may well be good.
        return "rate_limited"
    except FutarError:
        return "cannot_connect"
    return None


def _key_schema(has_key: bool = False) -> vol.Schema:
    """The key field, masked like a password and always empty.

    The stored key is never put in the form (not even as a suggested value):
    the form data goes to the browser, and the key moved into Home Assistant
    so that browsers do not hold it. With a key stored, an empty field keeps
    it and a checkbox removes it.
    """
    fields: dict[Any, Any] = {
        vol.Optional(CONF_API_KEY): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        )
    }
    if has_key:
        fields[vol.Optional(CONF_REMOVE_KEY, default=False)] = BooleanSelector()
    return vol.Schema(fields)


def _script_name(hass: HomeAssistant, name: str) -> str | None:
    """A script service's script name. The service is the script's
    unique_id; its entity_id may have been renamed, so it is looked up in
    the entity registry (script.<service> without a registry entry)."""
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("script", "script", name) or f"script.{name}"
    if (state := hass.states.get(entity_id)) and (friendly := state.attributes.get("friendly_name")):
        return str(friendly)
    if entry := registry.async_get(entity_id):
        return entry.name or entry.original_name
    return None


def _sort_key(label: str) -> tuple[str, str]:
    """Accents aside first ("É…" with "E…", as Hungarian sorts), then as is."""
    folded = unicodedata.normalize("NFKD", label.casefold())
    return "".join(c for c in folded if not unicodedata.combining(c)), label.casefold()


def _service_options(hass: HomeAssistant, current: str) -> list[SelectOptionDict]:
    """The services the notifications can also call, for the options form:
    "none" first, then every script and notify.* service but SKIP_SERVICES,
    by label. A script shows its name ("Központi értesítés (script.ertesit)"),
    a notify service its own ("mobile_app_iphone (notify)"). current (the
    stored or submitted service) is an option too, even when Home Assistant
    does not have it (any more), so the form can show it: marked missing."""
    found: list[SelectOptionDict] = []
    for domain in ("script", "notify"):
        for name in hass.services.async_services_for_domain(domain):
            if name in SKIP_SERVICES[domain]:
                continue
            label = f"{name} ({domain})"
            if domain == "script" and (friendly := _script_name(hass, name)):
                label = f"{friendly} (script.{name})"
            found.append(SelectOptionDict(value=f"{domain}.{name}", label=label))
    if current and current != NO_SERVICE and all(o["value"] != current for o in found):
        lang = "hu" if str(hass.config.language or "").lower().startswith("hu") else "en"
        found.append(SelectOptionDict(value=current, label=f"{current} ({MISSING[lang]})"))
    found.sort(key=lambda o: (*_sort_key(o["label"]), o["value"]))
    return [SelectOptionDict(value=NO_SERVICE, label="— none —"), *found]


def _options_schema(hass: HomeAssistant, has_key: bool, values: dict[str, Any]) -> vol.Schema:
    """The key fields, the daily MÁV route index from GitHub (mav_daily.py)
    and its time, then the update notifications (timetable_notify.py): on or
    off, the service, and whether daily index installs call it.

    The service is picked from a list (_service_options) or typed. It is a
    suggested value, not a default: a default would come back when the field
    is cleared. The list is sorted here, not by the frontend, so that "none"
    stays first; its label is translated (selector.notify_service)."""
    service = values[CONF_NOTIFY_SERVICE]
    return _key_schema(has_key).extend(
        {
            vol.Optional(CONF_DAILY_MAV, default=values[CONF_DAILY_MAV]): BooleanSelector(),
            vol.Optional(CONF_DAILY_TIME, default=values[CONF_DAILY_TIME]): TimeSelector(),
            vol.Optional(CONF_NOTIFY_UPDATES, default=values[CONF_NOTIFY_UPDATES]): BooleanSelector(),
            vol.Optional(
                CONF_NOTIFY_SERVICE, description={"suggested_value": service or NO_SERVICE}
            ): SelectSelector(
                SelectSelectorConfig(
                    options=_service_options(hass, service),
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                    translation_key=CONF_NOTIFY_SERVICE,
                )
            ),
            # Whether that service hears about the daily MÁV index too.
            vol.Optional(CONF_NOTIFY_DAILY, default=values[CONF_NOTIFY_DAILY]): BooleanSelector(),
        }
    )


class HungarianTransportConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up Hungarian transport."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Change the BKK key later."""
        return HungarianTransportOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the single integration entry, optionally with a BKK key."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if api_key and (error := await _validate_key(self.hass, api_key)):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title="Hungarian transport", data={}, options={CONF_API_KEY: api_key}
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_key_schema(),
            errors=errors,
            description_placeholders={"url": KEY_URL},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """FUTÁR rejected the stored key at runtime."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a new BKK key (or none, which removes the FUTÁR service)."""
        # _get_reauth_entry() arrived in HA 2024.11; hacs.json allows 2024.10.
        entry_id = self.context["entry_id"]
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise config_entries.UnknownEntry(entry_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if api_key and (error := await _validate_key(self.hass, api_key)):
                errors["base"] = error
            else:
                # The entry's update listener reloads it once; reloading here
                # as well (async_update_reload_and_abort) would set it up twice.
                # The same key accepted again changes nothing, so no listener
                # fires: reload anyway to drop the proxy that saw it rejected.
                if not self.hass.config_entries.async_update_entry(
                    entry, options={**entry.options, CONF_API_KEY: api_key}
                ):
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_key_schema(),
            errors=errors,
            description_placeholders={"url": KEY_URL},
        )


class HungarianTransportOptionsFlow(config_entries.OptionsFlow):
    """Set, change or clear the BKK Open Data key; the daily MÁV index on or
    off and its time; update notifications."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # self.config_entry is only set automatically from HA 2024.11; the
        # handler is the entry id on every supported version.
        entry = self.hass.config_entries.async_get_entry(self.handler)
        options = dict(entry.options) if entry else {}
        current = str(options.get(CONF_API_KEY) or "")
        values: dict[str, Any] = {
            CONF_DAILY_MAV: bool(options.get(CONF_DAILY_MAV, DEFAULT_DAILY_MAV)),
            CONF_DAILY_TIME: str(options.get(CONF_DAILY_TIME) or DEFAULT_DAILY_TIME),
            CONF_NOTIFY_UPDATES: bool(options.get(CONF_NOTIFY_UPDATES, DEFAULT_NOTIFY_UPDATES)),
            CONF_NOTIFY_DAILY: bool(options.get(CONF_NOTIFY_DAILY, DEFAULT_NOTIFY_DAILY)),
            CONF_NOTIFY_SERVICE: str(options.get(CONF_NOTIFY_SERVICE) or ""),
        }
        stored_service = values[CONF_NOTIFY_SERVICE]
        errors: dict[str, str] = {}
        if user_input is not None:
            # What the form shows again on an error: what was submitted.
            # (The time selector has checked the time already.)
            # "none" from the list, or the field cleared: no service.
            picked = str(user_input.get(CONF_NOTIFY_SERVICE) or "").strip()
            if picked == NO_SERVICE:
                picked = ""
            service, error = check_service(self.hass, picked)
            if error == "service_not_found" and service == stored_service:
                # The stored service is gone (a script removed): it stays as
                # it is, so the other options can still be saved.
                error = None
            values = {
                CONF_DAILY_MAV: bool(user_input.get(CONF_DAILY_MAV, values[CONF_DAILY_MAV])),
                CONF_DAILY_TIME: parse_daily_time(user_input.get(CONF_DAILY_TIME)).strftime("%H:%M:%S"),
                CONF_NOTIFY_UPDATES: bool(user_input.get(CONF_NOTIFY_UPDATES, values[CONF_NOTIFY_UPDATES])),
                CONF_NOTIFY_DAILY: bool(user_input.get(CONF_NOTIFY_DAILY, values[CONF_NOTIFY_DAILY])),
                CONF_NOTIFY_SERVICE: service,
            }
            if error:
                errors[CONF_NOTIFY_SERVICE] = error
            typed = (user_input.get(CONF_API_KEY) or "").strip()
            if user_input.get(CONF_REMOVE_KEY):
                # A new key and "remove" at once: saving either one would
                # silently drop the other, so the form asks again.
                api_key = ""
                if typed:
                    errors["base"] = "remove_and_new_key"
            else:
                # An empty field keeps the stored key, saved again (a reload).
                api_key = typed or current
            if not errors and api_key and api_key == typed:
                if error := await _validate_key(self.hass, api_key):
                    errors["base"] = error
            if not errors:
                # Every other option stays as it was.
                new = {**options, CONF_API_KEY: api_key, **values}
                if entry is not None and new == options:
                    # Saving the same options fires no update listener, so
                    # the entry is not reloaded: reload it here, or a proxy
                    # that saw the key rejected would keep failing.
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_create_entry(data=new)
        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(self.hass, bool(current), values),
            errors=errors,
            description_placeholders={"url": KEY_URL},
        )
