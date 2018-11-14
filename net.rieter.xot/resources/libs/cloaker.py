#===============================================================================
# LICENSE Retrospect-Framework - CC BY-NC-ND
#===============================================================================
# This work is licenced under the Creative Commons
# Attribution-Non-Commercial-No Derivative Works 3.0 Unported License. To view a
# copy of this licence, visit http://creativecommons.org/licenses/by-nc-nd/3.0/
# or send a letter to Creative Commons, 171 Second Street, Suite 300,
# San Francisco, California 94105, USA.
#===============================================================================

from helpers.jsonhelper import JsonHelper


class Cloaker(object):
    CLOAKED_KEY = "cloaked"
    FIRST_TIME_SHOWN = "cloak_message_shown"

    def __init__(self, channel, settings_store, logger=None):
        """ Creates a Cloaker object that helps with cloaking objects

        :param channel:         The ChannelInfo of the channel for which we need cloak information.
        :param settings_store:   The settings store to use for retrieving the settings data.
        :param logger:          A Logger object for logging purposes.

        """

        self.__logger = logger
        self.__channel = channel
        self.__channelId = channel.guid
        self.__settingsStore = settings_store

        if self.__logger:
            self.__logger.Debug("Setting up a Cloaker based on '%s'", self.__settingsStore)

        # Create a new file if none existed
        self.__cloaked = self.__settingsStore.get_setting("cloaked", channel=channel, default=None)
        if self.__cloaked is None:
            self.__cloaked = {}
            self.__store(False)

        if self.__logger:
            self.__logger.Trace("Found cloaked data:\n%s", JsonHelper.dump(self.__cloaked, pretty_print=True))

    def cloak(self, url):
        """ Cloaks a specific URL from future listing.

        :param str url: the url to cloak.

        :return: boolean indicating whether this was the first cloak or not.
        :rtype: bool

        """

        if url in self.__cloaked:
            if self.__logger:
                self.__logger.Debug("'%s' in channel '%s' was already cloaked.", url, self.__channelId)
            return False

        if self.__logger:
            self.__logger.Debug("Cloaking '%s' in channel '%s'", url, self.__channelId)

        self.__cloaked[url] = {}
        return self.__store()

    def un_cloak(self, url):
        """ Uncloak an URL and make sure it is not cloaked anymore.

        :param str url: the URL to uncloak.

        """

        if url not in self.__cloaked:
            if self.__logger:
                self.__logger.Debug("'%s' in channel '%s' was not cloaked.", url, self.__channelId)
            return

        if self.__logger:
            self.__logger.Debug("Un-cloaking '%s' in channel '%s'", url, self.__channelId)

        self.__cloaked.pop(url, None)
        self.__store()
        return

    def is_cloaked(self, url):
        """ Checks whether an URL is cloaked or not.

        :param str url: The URL to check for cloaking

        :return: A boolean value indicating whether the url is cloaked (True) or not (False)
        :rtype: bool
        """

        return url in self.__cloaked

    def __store(self, update_first_time_message=True):
        # type: (bool) -> bool
        """ Store the current cloak information to the profile folder.

        :param bool update_first_time_message:  indicator if we should consider this the first
                                                cloaking action.

        :return: boolean indicating whether this was the first cloaking action.
        :rtype: bool

        """

        first_time = not self.__settingsStore.get_boolean_setting(Cloaker.FIRST_TIME_SHOWN,
                                                                  default=False)

        # update the first time message setting unless we should not.
        if update_first_time_message:
            self.__settingsStore.set_setting(Cloaker.FIRST_TIME_SHOWN, update_first_time_message)

        self.__settingsStore.\
            set_setting(Cloaker.CLOAKED_KEY, self.__cloaked, channel=self.__channel)

        if first_time and self.__logger and update_first_time_message:
            self.__logger.Debug("First time cloak found.")
        return first_time

    def __del__(self):
        # just release the reference here
        self.__settingsStore = None
        self.__logger.Trace("Removing Cloaker object")
