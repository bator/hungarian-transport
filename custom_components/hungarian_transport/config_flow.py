"""Config flow for Hungarian transport."""

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_API_KEY, DOMAIN
from .futar import FutarAuthError, FutarError, futar_get

KEY_URL = "https://opendata.bkk.hu/data-sources"


async def _validate_key(hass: HomeAssistant, api_key: str) -> str | None:
    """Return an error key, or None when FUTÁR accepts the key."""
    try:
        await futar_get(
            async_get_clientsession(hass), api_key, "search.json", {"query": "."}
        )
    except FutarAuthError:
        return "invalid_auth"
    except FutarError:
        return "cannot_connect"
    return None


def _key_schema(default: str = "") -> vol.Schema:
    return vol.Schema({vol.Optional(CONF_API_KEY, default=default): str})


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
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if api_key and (error := await _validate_key(self.hass, api_key)):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, options={CONF_API_KEY: api_key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_key_schema(),
            errors=errors,
            description_placeholders={"url": KEY_URL},
        )


class HungarianTransportOptionsFlow(config_entries.OptionsFlow):
    """Set, change or clear the BKK Open Data key."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        # self.config_entry is only set automatically from HA 2024.11; the
        # handler is the entry id on every supported version.
        entry = self.hass.config_entries.async_get_entry(self.handler)
        current = str((entry.options if entry else {}).get(CONF_API_KEY) or "")
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = (user_input.get(CONF_API_KEY) or "").strip()
            if api_key and (error := await _validate_key(self.hass, api_key)):
                errors["base"] = error
            else:
                return self.async_create_entry(data={CONF_API_KEY: api_key})
        return self.async_show_form(
            step_id="init",
            data_schema=_key_schema(current),
            errors=errors,
            description_placeholders={"url": KEY_URL},
        )
