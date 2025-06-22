from functools import partial
import logging
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

LOGGER = logging.getLogger(__name__)

def has_mixed_mode_capability(resource: Light) -> bool:
    """Check if a light has mixed mode capability (separate color and white controls)."""
    try:
        # Check if the device has mixed mode in its color mode capabilities
        if hasattr(resource, 'color_mode') and resource.color_mode:
            # For now, we'll use a broader check - if device has both color and white brightness controls
            return has_separate_brightness_controls(resource)
        return False
    except Exception as e:
        LOGGER.debug(f"Error checking mixed mode capability: {e}")
        return False

def has_separate_brightness_controls(resource: Light) -> bool:
    """Check if device has separate brightness controls for color and white."""
    try:
        # Check if device supports both color and white controls
        has_color = (hasattr(resource, 'supports_color') and resource.supports_color) or (hasattr(resource, 'color') and resource.color)
        has_temperature = (hasattr(resource, 'supports_color_temperature') and resource.supports_color_temperature) or (hasattr(resource, 'color_temperature') and resource.color_temperature)
        has_dimming = hasattr(resource, 'supports_dimming') and resource.supports_dimming
        
        # Check for Hampton Bay Flushmount Light or similar dual-mode devices
        # This could be expanded to include other devices with mixed-mode capability
        device_identifiers = []
        if hasattr(resource, 'name') and resource.name:
            device_identifiers.append(resource.name.lower())
        
        # Check if this looks like a device that might have dual-mode capability
        # Devices with both RGB and tunable white usually have this capability
        is_dual_mode_candidate = (
            has_color and has_temperature and has_dimming and
            any('flushmount' in identifier for identifier in device_identifiers)
        )
        
        return is_dual_mode_candidate
    except Exception as e:
        LOGGER.debug(f"Error checking brightness controls: {e}")
        return False

def should_create_dual_lights(resource: Light) -> bool:
    """Determine if we should create separate color and white light entities."""
    result = has_mixed_mode_capability(resource) and has_separate_brightness_controls(resource)
    if result:
        LOGGER.info(f"Device {resource.name} has dual-mode capability - creating separate color and white entities")
    return result

class HubspaceLight(HubspaceBaseEntity, LightEntity):
    def __init__(
        self,
        bridge: HubspaceBridge,
        controller: LightController,
        resource: Light,
    ) -> None:
        super().__init__(bridge, controller, resource)
        LOGGER.warning("Creating light %s", resource)
        LOGGER.warning("Creating light", resource.instances)
        LOGGER.warning("Modes????", resource.get_instance("color-mode"))
        LOGGER.warning("COlor modes", controller.items[0].instances)
        LOGGER.warning("COlor modes", controller.items[1].instances)
        LOGGER.warning("Items", controller.items)
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

class HubspaceColorLight(HubspaceLight):
    """Representation of the color portion of a dual-mode light."""
    
    def __init__(self, bridge: HubspaceBridge, controller: LightController, resource: Light) -> None:
        super().__init__(bridge, controller, resource)
        self._attr_name = f"{resource.name} Color"
        self._attr_unique_id = f"{resource.id}_color"
        # Force color mode only - remove temperature support for color portion
        supported_modes = {ColorMode.ONOFF}
        if self.resource.supports_color:
            supported_modes.add(ColorMode.RGB)
        if self.resource.supports_dimming:
            supported_modes.add(ColorMode.BRIGHTNESS)
        self._attr_supported_color_modes = filter_supported_color_modes(supported_modes)
    
    @property
    def color_mode(self) -> ColorMode:
        """Return the color mode - RGB if we have color, brightness otherwise."""
        if ColorMode.RGB in self._attr_supported_color_modes and self.resource.color:
            return ColorMode.RGB
        elif ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            return ColorMode.BRIGHTNESS
        else:
            return ColorMode.ONOFF
    
    @update_decorator
    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the color portion only."""
        brightness: int | None = None
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(brightness_to_value((1, 100), kwargs[ATTR_BRIGHTNESS]))
        
        color: tuple[int, int, int] | None = kwargs.get(ATTR_RGB_COLOR, None)
        
        # Use mixed mode to control both independently
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            on=True,
            brightness=brightness,
            color=color,
            color_mode="mixed"  # Always use mixed mode
        )

class HubspaceWhiteLight(HubspaceLight):
    """Representation of the white portion of a dual-mode light."""
    
    def __init__(self, bridge: HubspaceBridge, controller: LightController, resource: Light) -> None:
        super().__init__(bridge, controller, resource)
        self._attr_name = f"{resource.name} White"
        self._attr_unique_id = f"{resource.id}_white"
        # Force white modes only - remove RGB support for white portion
        supported_modes = {ColorMode.ONOFF}
        if self.resource.supports_color_temperature:
            supported_modes.add(ColorMode.COLOR_TEMP)
        if self.resource.supports_dimming:
            supported_modes.add(ColorMode.BRIGHTNESS)
        self._attr_supported_color_modes = filter_supported_color_modes(supported_modes)
    
    @property
    def color_mode(self) -> ColorMode:
        """Return the color mode - color temp if available, brightness otherwise."""
        if ColorMode.COLOR_TEMP in self._attr_supported_color_modes and self.resource.color_temperature:
            return ColorMode.COLOR_TEMP
        elif ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            return ColorMode.BRIGHTNESS
        else:
            return ColorMode.ONOFF
    
    @update_decorator
    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the white portion only."""
        brightness: int | None = None
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(brightness_to_value((1, 100), kwargs[ATTR_BRIGHTNESS]))
        
        temperature: int | None = kwargs.get(ATTR_COLOR_TEMP_KELVIN, None)
        
        # Use mixed mode to control both independently
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            on=True,
            brightness=brightness,
            temperature=temperature,
            color_mode="mixed"  # Always use mixed mode
        )

def get_color_mode(resource: Light, supported_modes: set[ColorMode]) -> ColorMode:
    """Determine the correct mode

    :param resource: Light from aioafero
    :param supported_modes: Supported color modes
    """
    LOGGER.warning("!!!!! Color mode %s", resource.color_mode)
    LOGGER.warning("!!!!! supported mode %s", supported_modes)
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
        if should_create_dual_lights(resource):
            LOGGER.info(f"Creating dual-mode lights for {resource.name}")
            return [
                HubspaceColorLight(bridge, controller, resource),
                HubspaceWhiteLight(bridge, controller, resource)
            ]
        else:
            return [HubspaceLight(bridge, controller, resource)]

    LOGGER.warning("Here are the lights2")
    LOGGER.warning(api.lights.items)

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
