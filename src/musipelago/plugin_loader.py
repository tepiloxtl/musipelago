# -*- coding: utf-8 -*-
import os
import importlib.util
import inspect
from kivy.logger import Logger
from .backends import AbstractMusicBackend

class PluginManager:
    """
    Discovers and loads music backend plugins from a directory.
    Each plugin is expected to define a 'MUSIPELAGO_PLUGIN' dictionary.
    """
    def __init__(self, plugin_dir='plugins'):
        self.plugin_dir = plugin_dir
        # This now stores the full plugin manifest dictionary, keyed by module name
        self.plugins = {} 
        
        if not os.path.exists(self.plugin_dir):
            os.makedirs(self.plugin_dir)
            Logger.info(f"PluginManager: Created missing plugin directory: {self.plugin_dir}")

    def discover_plugins(self):
        """
        Scans the plugin directory, loads modules, and finds plugin manifests.
        """
        self.plugins.clear()
        Logger.info(f"PluginManager: Discovering plugins in '{self.plugin_dir}'...")
        
        for filename in os.listdir(self.plugin_dir):
            if not filename.endswith('.py') or filename == '__init__.py':
                continue

            module_name = filename[:-3]
            filepath = os.path.join(self.plugin_dir, filename)

            try:
                spec = importlib.util.spec_from_file_location(module_name, filepath)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                # --- NEW REGISTRATION ---
                # We no longer look for a single class. We look for the manifest dict.
                if hasattr(module, 'MUSIPELAGO_PLUGIN'):
                    plugin_manifest = getattr(module, 'MUSIPELAGO_PLUGIN')
                    
                    if isinstance(plugin_manifest, dict):
                        self.plugins[module_name] = plugin_manifest
                        friendly_name = plugin_manifest.get('name', module_name)
                        Logger.info(f"PluginManager: Loaded plugin '{friendly_name}' from {filename}.")
                    else:
                        Logger.warning(f"PluginManager: '{filename}' has 'MUSIPELAGO_PLUGIN' but it's not a dict.")
                else:
                    Logger.info(f"PluginManager: Skipping '{filename}' (not a Musipelago plugin).")

            except Exception as e:
                Logger.error(f"PluginManager: Failed to load plugin '{filename}': {e}")
                
        Logger.info(f"PluginManager: Discovery complete. Found {len(self.plugins)} plugins.")

    def get_available_backends(self, app_type_key: str) -> list[str]:
        """
        Returns a list of plugin module names that support a specific app type
        (e.g., 'generator_backend').
        """
        available = []
        for module_name, manifest in self.plugins.items():
            # Check if it has *both* a backend and a UI for this app type
            if (app_type_key in manifest and 
                f"{app_type_key.split('_')[0]}_ui" in manifest):
                available.append(module_name)
        return available

    def get_plugin_component_class(self, module_name: str, component_key: str):
        """
        Returns the specific class for a given plugin and component key.
        """
        manifest = self.plugins.get(module_name)
        if not manifest:
            return None
        return manifest.get(component_key) # Returns the class, or None

    def get_plugin_manifest(self, module_name: str) -> dict:
        """Returns the full manifest dictionary for a plugin."""
        return self.plugins.get(module_name)