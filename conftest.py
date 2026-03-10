# conftest.py — configurazione pytest per Appunti AI
# Disabilita plugin ROS2 (launch_testing) che causano PluginValidationError
# su sistemi con ROS Humble installato system-wide.

def pytest_configure(config):
    # Rimuovi il plugin ROS launch_testing se presente
    pluginmanager = config.pluginmanager
    for plugin in list(pluginmanager.get_plugins()):
        mod = getattr(plugin, "__name__", "") or ""
        if "launch_testing_ros" in mod or "launch_testing" in mod:
            try:
                pluginmanager.unregister(plugin)
            except Exception:
                pass
