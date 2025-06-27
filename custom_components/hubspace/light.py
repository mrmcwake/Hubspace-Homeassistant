"""Home Assistant entity for interacting with Afero Light."""

from functools import partial
import logging
import asyncio
from typing import Optional

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
from .shared_framebuffer import get_shared_context

LOGGER = logging.getLogger(__name__)


class HubspaceLight(HubspaceBaseEntity, LightEntity):
    """Representation of an Afero light."""

    def __init__(
        self,
        bridge: HubspaceBridge,
        controller: LightController,
        resource: Light,
    ) -> None:
        """Initialize an Afero light."""

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
        """The brightness of this light between 1..255."""
        return (
            value_to_brightness((1, 100), self.resource.brightness)
            if self.resource.dimming
            else None
        )

    @property
    def color_mode(self) -> ColorMode:
        """Get the current color mode for the light."""
        return get_color_mode(self.resource, self._attr_supported_color_modes)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Get the current color temperature for the light."""
        return (
            self.resource.color_temperature.temperature
            if self.resource.color_temperature
            else None
        )

    @property
    def effect(self) -> str | None:
        """Get the current effect for the light."""
        return (
            self.resource.effect.effect
            if (self.resource.effect and self.resource.color_mode.mode == "sequence")
            else None
        )

    @property
    def effect_list(self) -> list[str] | None:
        """Get all available effects for the light."""
        all_effects = []
        for effects in self.resource.effect.effects.values() or []:
            all_effects.extend(effects)
        return all_effects or None

    @property
    def is_on(self) -> bool | None:
        """Determine if the light is currently on."""
        return self.resource.is_on

    @property
    def max_color_temp_kelvin(self) -> int | None:
        """Get the lights maximum temperature color."""
        return (
            max(self.resource.color_temperature.supported)
            if self.resource.color_temperature
            else None
        )

    @property
    def min_color_temp_kelvin(self) -> int | None:
        """Get the lights minimum temperature color."""
        return (
            min(self.resource.color_temperature.supported)
            if self.resource.color_temperature
            else None
        )

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Get the lights current RGB colors."""
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
        """Get all supported color modes."""
        return self._attr_supported_color_modes

    @property
    def supported_features(self) -> LightEntityFeature:
        """Get all supported light features."""
        if self.resource.effect:
            return LightEntityFeature(0) | LightEntityFeature.EFFECT
        return LightEntityFeature(0)

    @update_decorator
    async def async_turn_on(self, **kwargs) -> None:
        """Turn device on."""
        brightness: int | None = None
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(brightness_to_value((1, 100), kwargs[ATTR_BRIGHTNESS]))
        temperature: int | None = kwargs.get(ATTR_COLOR_TEMP_KELVIN)
        color: tuple[int, int, int] | None = kwargs.get(ATTR_RGB_COLOR)
        effect: str | None = kwargs.get(ATTR_EFFECT)
        color_mode: str | None = None
        if temperature:
            color_mode = "white"
        elif color:
            color_mode = "color"
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
        """Turn device off."""
        await self.bridge.async_request_call(
            self.controller.set_state,
            device_id=self.resource.id,
            on=False,
        )


def get_color_mode(resource: Light, supported_modes: set[ColorMode]) -> ColorMode:
    """Determine the correct mode.

    :param resource: Light from aioafero
    :param supported_modes: Supported color modes
    """
    if not resource.color_mode:
        return list(supported_modes)[0] if len(supported_modes) else ColorMode.ONOFF
    if resource.color_mode.mode == "color":
        return ColorMode.RGB
    if resource.color_mode.mode == "white":
        if ColorMode.COLOR_TEMP in supported_modes:
            return ColorMode.COLOR_TEMP
        if ColorMode.BRIGHTNESS in supported_modes:
            return ColorMode.BRIGHTNESS
        return ColorMode.ONOFF
    return list(supported_modes)[-1] if len(supported_modes) else ColorMode.ONOFF


# ============================================================================
# DUAL MODE LIGHT COMPONENTS
# ============================================================================

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


# ============================================================================
# STRING LIGHT COMPONENTS
# ============================================================================

def has_string_light_capability(resource: Light) -> bool:
    """Check if a light has string light capability with individual bulb control."""
    try:
        # Check device names for string light indicators
        device_names_to_check = []
        
        if hasattr(resource, 'device_information') and resource.device_information:
            if resource.device_information.name:
                device_names_to_check.append(resource.device_information.name.lower())
            if resource.device_information.default_name:
                device_names_to_check.append(resource.device_information.default_name.lower())
        
        # Check if any of the device names indicate string lights
        for device_name in device_names_to_check:
            if 'string' in device_name and 'light' in device_name:
                LOGGER.info(f"String light detected by name: {device_name}")
                return True
        
        # Check if device has color-sequence-v2 functions which indicate framebuffer support
        has_framebuffer = False
        if hasattr(resource, 'instances') and resource.instances:
            # Check for color-sequence-v2 functionClass in instances
            if 'color-sequence-v2' in resource.instances:
                LOGGER.info(f"String light detected by framebuffer capability: color-sequence-v2")
                has_framebuffer = True
        
        # Additional technical checks for string light capabilities
        
        # Check device information for string light indicators
        has_string_light_indicators = False
        if hasattr(resource, 'device_information') and resource.device_information:
            # Check default image for string light icon
            if (hasattr(resource.device_information, 'default_image') and 
                resource.device_information.default_image and 
                'string' in resource.device_information.default_image.lower()):
                LOGGER.info(f"String light detected by default image: {resource.device_information.default_image}")
                has_string_light_indicators = True
            
            # Check default name for "String Lights" 
            if (hasattr(resource.device_information, 'default_name') and 
                resource.device_information.default_name and 
                resource.device_information.default_name.lower() == 'string lights'):
                LOGGER.info(f"String light detected by default name: {resource.device_information.default_name}")
                has_string_light_indicators = True
                
            # Check model for Hampton Bay string light models
            if (hasattr(resource.device_information, 'model') and 
                resource.device_information.model and 
                resource.device_information.model.startswith('HB-') and
                'HS' in resource.device_information.model):
                LOGGER.info(f"String light detected by model pattern: {resource.device_information.model}")
                has_string_light_indicators = True
        
        # If we have framebuffer capability OR string light indicators, it's likely a string light
        if has_framebuffer or has_string_light_indicators:
            LOGGER.info(f"String light capability detected for device {resource.id} - framebuffer: {has_framebuffer}, indicators: {has_string_light_indicators}")
            return True
        
        return False
    except Exception as e:
        LOGGER.debug(f"Error checking string light capability: {e}")
        return False


def _extract_bulb_count_from_state(resource: Light) -> int:
    """Try to extract bulb count from resource state/framebuffer data."""
    try:
        # Check if resource has color-sequence-v2 instance with current state
        if hasattr(resource, 'instances') and resource.instances:
            if 'color-sequence-v2' in resource.instances:
                instance = resource.instances['color-sequence-v2']
                
                # Try to get current framebuffer data from the instance
                if hasattr(instance, 'state') and instance.state:
                    state = instance.state
                    
                    # Look for framebuffer data in various possible state keys
                    framebuffer_keys = ['framebuffer', 'frameBuffer', 'colorSequence', 'bulbs', 'leds']
                    
                    for key in framebuffer_keys:
                        if hasattr(state, key):
                            framebuffer_data = getattr(state, key)
                            if framebuffer_data and isinstance(framebuffer_data, (list, tuple)):
                                bulb_count = len(framebuffer_data)
                                LOGGER.info(f"Found {bulb_count} bulbs from state.{key}")
                                return bulb_count
                    
                    # Also check if state has any dictionary attributes that might contain framebuffer
                    if hasattr(state, '__dict__'):
                        for attr_name, attr_value in state.__dict__.items():
                            if isinstance(attr_value, (list, tuple)) and len(attr_value) > 0:
                                # Check if this looks like bulb data (has color/brightness info)
                                if len(attr_value) > 0 and isinstance(attr_value[0], dict):
                                    first_item = attr_value[0]
                                    if any(key in first_item for key in ['r', 'g', 'b', 'colorBrightness', 'whiteBrightness']):
                                        bulb_count = len(attr_value)
                                        LOGGER.info(f"Found {bulb_count} bulbs from state.{attr_name} (appears to be bulb data)")
                                        return bulb_count
        
        # Check if resource itself has any framebuffer-like attributes
        if hasattr(resource, '__dict__'):
            for attr_name, attr_value in resource.__dict__.items():
                if 'framebuffer' in attr_name.lower() or 'bulb' in attr_name.lower():
                    if isinstance(attr_value, (list, tuple)) and len(attr_value) > 0:
                        bulb_count = len(attr_value)
                        LOGGER.info(f"Found {bulb_count} bulbs from resource.{attr_name}")
                        return bulb_count
        
        return 0
        
    except Exception as e:
        LOGGER.debug(f"Error extracting bulb count from state: {e}")
        return 0


def get_string_light_bulb_count(resource: Light) -> int:
    """Get the number of individual bulbs in a string light."""
    try:
        LOGGER.info(f"Attempting to get bulb count for device {resource.id}")
        
        # Method 1: Try to extract from current state framebuffer data
        # Check if the resource has any existing state with framebuffer data
        bulb_count_from_state = _extract_bulb_count_from_state(resource)
        if bulb_count_from_state > 0:
            LOGGER.info(f"Found bulb count from state data: {bulb_count_from_state}")
            return bulb_count_from_state
        
        # Method 2: Try to get bulb count from color-sequence-v2 instance capabilities
        if hasattr(resource, 'instances') and resource.instances:
            LOGGER.info(f"Available instances: {list(resource.instances.keys())}")
            if 'color-sequence-v2' in resource.instances:
                instance = resource.instances['color-sequence-v2']
                LOGGER.info(f"Device has color-sequence-v2 capability, examining instance")
                
                # Try to get bulb count from instance attributes/capabilities
                if hasattr(instance, 'capabilities') and instance.capabilities:
                    capabilities = instance.capabilities
                    if hasattr(capabilities, 'maxBulbs') or hasattr(capabilities, 'max_bulbs'):
                        max_bulbs = getattr(capabilities, 'maxBulbs', None) or getattr(capabilities, 'max_bulbs', None)
                        if max_bulbs and isinstance(max_bulbs, int) and max_bulbs > 0:
                            LOGGER.info(f"Found bulb count from capabilities: {max_bulbs}")
                            return max_bulbs
                    
                    # Check for other capability indicators
                    if hasattr(capabilities, '__dict__'):
                        for attr_name, attr_value in capabilities.__dict__.items():
                            if 'bulb' in attr_name.lower() or 'led' in attr_name.lower() or 'count' in attr_name.lower():
                                if isinstance(attr_value, int) and attr_value > 0:
                                    LOGGER.info(f"Found potential bulb count from capabilities.{attr_name}: {attr_value}")
                                    return attr_value
                
                # Try to get default framebuffer size from instance
                if hasattr(instance, 'default_framebuffer_size'):
                    size = instance.default_framebuffer_size
                    if isinstance(size, int) and size > 0:
                        LOGGER.info(f"Found bulb count from default_framebuffer_size: {size}")
                        return size
                
        # Method 3: Check device model for known bulb counts
        if hasattr(resource, 'device_information') and resource.device_information:
            if (hasattr(resource.device_information, 'model') and 
                resource.device_information.model):
                model = resource.device_information.model
                LOGGER.info(f"Checking model '{model}' for bulb count")
                
                # Hampton Bay HB-10521-HS is a 12-bulb string light
                if model == 'HB-10521-HS':
                    LOGGER.info(f"Known 12-bulb model: {model}")
                    return 12
        
        # Default to 12 bulbs for Hampton Bay string lights if we can't determine exact count
        default_bulb_count = 12
        LOGGER.info(f"Using default bulb count of {default_bulb_count} for device {resource.id}")
        return default_bulb_count
        
    except Exception as e:
        LOGGER.debug(f"Error getting bulb count: {e}")
        return 12


def should_create_string_light_bulbs(resource: Light) -> bool:
    """Determine if we should create individual bulb entities for string lights."""
    
    LOGGER.info(f"Checking device {resource.id} for string light capability")
    
    if hasattr(resource, 'device_information') and resource.device_information:
        LOGGER.info(f"  Device info - name: {resource.device_information.name}, default_name: {resource.device_information.default_name}")
    
    if hasattr(resource, 'instances') and resource.instances:
        LOGGER.info(f"  Device instances: {list(resource.instances.keys())}")
    
    result = has_string_light_capability(resource)
    if result:
        bulb_count = get_string_light_bulb_count(resource)
        LOGGER.info(f"Device {resource.id} has string light capability with {bulb_count} individual bulbs")
    else:
        LOGGER.info(f"Device {resource.id} does not have string light capability")
    
    return result


class HubspaceStringLightBulb(HubspaceBaseEntity, LightEntity):
    """Representation of an individual bulb in a string light."""
    
    def __init__(
        self,
        bridge: HubspaceBridge,
        controller: LightController,
        resource: Light,
        bulb_index: int,
        total_bulbs: int
    ) -> None:
        super().__init__(bridge, controller, resource)
        self._bulb_index = bulb_index
        self._total_bulbs = total_bulbs
        self._attr_name = f"{self._attr_name} - Bulb {bulb_index + 1}"
        self._attr_unique_id = f"{resource.id}_bulb_{bulb_index}"
        
        # Sharing a local context across bulbs to ensure immediate access
        # to the current frame buffer state. Otherwise if we wait for update
        # then subsequent calls would overwrite the previous bulb state. 
        self._shared_context = get_shared_context(resource, bridge, total_bulbs)
        
        # String light bulbs support RGB, brightness, and color temperature
        supported_color_modes = {ColorMode.ONOFF, ColorMode.RGB, ColorMode.BRIGHTNESS}
        if self.resource.supports_color_temperature:
            supported_color_modes.add(ColorMode.COLOR_TEMP)
            
        self._attr_supported_color_modes = filter_supported_color_modes(supported_color_modes)
        
        # Cache current bulb state
        self._current_bulb_state = {
            'r': 255, 'g': 255, 'b': 255,
            'colorBrightness': 100,
            'whiteBrightness': 0,
            'cct': 3500
        }
        self._is_on = False
        
        LOGGER.info(f"Created string light bulb {bulb_index + 1} of {total_bulbs} for device {resource.id}")
    
    async def _update_bulb_state_from_resource(self) -> None:
        """Update cached bulb state from the shared framebuffer context."""
        try:
            # Check if device power state and refresh framebuffer if needed
            power_state = self._shared_context.get_power_state()
            LOGGER.info(f"Bulb {self._bulb_index} updating state, device power state: {power_state}")
            
            if power_state == "on":
                # Try to refresh framebuffer from device to get current colors
            if power_state == "on":
                # Try to refresh framebuffer from device to get current colors
                refresh_success = await self._shared_context.refresh_framebuffer_from_device()
                LOGGER.info(f"Bulb {self._bulb_index} framebuffer refresh result: {refresh_success}")
                
                # Even if refresh failed, check if we have cached framebuffer data
                if not refresh_success:
                    LOGGER.warning(f"Bulb {self._bulb_index} refresh failed, checking for cached framebuffer data")
            
            # Use shared context to get current framebuffer - this includes refreshed data
            current_framebuffer = self._shared_context.get_current_framebuffer()
            if current_framebuffer and len(current_framebuffer) > self._bulb_index:                LOGGER.info(f"Bulb {self._bulb_index} framebuffer data: {current_framebuffer[self._bulb_index]}")
            
            if current_framebuffer and self._bulb_index < len(current_framebuffer):
                bulb_data = current_framebuffer[self._bulb_index]
                if isinstance(bulb_data, dict):
                    # Store previous state for comparison
                    old_state = dict(self._current_bulb_state)
                    self._current_bulb_state.update(bulb_data)
                    
                    # Determine if bulb is "on" based on brightness values
                    color_brightness = bulb_data.get('colorBrightness', 0)
                    white_brightness = bulb_data.get('whiteBrightness', 0)
                    self._is_on = (color_brightness > 0 or white_brightness > 0)
                    
                    # Log state change if significant
                    if (old_state.get('r') != bulb_data.get('r') or 
                        old_state.get('g') != bulb_data.get('g') or 
                        old_state.get('b') != bulb_data.get('b') or
                        old_state.get('colorBrightness') != color_brightness or
                        old_state.get('whiteBrightness') != white_brightness):
                        LOGGER.info(f"Bulb {self._bulb_index} state changed: on={self._is_on}, "
                                f"RGB=({bulb_data.get('r', 0)}, {bulb_data.get('g', 0)}, {bulb_data.get('b', 0)}), "
                                f"colorBrightness={color_brightness}, whiteBrightness={white_brightness}")
                    
                    return
            
            # Fallback to check overall device power state
            if power_state == "on":
                # Device is on but we don't have framebuffer data yet - set default color
                self._is_on = True
                # Provide default color data so bulbs aren't just "on" with no color
                self._current_bulb_state.update({
                    'r': 255, 'g': 255, 'b': 255,
                    'colorBrightness': 50,
                    'whiteBrightness': 0
                })
                LOGGER.warning(f"Bulb {self._bulb_index} no framebuffer data - using default white color")
            else:
                self._is_on = False
                LOGGER.info(f"Bulb {self._bulb_index} device is off")
                
        except Exception as e:
            LOGGER.error(f"Error updating bulb {self._bulb_index} state: {e}")
            # Default to off if we can't determine state
            self._is_on = False
    
    async def async_added_to_hass(self) -> None:
        """Subscribe to updates when entity is added to hass."""
        await super().async_added_to_hass()
        LOGGER.info(f"Bulb {self._bulb_index} being added to Home Assistant")
        
        # Initialize bulb state from device
        await self._update_bulb_state_from_resource()
        
        # Force immediate state write to Home Assistant        self.async_write_ha_state()
        
        # Set up a periodic state refresh to keep in sync with the device
        # This helps ensure HomeAssistant reflects actual device state
        async def periodic_update():
            """Periodically update state from device."""
            try:
                await self._update_bulb_state_from_resource()
                self.async_write_ha_state()
            except Exception as e:
                LOGGER.debug(f"Error in periodic update for bulb {self._bulb_index}: {e}")
        
        # Schedule periodic updates every 30 seconds
        async def schedule_updates():
            while True:
                await asyncio.sleep(60)
                await periodic_update()
        
        self.hass.async_create_task(schedule_updates())
    
    @property
    def is_on(self) -> bool:
        """Return if the individual bulb is on."""
        return self._is_on
    
    @property
    def brightness(self) -> int | None:
        """Return the brightness of this bulb."""
        # Use color brightness if available, otherwise white brightness
        color_brightness = self._current_bulb_state.get('colorBrightness', 0)
        white_brightness = self._current_bulb_state.get('whiteBrightness', 0)
        brightness = max(color_brightness, white_brightness)
        return value_to_brightness((1, 100), brightness) if brightness > 0 else None
    
    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the RGB color of this bulb."""
        if self._current_bulb_state.get('colorBrightness', 0) > 0:
            return (
                self._current_bulb_state.get('r', 255),
                self._current_bulb_state.get('g', 255),
                self._current_bulb_state.get('b', 255)
            )
        return None
    
    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature of this bulb."""
        if self._current_bulb_state.get('whiteBrightness', 0) > 0:
            return self._current_bulb_state.get('cct', 3500)
        return None
    
    @property
    def color_mode(self) -> ColorMode:
        """Return the current color mode."""
        color_brightness = self._current_bulb_state.get('colorBrightness', 0)
        white_brightness = self._current_bulb_state.get('whiteBrightness', 0)
        
        if color_brightness > 0 and ColorMode.RGB in self._attr_supported_color_modes:
            return ColorMode.RGB
        elif white_brightness > 0 and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            return ColorMode.COLOR_TEMP
        elif ColorMode.BRIGHTNESS in self._attr_supported_color_modes:
            return ColorMode.BRIGHTNESS
        else:
            return ColorMode.ONOFF
    
    @property
    def max_color_temp_kelvin(self) -> int | None:
        """Return the max color temperature."""
        return 6500 if ColorMode.COLOR_TEMP in self._attr_supported_color_modes else None
    
    @property
    def min_color_temp_kelvin(self) -> int | None:
        """Return the min color temperature."""
        return 2700 if ColorMode.COLOR_TEMP in self._attr_supported_color_modes else None
    
    @update_decorator
    async def async_turn_on(self, **kwargs) -> None:
        """Turn on this individual bulb using SharedFramebufferContext."""
        bulb_updates = {}
        
        # Ensure string light is powered on first
        power_state = self._shared_context.get_power_state()
        if power_state != "on":
            LOGGER.info(f"Turning on string light device {self.resource.id}")
            await self._shared_context.set_power_state("on")
        
        # Handle brightness
        if ATTR_BRIGHTNESS in kwargs:
            brightness = int(brightness_to_value((1, 100), kwargs[ATTR_BRIGHTNESS]))
            # Check if we're in color or white mode
            if ATTR_RGB_COLOR in kwargs:
                bulb_updates['colorBrightness'] = brightness
                bulb_updates['whiteBrightness'] = 0
            elif ATTR_COLOR_TEMP_KELVIN in kwargs:
                bulb_updates['whiteBrightness'] = brightness
                bulb_updates['colorBrightness'] = 0
            else:
                # Default to color brightness
                bulb_updates['colorBrightness'] = brightness
        
        # Handle RGB color
        if ATTR_RGB_COLOR in kwargs:
            r, g, b = kwargs[ATTR_RGB_COLOR]
            bulb_updates.update({
                'r': r, 'g': g, 'b': b,
                'colorBrightness': bulb_updates.get('colorBrightness', 100),
                'whiteBrightness': 0
            })
        
        # Handle color temperature
        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            temp_k = kwargs[ATTR_COLOR_TEMP_KELVIN]
            bulb_updates.update({
                'cct': temp_k,
                'whiteBrightness': bulb_updates.get('whiteBrightness', 100),
                'colorBrightness': 0,
                'r': 0, 'g': 0, 'b': 0
            })
        
        # If no specific parameters, just turn on with current color
        if not bulb_updates:
            bulb_updates = {'colorBrightness': 100}
        
        # Use shared context to update this bulb - preserves other bulb state
        success = await self._shared_context.update_framebuffer(self._bulb_index, bulb_updates)
        
        if success:
            # Update our local cache
            self._current_bulb_state.update(bulb_updates)
            self._is_on = (self._current_bulb_state.get('colorBrightness', 0) > 0 or 
                          self._current_bulb_state.get('whiteBrightness', 0) > 0)
            LOGGER.info(f"Successfully turned on bulb {self._bulb_index} with updates: {bulb_updates}")
        else:
            LOGGER.error(f"Failed to turn on bulb {self._bulb_index}")
    
    @update_decorator
    async def async_turn_off(self, **kwargs) -> None:
        """Turn off this individual bulb using SharedFramebufferContext."""
        bulb_updates = {
            'colorBrightness': 0,
            'whiteBrightness': 0
        }
        
        # Use shared context to update this bulb - this preserves other bulbs!
        success = await self._shared_context.update_framebuffer(self._bulb_index, bulb_updates)
        
        if success:
            # Update our local cache
            self._current_bulb_state.update(bulb_updates)
            self._is_on = False
            LOGGER.info(f"Successfully turned off bulb {self._bulb_index}")
        else:
            LOGGER.error(f"Failed to turn off bulb {self._bulb_index}")
    
    @callback
    def on_update(self) -> None:
        """Called when the parent device is updated - refresh our state."""
        # Trigger an async state update when the device changes
        # This ensures we get the latest framebuffer when device state changes
        self.hass.async_create_task(self._handle_device_update())

    async def _handle_device_update(self) -> None:
        """Handle device update event asynchronously."""
        try:
            await self._update_bulb_state_from_resource()
            self.async_write_ha_state()
        except Exception as e:
            LOGGER.debug(f"Error handling device update for bulb {self._bulb_index}: {e}")


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up entities."""
    bridge: HubspaceBridge = hass.data[DOMAIN][config_entry.entry_id]
    api: AferoBridgeV1 = bridge.api
    controller: LightController = api.lights
    make_entity = partial(HubspaceLight, bridge, controller)

    def make_entities(resource: Light) -> list[HubspaceLight]:
        """Create light entity(ies) based on device capabilities."""
        try:
            # Try enhanced functionality first
            if should_create_dual_lights(resource):
                LOGGER.info(f"Creating dual-mode lights for device {resource.id}")
                return [
                    HubspaceColorLight(bridge, controller, resource),
                    HubspaceWhiteLight(bridge, controller, resource)
                ]
            
            if should_create_string_light_bulbs(resource):
                LOGGER.info(f"Creating string light bulbs for device {resource.id}")
                bulb_count = get_string_light_bulb_count(resource)
                return [
                    HubspaceStringLightBulb(bridge, controller, resource, i, bulb_count)
                    for i in range(bulb_count)
                ]

        except Exception as e:
            # If enhanced functionality fails, fall back to original behavior
            LOGGER.warning(f"Enhanced light functionality failed for device {resource.id}, using standard light: {e}")
        
        # Default: use original single light entity (preserves original behavior)
        return [make_entity(resource)]

    @callback
    def async_add_entity(event_type: EventType, resource: Light) -> None:
        """Add an entity or entities."""
        entities = make_entities(resource)
        async_add_entities(entities)

    # add all current items in controller using new logic
    all_entities = []
    for entity in controller:
        all_entities.extend(make_entities(entity))
    async_add_entities(all_entities)
    
    # register listener for new entities
    config_entry.async_on_unload(
        controller.subscribe(async_add_entity, event_filter=EventType.RESOURCE_ADDED)
    )
