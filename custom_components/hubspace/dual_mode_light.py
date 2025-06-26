"""
Dual Mode Light Components

Works with devices like Commercial Electric 12in LED Flush Mount

This module provides dual-mode light detection logic and the HubspaceColorLight
and HubspaceWhiteLight entity classes for independent color and white control.
"""
import logging

from aioafero.v1 import LightController
from aioafero.v1.models import Light
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    filter_supported_color_modes,
)
from homeassistant.util.color import brightness_to_value

from .bridge import HubspaceBridge
from .entity import update_decorator
from .light import HubspaceLight

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
        if hasattr(resource, 'device_information') and resource.device_information and resource.device_information.name:
            device_identifiers.append(resource.device_information.name.lower())
        if hasattr(resource, 'device_information') and resource.device_information and resource.device_information.default_name:
            device_identifiers.append(resource.device_information.default_name.lower())
        
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
        LOGGER.info(f"Device {resource.id} has dual-mode capability - creating separate color and white entities")
    return result


class HubspaceColorLight(HubspaceLight):
    """Representation of the color portion of a dual-mode light."""
    
    def __init__(self, bridge: HubspaceBridge, controller: LightController, resource: Light) -> None:
        super().__init__(bridge, controller, resource)
        self._attr_name = f"{self._attr_name} - Color"
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
        """Turn on the color portion only, independently of white."""
        brightness: int | None = None
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(brightness_to_value((1, 100), kwargs[ATTR_BRIGHTNESS]))
        else:
            brightness = 100  # Default to full brightness if not specified
        color: tuple[int, int, int] | None = kwargs.get(ATTR_RGB_COLOR, None)
        # Always use mixed mode and set whiteBrightness to 0
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            on=True,
            color=color,
            color_mode="mixed",
            colorBrightness=brightness,
            whiteBrightness=0
        )

    @update_decorator
    async def async_turn_off(self, **kwargs) -> None:
        """Turn off only the color portion (set colorBrightness to 0, leave white alone)."""
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            color_mode="mixed",
            colorBrightness=0
        )


class HubspaceWhiteLight(HubspaceLight):
    """Representation of the white portion of a dual-mode light."""
    
    def __init__(self, bridge: HubspaceBridge, controller: LightController, resource: Light) -> None:
        super().__init__(bridge, controller, resource)
        self._attr_name = f"{self._attr_name} - White"
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
        """Turn on the white portion only, independently of color."""
        brightness: int | None = None
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(brightness_to_value((1, 100), kwargs[ATTR_BRIGHTNESS]))
        else:
            brightness = 100  # Default to full brightness if not specified
        temperature: int | None = kwargs.get(ATTR_COLOR_TEMP_KELVIN, None)
        # Always use mixed mode and set colorBrightness to 0
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            on=True,
            temperature=temperature,
            color_mode="mixed",
            whiteBrightness=brightness,
            colorBrightness=0
        )

    @update_decorator
    async def async_turn_off(self, **kwargs) -> None:
        """Turn off only the white portion (set whiteBrightness to 0, leave color alone)."""
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            color_mode="mixed",
            whiteBrightness=0
        )
