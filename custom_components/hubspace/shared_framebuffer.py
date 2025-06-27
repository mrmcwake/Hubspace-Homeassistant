"""
Shared Framebuffer Context for Hampton Bay String Lights.

This module provides the SharedFramebufferContext class which manages
framebuffer state for Hampton Bay string lights with individual bulb control.
The context ensures consistent state across all bulb entities by maintaining
an authoritative cache of the framebuffer data.
"""
import logging
import time
import asyncio
from typing import Dict, Any, Optional, List

from aioafero.v1.models import Light
from .bridge import HubspaceBridge

LOGGER = logging.getLogger(__name__)


class SharedFramebufferContext:
    """
    Manages shared framebuffer state for Hampton Bay string lights.
    All individual bulb entities share this context to ensure consistent state.
    This version maintains its own authoritative framebuffer state to avoid stale reads.
    """
    def __init__(self, resource: Light, bridge: HubspaceBridge, expected_bulb_count: int = 12):
        self.resource = resource
        self.bridge = bridge
        self.controller = bridge.api.lights
        self.expected_bulb_count = expected_bulb_count
        self._state_lock = asyncio.Lock()
        # Maintain our own authoritative framebuffer state
        self._cached_framebuffer: Optional[list[dict]] = None
        self._framebuffer_initialized = False
        LOGGER.info(f"SharedFramebufferContext initialized for device {resource.id} with {expected_bulb_count} bulbs")

    def _read_framebuffer_from_resource(self) -> Optional[list[dict]]:
        """Read framebuffer directly from the resource's current state."""
        try:
            # Check color mode first
            color_mode = getattr(self.resource.color_mode, 'mode', None)
            LOGGER.debug(f"Current color mode: {color_mode}")
            
            # Debug: Log all available instances
            if hasattr(self.resource, 'instances') and self.resource.instances:
                LOGGER.debug(f"Available resource instances: {list(self.resource.instances.keys())}")
                for k, v in self.resource.instances.items():
                    LOGGER.debug(f"  Instance {k}: {type(v)} = {v}")
            else:
                LOGGER.debug("No instances found in resource")
                return None
            
            # Look for any color-sequence-v2 instances regardless of color mode
            sequence_instances = []
            for k, v in self.resource.instances.items():
                if isinstance(k, tuple) and k[0] == 'color-sequence-v2':
                    sequence_instances.append((k, v))
            
            LOGGER.debug(f"Found {len(sequence_instances)} color-sequence-v2 instances")
            
            # Try to extract framebuffer from any available sequence
            for k, seq_data in sequence_instances:
                LOGGER.debug(f"Checking sequence instance {k}")
                if isinstance(seq_data, dict):
                    framebuffer = None
                    
                    # Path 1: seq_data['color-sequence-v2']['frameBuffer']['framebuffer']
                    if 'color-sequence-v2' in seq_data:
                        cs2_data = seq_data['color-sequence-v2']
                        if isinstance(cs2_data, dict) and 'frameBuffer' in cs2_data:
                            fb_data = cs2_data['frameBuffer']
                            if isinstance(fb_data, dict) and 'framebuffer' in fb_data:
                                framebuffer = fb_data['framebuffer']
                    
                    # Path 2: seq_data['frameBuffer']['framebuffer'] (direct)
                    if not framebuffer and 'frameBuffer' in seq_data:
                        fb_data = seq_data['frameBuffer']
                        if isinstance(fb_data, dict) and 'framebuffer' in fb_data:
                            framebuffer = fb_data['framebuffer']
                    
                    # Path 3: seq_data['framebuffer'] (most direct)
                    if not framebuffer and 'framebuffer' in seq_data:
                        framebuffer = seq_data['framebuffer']
                    
                    if framebuffer and isinstance(framebuffer, list):
                        LOGGER.debug(f"Found framebuffer with {len(framebuffer)} bulbs")
                        return [dict(b) for b in framebuffer]  # Deep copy
                    else:
                        LOGGER.debug(f"No valid framebuffer found in sequence {k}")
            
            LOGGER.debug("No framebuffer found in any sequence instance")
            return None
            
        except Exception as e:
            LOGGER.error(f"Error reading framebuffer from resource: {e}")
            return None

    async def refresh_framebuffer_from_device(self) -> bool:
        """
        Actively refresh the framebuffer state from the device.
        This forces a re-read of the current device state.
        """
        try:
            LOGGER.info(f"Refreshing framebuffer from device {self.resource.id}")
            
            # Clear our cached framebuffer to force a fresh read
            old_framebuffer = self._cached_framebuffer
            self._cached_framebuffer = None
            
            # Try to read fresh state from resource
            fresh_framebuffer = self._read_framebuffer_from_resource()
            
            if fresh_framebuffer:
                self._cached_framebuffer = fresh_framebuffer
                self._framebuffer_initialized = True
                LOGGER.info(f"Successfully refreshed framebuffer with {len(fresh_framebuffer)} bulbs:")
                for i, bulb in enumerate(fresh_framebuffer):
                    color_brightness = bulb.get('colorBrightness', 0)
                    white_brightness = bulb.get('whiteBrightness', 0)
                    r, g, b = bulb.get('r', 0), bulb.get('g', 0), bulb.get('b', 0)
                    LOGGER.info(f"  Bulb {i}: RGB=({r},{g},{b}), ColorBrightness={color_brightness}, WhiteBrightness={white_brightness}")
                return True
            else:
                # If we can't read fresh state, restore the old cached version
                self._cached_framebuffer = old_framebuffer
                LOGGER.warning(f"Could not refresh framebuffer from device, keeping cached version")
                return False
                
        except Exception as e:
            LOGGER.error(f"Error refreshing framebuffer: {e}")
            return False

    def get_current_framebuffer(self) -> Optional[list[dict]]:
        """
        Get the current framebuffer state for individual bulb control.
        If we have our own cached version, use that. Otherwise try to read from resource.
        """
        try:
            # If we have our own cached framebuffer, use it (this is the authoritative state)
            if self._cached_framebuffer is not None:
                LOGGER.debug(f"SharedFramebufferContext: Using cached framebuffer with {len(self._cached_framebuffer)} bulbs")
                return self._cached_framebuffer
            
            # Try to initialize from resource data if available
            LOGGER.debug(f"SharedFramebufferContext: Attempting to initialize framebuffer from device {self.resource.id}")
            return self._read_framebuffer_from_resource()
            
        except Exception as e:
            LOGGER.error(f"Error getting current framebuffer: {e}")
            return None

    def get_power_state(self) -> Optional[str]:
        """Get the power state from the resource."""
        if hasattr(self.resource, 'on') and self.resource.on:
            return 'on' if self.resource.on.on else 'off'
        return None

    async def update_framebuffer(self, bulb_index: int, bulb_data: dict) -> bool:
        """
        Update a specific bulb in the framebuffer and send to device.
        """
        async with self._state_lock:
            try:
                LOGGER.info(f"SharedFramebufferContext: Updating bulb {bulb_index} with {bulb_data}")
                
                # Get current framebuffer or create one
                framebuffer = self.get_current_framebuffer()
                
                # If no framebuffer exists, we need to determine the expected bulb count
                # and create an appropriate framebuffer
                if not framebuffer:
                    LOGGER.info("No existing framebuffer found, creating new one")
                    
                    # Use the configured expected bulb count, but ensure it's at least large enough for this bulb_index
                    expected_bulb_count = max(self.expected_bulb_count, bulb_index + 1)
                    
                    # Create a new framebuffer with all bulbs off initially
                    framebuffer = []
                    for i in range(expected_bulb_count):
                        default_bulb = {
                            'r': 255, 'g': 255, 'b': 255,
                            'colorBrightness': 0,
                            'whiteBrightness': 0,
                            'cct': 3500
                        }
                        framebuffer.append(default_bulb)
                    
                    LOGGER.info(f"Created new framebuffer with {expected_bulb_count} bulbs")
                
                # Ensure framebuffer is large enough for this bulb index
                if bulb_index >= len(framebuffer):
                    LOGGER.info(f"Extending framebuffer from {len(framebuffer)} to {bulb_index + 1} bulbs")
                    while len(framebuffer) <= bulb_index:
                        # Add more bulbs with default off state
                        default_bulb = {
                            'r': 255, 'g': 255, 'b': 255,
                            'colorBrightness': 0,
                            'whiteBrightness': 0,
                            'cct': 3500
                        }
                        framebuffer.append(default_bulb)
                
                # Copy framebuffer to avoid mutating our cached version directly
                new_framebuffer = [dict(b) for b in framebuffer]
                
                # Update the specific bulb
                new_framebuffer[bulb_index].update(bulb_data)
                
                LOGGER.info(f"Updated bulb {bulb_index}: {new_framebuffer[bulb_index]}")
                
                # Build new sequence data
                new_sequence_data = {
                    "color-sequence-v2": {
                        "sequenceFlags": 0,
                        "brightnessSpeed": 50,
                        "motionSpeed": 48,
                        "motionEffect": 0,
                        "brightnessEffect": 0,
                        "headerFlags": 128,
                        "frameBuffer": {
                            "flags": 0,
                            "framebuffer": new_framebuffer
                        },
                        "id": 0,
                        "version": 1,
                        "brightnessDepth": 100
                    }
                }
                
                # Send update to device
                await self.bridge.async_request_call(
                    self.controller.update,
                    device_id=self.resource.id,
                    states=[{
                        "functionClass": "color-sequence-v2",
                        "functionInstance": "custom-1",
                        "value": new_sequence_data,
                        "lastUpdateTime": int(time.time())
                    }]
                )
                
                # Set color mode to individual and select the custom sequence
                await self.bridge.async_request_call(
                    self.controller.update,
                    device_id=self.resource.id,
                    states=[
                        {
                            "functionClass": "color-mode",
                            "value": "individual",
                            "lastUpdateTime": int(time.time())
                        },
                        {
                            "functionClass": "color-individual",
                            "functionInstance": "custom",
                            "value": "custom-1",
                            "lastUpdateTime": int(time.time())
                        }
                    ]
                )
                
                # Update our cached framebuffer to the new state
                # This ensures other bulbs see this update immediately
                self._cached_framebuffer = new_framebuffer
                self._framebuffer_initialized = True
                
                LOGGER.info(f"Successfully updated framebuffer for bulb {bulb_index} and cached new state")
                return True
                
            except Exception as e:
                LOGGER.error(f"Failed to update framebuffer for bulb {bulb_index}: {e}")
                import traceback
                LOGGER.error(f"Traceback: {traceback.format_exc()}")
                return False

    async def set_power_state(self, power_state: str) -> bool:
        """Set the power state of the string light."""
        try:
            await self.bridge.async_request_call(
                self.controller.set_state,
                device_id=self.resource.id,
                on=(power_state == "on")
            )
            return True
        except Exception as e:
            LOGGER.error(f"Failed to set power state: {e}")
            return False


# Global registry to manage shared contexts
_shared_contexts: Dict[str, SharedFramebufferContext] = {}


def get_shared_context(resource: Light, bridge: HubspaceBridge, expected_bulb_count: int = 12) -> SharedFramebufferContext:
    """
    Get or create a shared framebuffer context for a device.
    All bulbs for the same device will share this context.
    """
    if resource.id not in _shared_contexts:
        _shared_contexts[resource.id] = SharedFramebufferContext(resource, bridge, expected_bulb_count)
        LOGGER.info(f"Created shared framebuffer context for device {resource.id} with {expected_bulb_count} bulbs")
    return _shared_contexts[resource.id]


def cleanup_shared_context(device_id: str):
    """Clean up shared context when device is removed."""
    if device_id in _shared_contexts:
        del _shared_contexts[device_id]
        LOGGER.info(f"Cleaned up shared framebuffer context for device {device_id}")
