"""
String Light Components for Hampton Bay String Lights.

This module provides string light detection logic and the HubspaceStringLightBulb
entity class for individual bulb control within Hampton Bay string lights.
"""
import logging
import asyncio
from typing import Optional

from aioafero.v1 import LightController
from aioafero.v1.models import Light
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    filter_supported_color_modes,
)
from homeassistant.util.color import brightness_to_value, value_to_brightness

from .bridge import HubspaceBridge
from .entity import HubspaceBaseEntity, update_decorator
from .shared_framebuffer import get_shared_context

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
        device_name = _get_device_name(resource)
        
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
            LOGGER.info(f"String light capability detected for device '{device_name}' - framebuffer: {has_framebuffer}, indicators: {has_string_light_indicators}")
            return True
        
        return False
    except Exception as e:
        LOGGER.debug(f"Error checking string light capability: {e}")
        return False


def get_string_light_bulb_count(resource: Light) -> int:
    """Get the number of individual bulbs in a string light."""
    try:
        device_name = _get_device_name(resource)
        LOGGER.info(f"Attempting to get bulb count for device '{device_name}'")
        
        # Method 1: Try to extract from current state framebuffer data
        # Check if the resource has any existing state with framebuffer data
        bulb_count_from_state = _extract_bulb_count_from_state(resource)
        if bulb_count_from_state > 0:
            LOGGER.info(f"Found bulb count from state data: {bulb_count_from_state}")
            return bulb_count_from_state
        
        # Method 2: Check instances for color-sequence-v2 capability
        if hasattr(resource, 'instances') and resource.instances:
            LOGGER.info(f"Available instances: {list(resource.instances.keys())}")
            if 'color-sequence-v2' in resource.instances:
                LOGGER.info(f"Device has color-sequence-v2 capability, using default bulb count")
                
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
        LOGGER.info(f"Using default bulb count of {default_bulb_count} for device '{device_name}'")
        return default_bulb_count
        
    except Exception as e:
        LOGGER.debug(f"Error getting bulb count: {e}")
        return 12


def _extract_bulb_count_from_state(resource: Light) -> int:
    """Try to extract bulb count from resource state/framebuffer data."""
    try:
        # This function attempts to extract bulb count from the resource's current state
        # In some cases, the framebuffer data might be available in the resource
        
        # Check if resource has any color-sequence-v2 state data
        # This is a bit speculative since we don't have direct access to the state at init time
        # But the aioafero library might populate some initial state
        
        if hasattr(resource, 'effect') and resource.effect:
            # Some framebuffer data might be available through effect state
            # This would need to be verified with actual device testing
            pass
            
        # For now, return 0 to indicate we couldn't determine from state
        return 0
        
    except Exception as e:
        LOGGER.debug(f"Error extracting bulb count from state: {e}")
        return 0


def should_create_string_light_bulbs(resource: Light) -> bool:
    """Determine if we should create individual bulb entities for string lights."""
    device_name = _get_device_name(resource)
    
    # Add debug logging to understand the device structure
    LOGGER.info(f"Checking device '{device_name}' for string light capability")
    
    if hasattr(resource, 'device_information') and resource.device_information:
        LOGGER.info(f"  Device info - name: {resource.device_information.name}, default_name: {resource.device_information.default_name}")
    
    if hasattr(resource, 'instances') and resource.instances:
        LOGGER.info(f"  Device instances: {list(resource.instances.keys())}")
    
    result = has_string_light_capability(resource)
    if result:
        bulb_count = get_string_light_bulb_count(resource)
        LOGGER.info(f"Device {device_name} has string light capability with {bulb_count} individual bulbs")
    else:
        LOGGER.info(f"Device {device_name} does not have string light capability")
    
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
        device_name = _get_device_name(resource)
        self._attr_name = f"{device_name} Bulb {bulb_index + 1}"
        self._attr_unique_id = f"{resource.id}_bulb_{bulb_index}"
        
        # Get shared framebuffer context - this is the key to the solution!
        self._shared_context = get_shared_context(resource, bridge)
        
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
            # Use shared context to get current framebuffer - this bypasses aioafero caching!
            current_framebuffer = self._shared_context.get_current_framebuffer()
            
            if current_framebuffer and self._bulb_index < len(current_framebuffer):
                bulb_data = current_framebuffer[self._bulb_index]
                if isinstance(bulb_data, dict):
                    self._current_bulb_state.update(bulb_data)
                    # Determine if bulb is "on" based on brightness values
                    color_brightness = bulb_data.get('colorBrightness', 0)
                    white_brightness = bulb_data.get('whiteBrightness', 0)
                    self._is_on = (color_brightness > 0 or white_brightness > 0)
                    LOGGER.debug(f"Updated bulb {self._bulb_index} state from shared context: on={self._is_on}, color_brightness={color_brightness}, white_brightness={white_brightness}")
                    return
            
            # Fallback to check overall device power state
            power_state = self._shared_context.get_power_state()
            if power_state == "on":
                # Device is on but we don't have framebuffer data yet
                self._is_on = True
                LOGGER.debug(f"Bulb {self._bulb_index} assuming on state from device power")
            else:
                self._is_on = False
                LOGGER.debug(f"Bulb {self._bulb_index} assuming off state")
                
        except Exception as e:
            LOGGER.debug(f"Error updating bulb state: {e}")
            # Default to off if we can't determine state
            self._is_on = False
    
    async def async_added_to_hass(self) -> None:
        """Subscribe to updates when entity is added to hass."""
        await super().async_added_to_hass()
        
        # Initialize bulb state from device
        await self._update_bulb_state_from_resource()
        
        # Set up a periodic state refresh to keep in sync with the device
        # This helps ensure HomeAssistant reflects actual device state
        async def periodic_update():
            """Periodically update state from device."""
            try:
                await self.update_from_device_state()
                self.async_write_ha_state()
            except Exception as e:
                LOGGER.debug(f"Error in periodic update for bulb {self._bulb_index}: {e}")
        
        # Schedule periodic updates every 30 seconds
        async def schedule_updates():
            while True:
                await asyncio.sleep(30)
                await periodic_update()
        
        # Start the update task but don't block
        self.hass.async_create_task(schedule_updates())
    
    @property
    def is_on(self) -> bool:
        """Return if the individual bulb is on."""
        # Only update state periodically, not on every property access
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
        
        # Use shared context to update this bulb - this preserves other bulbs!
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
