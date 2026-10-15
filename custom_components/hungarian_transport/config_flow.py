"""Config flow for Hungarian transport."""

from typing import Any

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN


class HungarianTransportConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up Hungarian transport."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create the single integration entry."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Hungarian transport", data={})
