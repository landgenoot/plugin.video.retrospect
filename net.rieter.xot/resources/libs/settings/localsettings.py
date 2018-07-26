#===============================================================================
# LICENSE Retrospect-Framework - CC BY-NC-ND
#===============================================================================
# This work is licenced under the Creative Commons
# Attribution-Non-Commercial-No Derivative Works 3.0 Unported License. To view a
# copy of this licence, visit http://creativecommons.org/licenses/by-nc-nd/3.0/
# or send a letter to Creative Commons, 171 Second Street, Suite 300,
# San Francisco, California 94105, USA.
#===============================================================================

import os
import io
import json
import shutil

import settingsstore


class LocalSettings(settingsstore.SettingsStore):
    __settings = None

    __SETTINGS_KEY = "settings"
    __CHANNELS_KEY = "channels"

    def __init__(self, addon_data_folder, logger):
        super(LocalSettings, self).__init__(logger)

        if not addon_data_folder or not os.path.isdir(addon_data_folder):
            raise ValueError("Invalid add-data path: {0}".format(addon_data_folder))

        self.addon_data_folder = addon_data_folder
        self.local_settings_file = os.path.join(self.addon_data_folder, "settings.json")

        # init the settings
        if LocalSettings.__settings is None:
            self.__load_settings()

    def set_setting(self, setting_id, setting_value, channel=None):
        if channel is None:
            self._logger.Debug("Local Setting  Updated: '%s': '%s'", setting_id, setting_value)
            LocalSettings.__settings[LocalSettings.__SETTINGS_KEY][setting_id] = setting_value
        else:
            self._logger.Debug("Local Channel Setting Updated: '%s:%s': '%s'",
                               channel.id, setting_id, setting_value)

            if channel.id not in LocalSettings.__settings[LocalSettings.__CHANNELS_KEY]:
                LocalSettings.__settings[LocalSettings.__CHANNELS_KEY][channel.id] = {}

            LocalSettings.__settings[LocalSettings.__CHANNELS_KEY][channel.id][setting_id] = \
                setting_value

        # store the file
        self.__store_settings()
        return setting_value

    def get_boolean_setting(self, setting_id, channel=None, default=None):
        return self.get_setting(setting_id, channel, default)

    def get_integer_setting(self, setting_id, channel=None, default=None):
        return self.get_setting(setting_id, channel, default)

    def get_setting(self, setting_id, channel=None, default=None):
        if channel is None:
            setting_value = LocalSettings.__settings["settings"].get(setting_id, default)
            self._logger.Trace("Local Setting: '%s'='%s'", setting_id, setting_value)
        else:
            channel_settings = LocalSettings.__settings["channels"].get(channel.id, {})
            setting_value = channel_settings.get(setting_id, default)
            self._logger.Trace("Local Channel Setting: '%s.%s'='%s'",
                               channel.id, setting_id, setting_value)

        return setting_value or default

    def clear_settings(self):
        LocalSettings.__settings = None

    def get_localized_string(self, string_id):
        raise NotImplementedError("No localization for Local Settings")

    def __del__(self):
        del LocalSettings.__settings
        LocalSettings.__settings = None
        self._logger.Debug("Removed Local settings-store")

    def __load_settings(self):
        if not os.path.isfile(self.local_settings_file):
            LocalSettings.__settings = self.__empty_settings()
            self._logger.Warning("No local settings file found: %s", self.local_settings_file)
            return

        try:
            with io.open(self.local_settings_file, mode="rb") as fp:
                content = fp.read()
                if not content:
                    LocalSettings.__settings = self.__empty_settings()
                    self._logger.Warning("Empty local settings file found: %s", self.local_settings_file)
                    return

                self._logger.Debug("Loading settings: %s", content)
                LocalSettings.__settings = json.loads(content, encoding='utf-8')
        except:
            self._logger.Error("Error loading JSON settings. Resetting all settings.", exc_info=True)
            LocalSettings.__settings = self.__empty_settings()
            shutil.copy(
                self.local_settings_file,
                self.local_settings_file.replace(".json", ".error.json")
            )
            self.__store_settings()
            return

    def __store_settings(self):
        if LocalSettings.__settings is None or not LocalSettings.__settings.keys():
            raise ValueError("Empty settings object cannot save.")

        # open the file as binary file, as json.dumps will already encode as utf-8 bytes
        with io.open(self.local_settings_file, mode='w+b') as fp:
            content = json.dumps(LocalSettings.__settings, indent=4, encoding='utf-8')
            self._logger.Debug("Storing settings: %s", content)
            fp.write(content)

    def __empty_settings(self):
        return {
            LocalSettings.__SETTINGS_KEY: {},
            LocalSettings.__CHANNELS_KEY: {}
        }
