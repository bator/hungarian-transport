"""The cards' module as a Lovelace resource too.

Home Assistant serves its page while it is still starting, before custom
integrations have added their extra modules, and the frontend never loads an
extra module added later: a dashboard opened (or reloaded after an update) in
that window shows "Configuration error" on every card until the next reload.
Lovelace resources come from storage, so such a page still loads them. The
resource carries the extra module's own URL, and a browser runs a module once
per URL however it is asked for, so the cards never load twice.

Only storage-mode resources are touched; YAML mode lists resources by hand.

The storage-mode dashboards are also checked for a card that still holds its
own BKK key (`apiKey`, deprecated): any logged-in user, admin or not, can read
it with lovelace/config, and the card sends it to BKK in the request URL. A
Repairs issue names those dashboards until none is left. Lovelace keeps its
dashboards in hass.data, not behind an API: what is read there differs
between Home Assistant versions, so anything unexpected only skips the check.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Every URL the integration serves its cards from, old versions included.
URL_PREFIX = "/hungarian-transport/"
LOVELACE = "lovelace"
# Fired by Lovelace when a dashboard's config is saved or deleted.
EVENT_LOVELACE_UPDATED = "lovelace_updated"
ISSUE_CARD_API_KEY = "card_api_key"
# The cards whose apiKey the cards read (src/tags.js OWN_CARD_TYPES); an
# apiKey on anyone else's card is not ours to report.
OWN_CARD_TYPES = frozenset({
    "hungarian-transport-card",
    "bkk-stop-card-r3",
    "hungarian-transit-stop-card-plan",
    "bkk-stop-card-plan",
})
# How the default dashboard (url_path None) is named in the issue.
DEFAULT_DASHBOARD = "lovelace"


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


def has_card_api_key(config: Any) -> bool:
    """Whether a dashboard config holds one of our cards with a key of its
    own, at any depth (stacks, grids, sections, conditional cards)."""
    stack = [config]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, dict):
            kind = str(node.get("type") or "").removeprefix("custom:")
            key = node.get("apiKey")
            if kind in OWN_CARD_TYPES and isinstance(key, str) and key.strip():
                return True
            stack.extend(value for name, value in node.items() if name != "apiKey")
    return False


def _storage_dashboards(hass: HomeAssistant) -> list[Any]:
    """Lovelace's storage-mode dashboards, the default one included."""
    data = hass.data.get(LOVELACE)
    dashboards = getattr(data, "dashboards", None)
    if dashboards is None and isinstance(data, dict):
        # Before Lovelace kept its data in a dataclass.
        dashboards = data.get("dashboards")
    if not isinstance(dashboards, dict):
        return []
    return [dash for dash in dashboards.values() if getattr(dash, "mode", None) == "storage"]


async def async_dashboards_with_card_key(hass: HomeAssistant) -> list[str]:
    """The url paths of the storage-mode dashboards holding a card key."""
    found: list[str] = []
    for dash in _storage_dashboards(hass):
        try:
            config = await dash.async_load(False)
        except Exception:  # noqa: BLE001 - no config yet (ConfigNotFound), or unreadable
            continue
        if has_card_api_key(config):
            found.append(str(dash.url_path or DEFAULT_DASHBOARD))
    return sorted(found)


async def async_check_card_keys(hass: HomeAssistant) -> None:
    """Raise or end the Repairs issue about card-level keys."""
    try:
        found = await async_dashboards_with_card_key(hass)
    except Exception:  # noqa: BLE001 - Lovelace internals changed: no check
        _LOGGER.debug("Could not check the dashboards for card-level BKK keys", exc_info=True)
        return
    if not found:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_CARD_API_KEY)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_CARD_API_KEY,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_CARD_API_KEY,
        translation_placeholders={"dashboards": ", ".join(found)},
    )


class CardKeyCheck:
    """Runs async_check_card_keys at the start and after every dashboard
    save, one check at a time: a save during a check runs one more after it,
    so the last check always sees the last save."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._task: asyncio.Task[None] | None = None
        self._again = False
        self._closed = False

    @callback
    def schedule(self, *_args: Any) -> None:
        if self._closed:
            return
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = self._hass.async_create_task(self._run(), "hungarian_transport_card_keys")

    async def _run(self) -> None:
        while not self._closed:
            self._again = False
            await async_check_card_keys(self._hass)
            if not self._again:
                return

    @callback
    def close(self) -> None:
        """The entry is unloaded: no more checks."""
        self._closed = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
