import logging
from typing import List

from aioafero import EventType
from aioafero.v1 import AferoBridgeV1, LightController
from aioafero.v1.models import Light
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
    filter_supported_color_modes,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .bridge import HubspaceBridge
from .const import DOMAIN
from .entity import HubspaceBaseEntity, update_decorator
from .dual_mode_lights import HubspaceColorLight, HubspaceWhiteLight, should_create_dual_lights
from .string_lights import HubspaceStringLightBulb, should_create_string_light_bulbs, get_string_light_bulb_count

LOGGER = logging.getLogger(__name__)


def _get_device_name(resource: Light) -> str:
    """Get the device name from the resource, with fallback options."""
    result_name = resource.id  # Default fallback
    
    if hasattr(resource, 'device_information') and resource.device_information:
        if resource.device_information.name:
            result_name = resource.device_information.name
            LOGGER.debug(f"Using device_information.name: {result_name}")
        elif resource.device_information.default_name:
            result_name = resource.device_information.default_name
            LOGGER.debug(f"Using device_information.default_name: {result_name}")
        else:
            LOGGER.debug(f"No device_information names available, using ID: {result_name}")
    else:
        LOGGER.debug(f"No device_information available, using ID: {result_name}")
    
    return result_name


class HubspaceLight(HubspaceBaseEntity, LightEntity):
    def __init__(
        self,
        bridge: HubspaceBridge,
        controller: LightController,
        resource: Light,
    ) -> None:
        super().__init__(bridge, controller, resource)
        self._supported_features: LightEntityFeature = LightEntityFeature(0)
        supported_color_modes = {ColorMode.ONOFF}
        if self.resource.supports_color:
            supported_color_modes.add(ColorMode.RGB)
        if self.resource.supports_color_temperature:
            supported_color_modes.add(ColorMode.COLOR_TEMP)
        if self.resource.supports_dimming:
            supported_color_modes.add(ColorMode.BRIGHTNESS)
        self._attr_supported_color_modes = filter_supported_color_modes(
            supported_color_modes
        )

    @property
    def brightness(self) -> int | None:
        return (
            value_to_brightness((1, 100), self.resource.brightness)
            if self.resource.dimming
            else None
        )

    @property
    def color_mode(self) -> ColorMode:
        return get_color_mode(self.resource, self._attr_supported_color_modes)

    @property
    def color_temp_kelvin(self) -> int | None:
        return (
            self.resource.color_temperature.temperature
            if self.resource.color_temperature
            else None
        )

    @property
    def effect(self) -> str | None:
        return (
            self.resource.effect.effect
            if (self.resource.effect and self.resource.color_mode.mode == "sequence")
            else None
        )

    @property
    def effect_list(self) -> list[str] | None:
        all_effects = []
        for effects in self.resource.effect.effects.values() or []:
            all_effects.extend(effects)
        return all_effects or None

    @property
    def is_on(self) -> bool | None:
        return self.resource.is_on

    @property
    def max_color_temp_kelvin(self) -> int | None:
        return (
            max(self.resource.color_temperature.supported)
            if self.resource.color_temperature
            else None
        )

    @property
    def min_color_temp_kelvin(self) -> int | None:
        return (
            min(self.resource.color_temperature.supported)
            if self.resource.color_temperature
            else None
        )

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        return (
            (
                self.resource.color.red,
                self.resource.color.green,
                self.resource.color.blue,
            )
            if self.resource.color
            else None
        )

    @property
    def supported_color_modes(self) -> set[ColorMode]:
        return self._attr_supported_color_modes

    @property
    def supported_features(self) -> LightEntityFeature:
        if self.resource.effect:
            return LightEntityFeature(0) | LightEntityFeature.EFFECT
        else:
            return LightEntityFeature(0)

    @update_decorator
    async def async_turn_on(self, **kwargs) -> None:
        brightness: int | None = None
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(brightness_to_value((1, 100), kwargs[ATTR_BRIGHTNESS]))
        temperature: int | None = kwargs.get(ATTR_COLOR_TEMP_KELVIN, None)
        color: tuple[int, int, int] | None = kwargs.get(ATTR_RGB_COLOR, None)
        effect: str | None = kwargs.get(ATTR_EFFECT, None)
        color_mode: str | None = None
        if temperature:
            color_mode = "mixed"
        elif color:
            color_mode = "mixed"
        elif effect:
            color_mode = "sequence"
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            on=True,
            brightness=brightness,
            temperature=temperature,
            color=color,
            color_mode=color_mode,
            effect=effect,
        )

    @update_decorator
    async def async_turn_off(self, **kwargs) -> None:
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            on=False,
        )


def get_color_mode(resource: Light, supported_modes: set[ColorMode]) -> ColorMode:
    """Determine the correct mode

    :param resource: Light from aioafero
    :param supported_modes: Supported color modes
    """
    if not resource.color_mode:
        return list(supported_modes)[0] if len(supported_modes) else ColorMode.ONOFF
    elif resource.color_mode.mode == "color":
        return ColorMode.RGB
    elif resource.color_mode.mode == "white":
        if ColorMode.COLOR_TEMP in supported_modes:
            return ColorMode.COLOR_TEMP
        elif ColorMode.BRIGHTNESS in supported_modes:
            return ColorMode.BRIGHTNESS
        else:
            return ColorMode.ONOFF
    else:
        return list(supported_modes)[-1] if len(supported_modes) else ColorMode.ONOFF


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities."""
    bridge: HubspaceBridge = hass.data[DOMAIN][config_entry.entry_id]
    api: AferoBridgeV1 = bridge.api
    controller: LightController = api.lights

    def make_entities(resource: Light) -> list[HubspaceLight]:
        """Create light entity(ies) based on device capabilities."""
        device_name = _get_device_name(resource)
        if should_create_dual_lights(resource):
            LOGGER.info(f"Creating dual-mode lights for {device_name}")
            return [
                HubspaceColorLight(bridge, controller, resource),
                HubspaceWhiteLight(bridge, controller, resource)
            ]
        elif should_create_string_light_bulbs(resource):
            LOGGER.info(f"Creating string light bulbs for {device_name}")
            bulb_count = get_string_light_bulb_count(resource)
            return [
                HubspaceStringLightBulb(bridge, controller, resource, i, bulb_count)
                for i in range(bulb_count)
            ]
        else:
            LOGGER.info(f"Creating single light for {device_name}")
            return [HubspaceLight(bridge, controller, resource)]

    @callback
    def async_add_entity(event_type: EventType, resource: Light) -> None:
        """Add an entity or entities."""
        entities = make_entities(resource)
        async_add_entities(entities)

    # add all current items in controller
    all_entities = []
    for entity in controller:
        all_entities.extend(make_entities(entity))
    async_add_entities(all_entities)
    
    # register listener for new entities
    config_entry.async_on_unload(
        controller.subscribe(async_add_entity, event_filter=EventType.RESOURCE_ADDED)
    )
