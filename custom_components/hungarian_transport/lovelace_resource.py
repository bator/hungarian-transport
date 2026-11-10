"""The cards' module as a Lovelace resource too.

Home Assistant serves its page while it is still starting, before custom
integrations have added their extra modules, and the frontend never loads an
extra module added later: a dashboard opened (or reloaded after an update) in
that window shows "Configuration error" on every card until the next reload.
Lovelace resources come from storage, so such a page still loads them. The
resource carries the extra module's own URL, and a browser runs a module once
per URL however it is asked for, so the cards never load twice.

Only storage-mode resources are touched; YAML mode lists resources by hand.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Every URL the integration serves its cards from, old versions included.
URL_PREFIX = "/hungarian-transport/"
LOVELACE = "lovelace"


def _storage_resources(hass: HomeAssistant) -> Any | None:
    """The storage-mode resource collection, None in YAML mode or without Lovelace."""
    try:
        from homeassistant.components.lovelace.resources import ResourceStorageCollection
    except ImportError:
        return None
    data = hass.data.get(LOVELACE)
    resources = getattr(data, "resources", None)
    if resources is None and isinstance(data, dict):
        # Before Lovelace kept its data in a dataclass.
        resources = data.get("resources")
    return resources if isinstance(resources, ResourceStorageCollection) else None


async def _loaded(resources: Any) -> Any:
    # Lovelace reads its resource store on first use only.
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    return resources


def _ours(resources: Any) -> list[dict[str, Any]]:
    return [
        item for item in resources.async_items()
        if str(item.get("url") or "").startswith(URL_PREFIX)
    ]


async def async_sync_resource(hass: HomeAssistant, url: str) -> None:
    """Keep exactly one resource of ours, a module at url."""
    resources = _storage_resources(hass)
    if resources is None:
        _LOGGER.debug("Lovelace resources are not in storage mode; the cards load as an extra module only")
        return
    ours = _ours(await _loaded(resources))
    if len(ours) == 1 and ours[0].get("url") == url and ours[0].get("type") == "module":
        return
    for item in ours[1:]:
        await resources.async_delete_item(item["id"])
    if ours:
        await resources.async_update_item(ours[0]["id"], {"res_type": "module", "url": url})
    else:
        await resources.async_create_item({"res_type": "module", "url": url})
    _LOGGER.info("Lovelace resource for the cards set to %s", url)


async def async_remove_resource(hass: HomeAssistant) -> None:
    """Remove our resources (the integration was removed)."""
    resources = _storage_resources(hass)
    if resources is None:
        return
    for item in _ours(await _loaded(resources)):
        await resources.async_delete_item(item["id"])
