# coding=utf-8  # NOSONAR
# SPDX-License-Identifier: GPL-3.0-or-later
import time
from random import randrange
from typing import Optional, Union, List, Tuple

import pytz
import datetime

from resources.lib import chn_class, mediatype, contenttype
from resources.lib.helpers.datehelper import DateHelper
from resources.lib.helpers.encodinghelper import EncodingHelper
from resources.lib.mediaitem import MediaItem, FolderItem
from resources.lib.addonsettings import AddonSettings, LOCAL
from resources.lib.helpers.jsonhelper import JsonHelper

from resources.lib.helpers.htmlentityhelper import HtmlEntityHelper
from resources.lib.helpers.languagehelper import LanguageHelper
from resources.lib.logger import Logger
from resources.lib.streams.mpd import Mpd
from resources.lib.webdialogue import WebDialogue
from resources.lib.xbmcwrapper import XbmcWrapper
from resources.lib.streams.m3u8 import M3u8
from resources.lib.urihandler import UriHandler
from resources.lib.helpers.subtitlehelper import SubtitleHelper


class Channel(chn_class.Channel):

    def __init__(self, channel_info):
        """ Initialisation of the class.

        All class variables should be instantiated here and this method should not
        be overridden by any derived classes.

        :param ChannelInfo channel_info: The channel info object to base this channel on.

        """

        chn_class.Channel.__init__(self, channel_info)

        # ============== Actual channel setup STARTS here and should be overwritten from derived classes ===============
        self.__max_page_size = 250
        self.__access_token = None

        if self.channelCode == "tv4segroup":
            self.noImage = "tv4image.png"
            self.httpHeaders = {"Content-Type": "application/json", "Client-Name": "tv4-web",
                                "Client-Version": "4.0.0"}
        else:
            raise Exception("Invalid channel code")

        self._add_data_parser(
            "https://client-gateway.tv4.a2d.tv/graphql?operationName=PageList&",
            name="Main TV4 pages", json=True, requires_logon=False,
            preprocessor=self.check_query_errors,
            parser=["data", "pageList", "content"],
            creator=self.create_api_typed_item)

        self.mainListUri = "#mainlist"
        self._add_data_parser(
            "#mainlist", name="Main TV4 page", json=True, preprocessor=self.list_main_content)

        # If logon is set to True, panels that are not available to the user, will not show.
        self._add_data_parser(
            "https://client-gateway.tv4.a2d.tv/graphql?operationName=Page&",
            name="Main TV4 pages", json=True, requires_logon=True,
            preprocessor=self.check_query_errors,
            parser=["data", "page", "content", "panels"],
            creator=self.create_api_typed_item)

        self._add_data_parser(
            "https://client-gateway.tv4.a2d.tv/graphql?operationName=MediaIndex&",
            name="Main show/movie list", json=True,
            preprocessor=self.fetch_mainlist_pages,
            parser=["data", "mediaIndex", "contentList", "items"],
            creator=self.create_api_typed_item)

        # Requires logon to list all seasons.
        self._add_data_parser(
            "https://client-gateway.tv4.a2d.tv/graphql?operationName=ContentDetailsPage&",
            name="Seasons for show", json=True, requires_logon=True,
            preprocessor=self.check_query_errors,
            parser=["data", "media", "allSeasonLinks"], creator=self.create_api_typed_item,
            postprocessor=self.check_for_seasons)

        self._add_data_parsers(
            ["https://client-gateway.tv4.a2d.tv/graphql?operationName=Panel&",
             "https://client-gateway.tv4.a2d.tv/graphql?operationName=LivePanel&"],
            name="Panel results", json=True, requires_logon=False,
            preprocessor=self.check_query_errors,
            parser=["data", "panel", "content", "items"],
            creator=self.create_api_typed_item)

        self._add_data_parser(
            "https://client-gateway.tv4.a2d.tv/graphql?operationName=SeasonEpisodes&",
            name="Episodes for a season", json=True, requires_logon=False,
            preprocessor=self.check_query_errors,
            parser=["data", "season", "episodes", "items"],
            creator=self.create_api_typed_item)

        self._add_data_parser("*", updater=self.update_video_item, requires_logon=True)

        # ===============================================================================================================
        # non standard items
        self.__timezone = pytz.timezone("Europe/Stockholm")
        self.__refresh_token_setting_id = "refresh_token"

        # ===============================================================================================================
        # Test cases:

        # ====================================== Actual channel setup STOPS here =======================================
        return

    def fetch_token(self) -> Optional[str]:
        wd = WebDialogue()
        token, cancelled = wd.input(
            LanguageHelper.SetRefreshToken, LanguageHelper.PasteRefreshToken, time_out=120)

        if not token or cancelled:
            return None

        split_data = token.split(".")
        if len(split_data) != 3:
            AddonSettings.set_channel_setting(
                self, self.__refresh_token_setting_id, "", store=LOCAL)
            XbmcWrapper.show_notification(
                LanguageHelper.InvalidRefreshToken, LanguageHelper.InvalidRefreshToken)
            # Retry
            return self.fetch_token()

        header, payload, signature = split_data
        payload_data = EncodingHelper.decode_base64(payload + '=' * (-len(payload) % 4))
        payload = JsonHelper(payload_data)
        expires_at = payload.get_value("exp")
        expire_date = DateHelper.get_date_from_posix(float(expires_at), tz=pytz.UTC)
        if expire_date < datetime.datetime.now(tz=pytz.UTC).astimezone(tz=pytz.UTC):
            Logger.info("Found expired TV4Play token (valid until: %s)", expire_date)
            AddonSettings.set_channel_setting(
                self, self.__refresh_token_setting_id, "", store=LOCAL)
            XbmcWrapper.show_notification(
                LanguageHelper.InvalidRefreshToken, LanguageHelper.ExpireRefreshToken)
            # Retry
            return self.fetch_token()

        # (Re)Store the valid token.
        Logger.info("Found existing valid TV4Play token (valid until: %s)", expire_date)
        AddonSettings.set_channel_setting(self, self.__refresh_token_setting_id, token, store=LOCAL)
        return token

    # No logon for now
    def log_on(self) -> bool:
        """ Makes sure that we are logged on. """

        if self.__access_token:
            return True

        # Fetch an existing token
        token: str = AddonSettings.get_channel_setting(
            self, self.__refresh_token_setting_id, store=LOCAL)
        if not token:
            token = self.fetch_token()

        if not token:
            return False

        url = "https://avod-auth-alb.a2d.tv/oauth/refresh"
        result = UriHandler.open(
            url, json={"refresh_token": token, "client_id": "tv4-web"}, no_cache=True)
        result = JsonHelper(result)
        self.__access_token = result.get_value("access_token", fallback=None)

        # Update headers for future calls
        self.httpHeaders.update({
            "Authorization": f"Bearer {self.__access_token}"
        })

        # Also update headers for the current parent item
        if self.parentItem:
            self.parentItem.HttpHeaders.update(self.httpHeaders)
        return bool(self.__access_token)

    def check_query_errors(self, data: str) -> Tuple[str, List[MediaItem]]:
        items = []
        count = 0
        max_retries = 5

        while "PERSISTED_QUERY_NOT_FOUND" in data and count < max_retries:
            Logger.warning("`PERSISTED_QUERY_NOT_FOUND` Error for TV4")
            headers = self.parentItem.HttpHeaders
            headers.update(self.httpHeaders)
            # Wait for remote cache to finish
            time.sleep(1)
            data = UriHandler.open(
                self.parentItem.url, additional_headers=self.parentItem.HttpHeaders, no_cache=True)
            count += 1

        if "PERSISTED_QUERY_NOT_FOUND" in data:
            Logger.error(f"`PERSISTED_QUERY_NOT_FOUND` Error for TV4 after {count} retries")

        return data, items

    def list_main_content(self, data: str) -> Tuple[str, List[MediaItem]]:
        items: List[MediaItem] = []

        def __create_item(lang_id: int, url: str, json: Optional[dict] = None):
            name = LanguageHelper.get_localized_string(lang_id)
            item = FolderItem(name, url, content_type=contenttype.VIDEOS)
            item.dontGroup = True
            item.postJson = json
            return item

        main_list_url = self.__get_api_url(
            "MediaIndex",
            "423ba183684c9ea464c94e200696c8f6ec190fe9837f542a672623fa87ef0f4e",
            {"input": {"letterFilters": list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
                       "limit": self.__max_page_size + randrange(25) * 0,
                       "offset": 0}
             }
        )
        items.append(__create_item(LanguageHelper.TvShows, main_list_url))

        recent_url, recent_data = self.__get_api_post_query("Panel", {"panelId": "1pDPvWRfhEg0wa5SvlP28N", "limit": 100, "offset": 0})
        items.append(__create_item(LanguageHelper.Recent, recent_url, recent_data))

        popular_url, popular_data = self.__get_api_post_query("Panel", {"panelId": "3QnNaigt4Szgkyz8yMU9oF", "limit": 100, "offset": 0})
        items.append(__create_item(LanguageHelper.Popular, popular_url, popular_data))

        latest_news_url, latest_news_data = self.__get_api_post_query("Panel", {"panelId": "5Rqb0w0SN16A6YHt5Mx8BU", "limit": 100, "offset": 0})
        items.append(__create_item(LanguageHelper.LatestNews, latest_news_url, latest_news_data))

        category_url, json_data = self.__get_api_post_query("PageList", {"pageListId": "categories"})
        items.append(__create_item(LanguageHelper.Categories, category_url, json_data))
        return data, items

    def fetch_mainlist_pages(self, data: str) -> Tuple[str, List[MediaItem]]:
        items = []
        data = JsonHelper(data)
        page_data = data
        count = 0

        while count < 25:
            count += 1
            next_offset = page_data.get_value("data", "mediaIndex", "contentList", "pageInfo",
                                              "nextPageOffset")
            if not next_offset or next_offset <= 0:
                break

            url = self.__get_api_url(
                "MediaIndex",
                "423ba183684c9ea464c94e200696c8f6ec190fe9837f542a672623fa87ef0f4e",
                {"input": {"letterFilters": list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
                           "limit": self.__max_page_size + randrange(25) * 0,
                           "offset": next_offset}
                 }
            )
            new_data = UriHandler.open(url, additional_headers=self.httpHeaders,
                                       force_cache_duration=60 * 60)
            if "PERSISTED_QUERY_NOT_FOUND" in new_data:
                Logger.warning("PERSISTED_QUERY_NOT_FOUND found")
                time.sleep(2)
                continue

            page_data = JsonHelper(new_data)
            data_items = page_data.get_value(*self.currentParser.Parser)
            list_items = data.get_value(*self.currentParser.Parser)
            list_items += data_items

        Logger.debug("Pre-Processing finished")
        return data, items

    def create_api_typed_item(self, result_set):
        """ Creates a new MediaItem based on the __typename attribute.

        This method creates a new MediaItem from the Regular Expression or Json
        results <result_set>. The method should be implemented by derived classes
        and are specific to the channel.

        :param list[str]|dict result_set: The result_set of the self.episodeItemRegex

        :return: A new MediaItem of type 'folder'.
        :rtype: MediaItem|None

        """

        api_type = result_set.get("__typename")
        if not api_type:
            Logger.warning(result_set)
            raise IndexError("`__typename` missing")

        if api_type == "Series":
            item = self.create_api_series(result_set)
        elif api_type == "MediaPanelSeriesItem":
            item = self.create_api_series(result_set["series"])
        elif api_type == "MediaIndexSeriesItem":
            item = self.create_api_typed_item(result_set["series"])

        elif api_type == "Clip":
            item = self.create_api_clip(result_set)
        elif api_type == "ClipsPanelItem":
            item = self.create_api_typed_item(result_set["clip"])

        elif api_type == "Episode":
            item = self.create_api_episode(result_set)

        elif api_type == "Movie":
            item = self.create_api_movie(result_set)
        elif api_type == "MediaPanelMovieItem":
            item = self.create_api_movie(result_set["movie"])
        elif api_type == "MediaIndexMovieItem":
            item = self.create_api_typed_item(result_set["movie"])

        elif api_type == "SeasonLink":
            item = self.create_api_season(result_set)
        elif api_type == "PageReference":
            item = self.create_api_page(result_set)
        elif api_type == "PageReferenceItem":
            item = self.create_api_page(result_set["pageReference"])
        elif api_type == "StaticPageItem":
            item = self.create_api_static_page(result_set)

        elif api_type == "SinglePanel":
            item = self.create_api_typed_item(result_set["link"])
        elif api_type == "SinglePanelMovieLink":
            item = self.create_api_movie(result_set["movie"])

        elif (api_type == "MediaPanel" or api_type == "ClipsPanel" or
              api_type == "PagePanel" or api_type == "SportEventPanel"):
            item = self.create_api_panel(result_set)
        elif api_type == "LivePanel":
            item = self.create_api_live_panel(result_set)
        elif api_type == "PagePanelPageItem":
            item = self.create_api_typed_item(result_set["page"])
        elif api_type == "SportEventPanelItem":
            item = self.create_api_sport_event(result_set["sportEvent"])
        elif api_type == "LivePanelEpisodeItem":
            item = self.create_api_typed_item(result_set["episode"])

        elif api_type == "ThemePanel":
            item = self.create_api_theme_panel(result_set)

        else:
            Logger.warning("Missing type: %s", api_type)
            return None

        return item

    def create_api_movie(self, result_set: dict) -> Optional[MediaItem]:
        video_id: str = result_set["id"]
        url = self.__get_video_url(video_id)
        title = result_set["title"]
        if not title:
            return None

        item = MediaItem(title, url, media_type=mediatype.MOVIE)
        item.isGeoLocked = True
        item = self.__update_base_typed_item(item, result_set)
        return item

    def create_api_clip(self, result_set: dict) -> Optional[MediaItem]:
        clip_id = result_set["id"]
        url = self.__get_video_url(clip_id)
        title = result_set["title"]
        if not title:
            return None

        item = MediaItem(title, url, media_type=mediatype.VIDEO)
        item = self.__update_base_typed_item(item, result_set)
        item.isPaid = not JsonHelper.get_from(
            result_set, "clipVideo", "access", "hasAccess", fallback=True)
        item.isLive = result_set.get("isLiveContent", False)

        duration = JsonHelper.get_from(result_set, "clipVideo", "duration", "seconds", fallback=0)
        if duration:
            item.set_info_label(MediaItem.LabelDuration, duration)
        return item

    def create_api_episode(self, result_set: dict) -> Optional[MediaItem]:
        video_id: str = result_set["id"]
        url = self.__get_video_url(video_id)
        title = result_set["title"]
        if not title:
            return None

        item = MediaItem(title, url, media_type=mediatype.MOVIE)
        item = self.__update_base_typed_item(item, result_set)
        item.isGeoLocked = True
        item.isPaid = not JsonHelper.get_from(
            result_set, "video", "access", "hasAccess", fallback=True)
        item.isLive = result_set.get("isLiveContent", False)
        item.description = result_set.get("synopsis", {}).get("medium", "")

        duration = JsonHelper.get_from(result_set, "video", "duration", "seconds", fallback=0)
        if duration:
            item.set_info_label(MediaItem.LabelDuration, duration)

        # Playable from
        self.__set_playback_window(item, result_set)
        return item

    def create_api_series(self, result_set: dict) -> Optional[MediaItem]:
        series_id = result_set["id"]
        url = self.__get_api_url(
            "ContentDetailsPage",
            # "fb3501e05a23d910fc9c636467df8578cb69d80abc0225062d8a86e77041225a", {
            "7011f75ca9455842d183eb73301019a84038af83a4126ab8719e5dfadb70eda6", {
                "mediaId": series_id, "panelsInput": {"offset": 0, "limit": 20}
            })
        title = result_set["title"]
        if not title:
            return None

        item = FolderItem(title, url, content_type=contenttype.EPISODES,
                          media_type=mediatype.TVSHOW)
        item = self.__update_base_typed_item(item, result_set)
        item.HttpHeaders.update({"feature_flag_enable_season_upsell_on_cdp": "true"})
        item.isPaid = result_set.get("upsell") is not None
        return item

    def create_api_season(self, result_set: dict) -> Optional[MediaItem]:
        title = result_set["title"]
        season_id = result_set["seasonId"]
        url, json_data = self.__get_api_post_query("SeasonEpisodes", {"seasonId": season_id, "input": {"limit": 100, "offset": 0}})
        item = FolderItem(title, url, content_type=contenttype.EPISODES, media_type=mediatype.FOLDER)
        item.postJson = json_data
        item.metaData["seasonId"] = result_set["seasonId"]
        return item

    def create_api_page(self, result_set: dict) -> Optional[MediaItem]:
        title = result_set["title"]
        page_id = result_set["id"]

        # Link goes to a page
        url = self.__get_api_url(
            "Page", "a30fb04a7dbabeaf3b08f66134c6ac1f1e4980de1f21024fa755d752608e6ad9",
            {"pageId": page_id, "input": {"limit": 100, "offset": 0}}
        )
        item = FolderItem(title, url, content_type=contenttype.TVSHOWS, media_type=mediatype.FOLDER)
        self.__set_art(item, result_set.get("images"))
        return item

    def create_api_panel(self, result_set: dict) -> Optional[MediaItem]:
        panel_id = result_set["id"]
        title = result_set["title"]
        url = self.__get_api_url(
            "Panel", "3ef650feea500555e560903fee7fc06f8276d046ea880c5540282a5341b65985", {
                "panelId": panel_id, "limit": self.__max_page_size, "offset": 0}
        )
        item = FolderItem(title, url, content_type=contenttype.VIDEOS)
        return item

    def create_api_live_panel(self, result_set: dict) -> Optional[MediaItem]:
        # perationName: LivePanel
        # variables: {"panelId":"2VegBPUzE507oWek5kXzh9","offset":6,"limit":6}
        # extensions: {"persistedQuery":{"version":1,"sha256Hash":"0dd12d54d8eba939d7c07778d23b1cca24abbdb1b4d62fd7728fc146231e0c08"}}
        panel_id = result_set["id"]
        title = result_set["title"]
        url = self.__get_api_url(
            "LivePanel", "0dd12d54d8eba939d7c07778d23b1cca24abbdb1b4d62fd7728fc146231e0c08", {
                "panelId": panel_id, "limit": 100, "offset": 0}
        )
        item = FolderItem(title, url, content_type=contenttype.VIDEOS)
        return item

    def create_api_sport_event(self, result_set: dict) -> Optional[MediaItem]:
        title = result_set["title"]
        event_id = result_set["id"]
        url = self.__get_video_url(event_id)

        item = MediaItem(title, url, media_type=mediatype.VIDEO)
        item.isLive = result_set.get("isLiveContent", False)
        item.description = result_set.get("league")
        self.__set_art(item, result_set["images"])
        self.__set_playback_window(item, result_set)
        return item

    def create_api_theme_panel(self, result_set: dict) -> Optional[MediaItem]:
        return None

    def create_api_static_page(self, result_set: dict) -> Optional[MediaItem]:
        result_set = result_set["staticPage"]
        page_id = result_set["id"]
        if page_id != "alphabetical":
            return None

        title = result_set["title"]
        url = self.__get_api_url(
            "MediaIndex",
            "dba092c9af0e54e4e3e68dd84b16bb913a9e0e5fe83ff01cf59b6b453d0c75d4",
            {"input": {"letterFilters": list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
                       "limit": self.__max_page_size + randrange(25) * 0,
                       "offset": 0}})
        item = FolderItem(title, url, content_type=contenttype.VIDEOS, media_type=mediatype.FOLDER)
        self.__set_art(item, result_set.get("images"))
        return item

    # noinspection PyUnusedLocal
    def check_for_seasons(self, data: JsonHelper, items: List[MediaItem]) -> List[MediaItem]:
        # If not seasons, or just one, fetch the episodes
        if len(items) != 1:
            return items

        # Retry with just this url.
        season_id = items[0].metaData["seasonId"]
        url, data = self.__get_api_post_query(
            "SeasonEpisodes",
            {"seasonId": season_id, "input": {"limit": 100, "offset": 0}}
        )
        self.parentItem.url = url
        self.parentItem.postJson = data
        return self.process_folder_list(self.parentItem)

    def search_site(self, url=None):
        """ Creates a list of items by searching the site.

        This method is called when the URL of an item is "searchSite". The channel
        calling this should implement the search functionality. This could also include
        showing of an input keyboard and following actions.

        The %s the url will be replaced with an URL encoded representation of the
        text to search for.

        :param str|None url:     Url to use to search with a %s for the search parameters.

        :return: A list with search results as MediaItems.
        :rtype: list[MediaItem]

        """

        # url = self.__get_api_query(
        #     '{programSearch(q:"",perPage:100){totalHits,programs%s}}' % self.__program_fields)
        # url = url.replace("%", "%%")
        # url = url.replace("%%22%%22", "%%22%s%%22")
        return chn_class.Channel.search_site(self, url)

    def update_video_item(self, item):
        """ Updates an existing MediaItem with more data.

        Used to update none complete MediaItems (self.complete = False). This
        could include opening the item's URL to fetch more data and then process that
        data or retrieve it's real media-URL.

        The method should at least:
        * cache the thumbnail to disk (use self.noImage if no thumb is available).
        * set at least one MediaStream.
        * set self.complete = True.

        if the returned item does not have a MediaSteam then the self.complete flag
        will automatically be set back to False.

        :param MediaItem item: the original MediaItem that needs updating.

        :return: The original item with more data added to it's properties.
        :rtype: MediaItem

        """

        Logger.debug('Starting update_video_item for %s (%s)', item.name, self.channelName)

        # noinspection PyStatementEffect
        """
                C:\temp\rtmpdump-2.3>rtmpdump.exe -z -o test.flv -n "cp70051.edgefcs.net" -a "tv
                4ondemand" -y "mp4:/mp4root/2010-06-02/pid2780626_1019976_T3MP48_.mp4?token=c3Rh
                cnRfdGltZT0yMDEwMDcyNjE2NDYyNiZlbmRfdGltZT0yMDEwMDcyNjE2NDgyNiZkaWdlc3Q9ZjFjN2U1
                NTRiY2U5ODMxMDMwYWQxZWEwNzNhZmUxNjI=" -l 2

                C:\temp\rtmpdump-2.3>rtmpdump.exe -z -o test.flv -r rtmpe://cp70051.edgefcs.net/
                tv4ondemand/mp4root/2010-06-02/pid2780626_1019976_T3MP48_.mp4?token=c3RhcnRfdGlt
                ZT0yMDEwMDcyNjE2NDYyNiZlbmRfdGltZT0yMDEwMDcyNjE2NDgyNiZkaWdlc3Q9ZjFjN2U1NTRiY2U5
                ODMxMDMwYWQxZWEwNzNhZmUxNjI=
                """

        # retrieve the mediaurl
        # needs an "x-jwt: Bearer"  header.
        token = self.__access_token
        headers = {
            "x-jwt": "Bearer {}".format(token)
        }
        data = UriHandler.open(item.url, additional_headers=headers)
        stream_info = JsonHelper(data)
        stream_url = stream_info.get_value("playbackItem", "manifestUrl")
        if stream_url is None:
            return item

        if ".mpd" in stream_url:
            return self.__update_dash_video(item, stream_info)

        subtitle = M3u8.get_subtitle(stream_url)
        stream = item.add_stream(stream_url, 0)
        M3u8.set_input_stream_addon_input(stream)
        item.complete = True

        if subtitle:
            subtitle = subtitle.replace(".m3u8", ".webvtt")
            item.subtitle = SubtitleHelper.download_subtitle(subtitle, format="m3u8srt")
        return item

    def update_live_item(self, item):
        """ Updates an existing MediaItem for a live stream with more data.

        Used to update none complete MediaItems (self.complete = False). This
        could include opening the item's URL to fetch more data and then process that
        data or retrieve it's real media-URL.

        The method should at least:
        * cache the thumbnail to disk (use self.noImage if no thumb is available).
        * set at least one MediaStream.
        * set self.complete = True.

        if the returned item does not have a MediaSteam then the self.complete flag
        will automatically be set back to False.

        :param MediaItem item: the original MediaItem that needs updating.

        :return: The original item with more data added to it's properties.
        :rtype: MediaItem

        """

        Logger.debug('Starting update_live_item for %s (%s)', item.name, self.channelName)

        item.streams = []
        for s, b in M3u8.get_streams_from_m3u8(item.url):
            item.add_stream(s, b)

        item.complete = True
        return item

    def __update_base_typed_item(
            self, item: Union[MediaItem, FolderItem], result_set: dict) -> Union[
        MediaItem, FolderItem]:

        self.__set_art(item, result_set.get("images"))
        return item

    def __get_video_url(self, program_id: str):
        # https://playback2.a2d.tv/play/8d1eb26ad728c9125de8?service=tv4play&device=browser&protocol=hls%2Cdash&drm=widevine&browser=GoogleChrome&capabilities=live-drm-adstitch-2%2Cyospace3
        url = "https://playback2.a2d.tv/play/{}?service=tv4play" \
              "&device=browser&browser=GoogleChrome" \
              "&protocol=hls%2Cdash" \
              "&drm=widevine" \
              "&capabilities=live-drm-adstitch-2%2Cexpired_assets". \
            format(program_id)
        return url

    def __set_art(self, item: MediaItem, art_info: Optional[dict]):
        if not art_info:
            return

        for k, v in art_info.items():
            if isinstance(v, str) or not v:
                continue

            encoded_url = v.get("sourceEncoded")
            if not encoded_url:
                continue

            url = HtmlEntityHelper.url_decode(encoded_url)
            if k == "cover2x3" or k == "image2x3":
                item.set_artwork(poster=url)
            elif k == "main16x9Annotated":
                item.set_artwork(thumb=url, fanart=url)
            elif k == "main16x9" or k == "image16x9":
                # Only thumbs should be set (not fanart)
                item.set_artwork(thumb=url)
            elif k == "image4x3":
                item.set_artwork(thumb=url)
            elif k == "logo":
                pass
            else:
                Logger.warning("Unknown image format: %s", k)

    def __get_api_url(self, operation, hash_value, variables=None):  # NOSONAR
        """ Generates a GraphQL url

        :param str operation:   The operation to use
        :param str hash_value:  The hash of the Query
        :param dict variables:  Any variables to pass

        :return: A GraphQL string
        :rtype: str

        """

        extensions = {"persistedQuery": {"version": 1, "sha256Hash": hash_value}}
        extensions = HtmlEntityHelper.url_encode(JsonHelper.dump(extensions, pretty_print=False))

        final_vars = {"order_by": "NAME", "per_page": 1000}
        if variables:
            final_vars = variables
        final_vars = HtmlEntityHelper.url_encode(JsonHelper.dump(final_vars, pretty_print=False))

        url = "https://client-gateway.tv4.a2d.tv/graphql?" \
              "operationName={}&" \
              "variables={}&" \
              "extensions={}".format(operation, final_vars, extensions)
        return url

    def __get_api_query(self, query):
        return "https://graphql.tv4play.se/graphql?query={}".format(
            HtmlEntityHelper.url_encode(query))

    def __get_api_post_query(self, operation: str, variables: dict) -> Tuple[str, dict]:
        base_url = f"https://client-gateway.tv4.a2d.tv/graphql?operationName={operation}&"
        query = None

        # 1:1 Generated from javascript source
        fragments = {
            "CdpPanelsFields": "fragment CdpPanelsFields on CdpPanels { items { ... on ClipsPanel { id title } } }",
            "ChannelFields": "fragment ChannelFields on Channel { __typename id title description tagline slug type images { logo { ...ImageFieldsLight } main16x9 { ...ImageFieldsLight } } access { hasAccess } epg { end start title } } ",
            "ChannelPanels": "fragment ChannelPanels on ChannelPanel { __typename id title content(input: {limit: 100, offset: 0}) { items { channel { __typename id title images { logo { ...ImageFieldsLight } } } } } }",
            "ChannelVideoFields": "fragment ChannelVideoFields on Channel { title id slug isDrmProtected access { hasAccess } epg { end start title type images { main16x9 { isFallback id source sourceEncoded } } } images { logo { ...ImageFieldsLight } main16x9 { ...ImageFieldsLight source } } }",
            "ClipFieldsLight": "fragment ClipFieldsLight on Clip { id slug title clipVideo: video { ...VideoFields } images { main16x9 { ...ImageFieldsLight } } playableFrom { readableDistance } mediaClassification parent { ... on ClipParentSeriesLink { id title } ... on ClipParentMovieLink { id title } } }",
            "ClipVideoFields": "fragment ClipVideoFields on Clip { id slug title images { main16x9 { ...ImageFieldsLight source } } playableFrom { humanDateTime readableDistance isoString } mediaClassification parent { ... on ClipParentSeriesLink { id title } ... on ClipParentMovieLink { id title } } clipVideo: video { ...VideoFields } isPollFeatureEnabled description playableUntil { isoString humanDateTime } }",
            "ContestantFields": "fragment ContestantFields on Contestant {  name shortName avatar { avatar1x1 { ...ImageFieldsLight } } confirmation { headline image { action1x1 { ...ImageFieldsLight } } text } thankYou { headline image { action1x1 { ...ImageFieldsLight } } text } }",
            "ContinueWatchingFields": "fragment ContinueWatchingFields on ContinueWatchingItem { continueWatchingEntryId labelText media { ... on ContinueWatchingEpisodeItem { episode { ...EpisodeFields progress { percent timeLeft } series { id images { main16x9Annotated { ...ImageFieldsLight } } } } } ... on ContinueWatchingMovieItem { movie { ...MovieFieldsLight progress { percent timeLeft } video { ...VideoFields } } } } }",
            "EliminationPollFields": "fragment EliminationPollFields on EliminationPoll { id accentColor backgroundColor eliminatedContestants { ...ContestantFields } liveTriggerTimestamps options { contestant { ...ContestantFields } id } rules { maxVotesPerOption } status eliminationSubtitle: subtitle title vodTriggerTimes }",
            "EpisodeFields": "fragment EpisodeFields on Episode { id slug title isLiveContent isStartOverEnabled series { id title slug } images { main16x9 { ...ImageFieldsLight } } liveEventEnd { isoString } playableUntil { readableDistance(type: DAYS_LEFT) humanDateTime } playableFrom { humanDateTime isoString } video { ...VideoFields } upsell { tierId } }",
            "EpisodeFieldsFull": "fragment EpisodeFieldsFull on Episode { ...EpisodeFields synopsis { medium } parentalRating { ...ParentalRatingFields } upsell { tierId } }",
            "EpisodeVideoFields": "fragment EpisodeVideoFields on Episode { id slug title extendedTitle isPollFeatureEnabled isLiveContent liveEventEnd { isoString } series { title id slug } synopsis { medium } images { main16x9 { ...ImageFieldsLight source } } parentalRating { ...ParentalRatingFields } video { ...VideoFields } playableFrom { isoString humanDateTime } playableUntil { isoString humanDateTime } }",
            "ImageFieldsFull": "fragment ImageFieldsFull on Image { sourceEncoded meta { muteBgColor { hex } } }",
            "ImageFieldsLight": "fragment ImageFieldsLight on Image { sourceEncoded isFallback }",
            "LabelFields": "fragment LabelFields on Label { id airtime announcement recurringBroadcast }",
            "ListSearchCountFields": "fragment ListSearchCountFields on ListSearchCount { clips movies pages series sportEvents }",
            "MediaPanels": "fragment MediaPanels on MediaPanel { id title content(input: {limit: 4, offset: 0}) { items { ... on MediaPanelSeriesItem { series { id title images { cover2x3 { ...ImageFieldsLight } } } } ... on MediaPanelMovieItem { movie { id title images { cover2x3 { ...ImageFieldsLight } } } } } } }",
            "MovieCreditsFields": "fragment MovieCreditsFields on MovieCredits { actors { characterName name type } directors { name type } }",
            "MovieFieldsLight": "fragment MovieFieldsLight on Movie { id slug title genres productionYear isLiveContent productionCountries { name } parentalRating { ...ParentalRatingFields } liveEventEnd { isoString } label { ...LabelFields } images { cover2x3 { ...ImageFieldsFull } main16x9Annotated { ...ImageFieldsLight } } playableUntil { readableDistance(type: DAYS_LEFT) } playableFrom { isoString humanDateTime readableDate } trailers { mp4 webm } upsell { tierId } }",
            "MovieVideoFields": "fragment MovieVideoFields on Movie { id slug title isPollFeatureEnabled liveEventEnd { isoString } isLiveContent synopsis { medium } images { main16x9 { ...ImageFieldsLight source } brandLogo { ...ImageFieldsLight } } parentalRating { ...ParentalRatingFields } video { ...VideoFields } playableFrom { isoString humanDateTime } playableUntil { isoString humanDateTime } }",
            "PageInfoFields": "fragment PageInfoFields on PageInfo { hasNextPage nextPageOffset totalCount }",
            "PageListFields": "fragment PageListFields on PageReference { id title images { image16x9 { ...ImageFieldsFull } image4x3 { ...ImageFieldsFull } logo { ...ImageFieldsLight isFallback } } }",
            "PagePanels": "fragment PagePanels on PagePanel { id title content(input: {limit: 10, offset: 0}) { items { ... on PagePanelPageItem { page { id title images { logo { ...ImageFieldsLight } } } } } } }",
            "ParentalRatingFields": "fragment ParentalRatingFields on ParentalRating { finland { ageRestriction reason } sweden { ageRecommendation suitableForChildren } }",
            "ProgressFields": "fragment ProgressFields on Progress { percent position timeLeft }",
            "Recommendations": "fragment Recommendations on MediaRecommendationsResult { pageInfo { ...PageInfoFields } items { ... on RecommendedMovie { movie { ...MovieFieldsLight } } ... on RecommendedSeries { series { ...SeriesFieldsLight } } ... on RecommendedClip { clip { ...ClipFieldsLight } } } }",
            "SeriesCreditsFields": "fragment SeriesCreditsFields on SeriesCredits { directors { name type } hosts { name type } actors { characterName name type } }",
            "SeriesFieldsLight": "fragment SeriesFieldsLight on Series { id slug title genres mediaClassification numberOfAvailableSeasons label { ...LabelFields } images { cover2x3 { ...ImageFieldsFull } main16x9Annotated { ...ImageFieldsLight } } parentalRating { ...ParentalRatingFields } trailers { mp4 webm } upsell { tierId } }",
            "SinglePanelFields": "fragment SinglePanelFields on SinglePanel { id secondaryLinkText images { image16x9 { ...ImageFieldsFull } image2x3 { ...ImageFieldsFull } brandLogo { ...ImageFieldsLight } } link { ... on SinglePanelPageLink { page { id } } ... on SinglePanelSportEventLink { sportEvent { ...SportEventFieldsLight } } ... on SinglePanelSeriesLink { series { ...SeriesFieldsLight } } ... on SinglePanelMovieLink { movie { ...MovieFieldsLight } } ... on SinglePanelEpisodeLink { episode { ...EpisodeFields } } ... on SinglePanelClipLink { clip { ...ClipFieldsLight } } ... on SinglePanelChannelLink { channel { ...ChannelFields } } } trailers { ...TrailerFields } linkText title pitch shortPitch }",
            "SportEventFieldsLight": "fragment SportEventFieldsLight on SportEvent { title slug id league round images { main16x9 { ...ImageFieldsLight } brandLogo { ...ImageFieldsLight } } playableFrom { humanDateTime isoString } liveEventEnd { isoString } isLiveContent upsell { tierId } } ",
            "SportEventVideoFields": "fragment SportEventVideoFields on SportEvent { title id slug isLiveContent isDrmProtected access { hasAccess } synopsis { medium } images { logo { ...ImageFieldsLight } main16x9 { ...ImageFieldsLight source } brandLogo { ...ImageFieldsLight } } playableUntil { isoString humanDateTime } playableFrom { humanDateTime isoString readableDistance } liveEventEnd { isoString } }",
            "SurveyPollFields": "fragment SurveyPollFields on SurveyPoll { buttonText color endTime id image { main4x3 { ...ImageFieldsFull } } inactiveSubtitle inactiveTitle liveTriggerTimestamps options { id image { option1x1 { ...ImageFieldsLight } } text } publishing { metadataIds videoAssetIds } resultConfiguration { isResultPublic isResultStatic } resultSubtitle resultTitle status subtitle title vodTriggerTimes }",
            "ThemePanelFields": "fragment ThemePanelFields on ThemePanel { id title pitch hexColor images { image16x9 { ...ImageFieldsFull } } link { ... on ThemePanelSeriesLink { series { id slug genres numberOfAvailableSeasons parentalRating { ...ParentalRatingFields } images { brandLogo { ...ImageFieldsLight } } upsell { tierId } } } ... on ThemePanelMovieLink { movie { id slug genres productionCountries { countryCode name } productionYear parentalRating { ...ParentalRatingFields } images { brandLogo { ...ImageFieldsLight } } upsell { tierId } } } ... on ThemePanelEpisodeLink { episode { id slug upsell { tierId } } } ... on ThemePanelClipLink { clip { id slug } } ... on ThemePanelPageLink { page { id } } ... on ThemePanelUrlsLink { webUrl } ... on ThemePanelSportEventLink { sportEvent { id slug arena league round playableFrom { humanDateTime isoString readableDate } images { brandLogo { ...ImageFieldsLight } } upsell { tierId } } } } themePanelLinkText: linkText showMetadataForLink subtitle trailers { ...TrailerFields } showUpsellLabel }",
            "TierPanels": "fragment TierPanels on TiersPanel { id title }",
            "TrailerFields": "fragment TrailerFields on Trailers { mp4 webm }",
            "UpcomingEpisodeFields": "fragment UpcomingEpisodeFields on UpcomingEpisode { id title seasonTitle playableFrom { humanDateTime isoString readableDateShort } image { main16x9 { ...ImageFieldsLight } } upsell { tierId } } ",
            "VideoFields": "fragment VideoFields on Video { id duration { readableShort readableMinutes seconds } isLiveContent access { hasAccess } isDrmProtected }",
        }

        if operation == "SeasonEpisodes":
            query = """
                query SeasonEpisodes($input: SeasonEpisodesInput!, $seasonId: ID!) { 
                    season(id: $seasonId) { id numberOfEpisodes episodes(input: $input) { initialSortOrder pageInfo { ...PageInfoFields } items { __typename ...EpisodeFieldsFull } } } } 
                %(PageInfoFields)s %(EpisodeFieldsFull)s %(EpisodeFields)s %(ParentalRatingFields)s %(ImageFieldsLight)s %(VideoFields)s
            """ % fragments
        elif operation == "PageList":
            query = """
                query PageList($pageListId: ID!) { pageList(id: $pageListId) { id content { ... 
                    on PageReferenceItem { pageReference { ...PageListFields __typename } __typename } ... 
                    on StaticPageItem { staticPage { id title type images { image4x3 { ...ImageFieldsFull __typename } __typename } __typename } __typename } __typename } __typename }} 
                %(PageListFields)s %(ImageFieldsFull)s %(ImageFieldsLight)s
            """ % fragments
        elif operation == "Panel":
            query = """
                query Panel($panelId: ID!, $offset: Int!, $limit: Int!) { panel(id: $panelId) { ... on ContinueWatchingPanel { id } ... on PagePanel 
                { id content(input: {offset: $offset, limit: $limit}) { pageInfo { ...PageInfoFields } items { ... 
                    on PagePanelPageItem { page { ...PageListFields } } } } } ... 
                    on SportEventPanel { id content(input: {offset: $offset, limit: $limit}) { pageInfo { ...PageInfoFields } items { sportEvent { ...SportEventFieldsLight } } } } ... 
                    on ClipsPanel { id title content(input: {offset: $offset, limit: $limit}) { pageInfo { ...PageInfoFields } items { __typename clip { __typename ...ClipFieldsLight } } } } ... 
                    on EpisodesPanel { id title content(input: {offset: $offset, limit: $limit}) { pageInfo { ...PageInfoFields } items { episode { ...EpisodeFields } labelText } } } ... 
                    on MediaPanel { __typename id slug title content(input: {offset: $offset, limit: $limit}) { __typename pageInfo { ...PageInfoFields } items { ... 
                        on MediaPanelMovieItem { __typename movie { ...MovieFieldsLight __typename } } ... 
                        on MediaPanelSeriesItem { __typename series { ...SeriesFieldsLight __typename} } } } } ... 
                        on ChannelPanel { id title type content(input: {offset: $offset, limit: $limit}) { pageInfo { ...PageInfoFields } items { channel { ...ChannelFields } } } } } }
                %(ImageFieldsFull)s, %(ParentalRatingFields)s %(ImageFieldsLight)s %(VideoFields)s %(EpisodeFields)s 
                %(LabelFields)s %(MovieFieldsLight)s %(SportEventFieldsLight)s %(SeriesFieldsLight)s %(ClipFieldsLight)s 
                %(ChannelFields)s %(PageListFields)s %(PageInfoFields)s
            """ % fragments

        if not query:
            raise IndexError

        data = {
            "operationName": operation,
            "query": query,
            "variables": variables
        }
        return base_url, data

    def __update_dash_video(self, item, stream_info):
        """

        :param MediaItem item:          The item that was updated
        :param JsonHelper stream_info:  The stream info
        """

        if not AddonSettings.use_adaptive_stream_add_on(with_encryption=True):
            XbmcWrapper.show_dialog(
                LanguageHelper.get_localized_string(LanguageHelper.DrmTitle),
                LanguageHelper.get_localized_string(LanguageHelper.WidevineLeiaRequired))
            return item

        playback_item = stream_info.get_value("playbackItem")

        stream_url = playback_item["manifestUrl"]
        stream = item.add_stream(stream_url, 0)

        license_info = playback_item.get("license", None)
        if license_info is not None:
            license_key_token = license_info["token"]
            auth_token = license_info["castlabsToken"]
            header = {
                "x-dt-auth-token": auth_token,
                "content-type": "application/octstream"
            }
            license_url = license_info["castlabsServer"]
            license_key = Mpd.get_license_key(
                license_url, key_value=license_key_token, key_headers=header)

            Mpd.set_input_stream_addon_input(
                stream, license_key=license_key)
            item.isDrmProtected = False
        else:
            Mpd.set_input_stream_addon_input(stream)

        item.complete = True
        return item

    def __set_playback_window(self, item: MediaItem, result_set: dict):
        if "playableFrom" in result_set:
            from_date = result_set["playableFrom"]["isoString"]
            # isoString=2022-07-27T22:01:00.000Z
            time_stamp = DateHelper.get_date_from_string(from_date, "%Y-%m-%dT%H:%M:%S.%fZ")
            item.set_date(*time_stamp[0:6])

            # Playable to
        if "playableUntil" in result_set and result_set["playableUntil"]:
            until_data = result_set["playableUntil"]["humanDateTime"]
            expires = "[COLOR gold]{}: {}[/COLOR]".format(MediaItem.ExpiresAt, until_data)
            item.description = f"{expires}\n\n{item.description}"

        elif "liveEventEnd" in result_set and result_set["liveEventEnd"]:
            until_data = result_set["liveEventEnd"]["isoString"]
            time_stamp = DateHelper.get_date_from_string(until_data, "%Y-%m-%dT%H:%M:%S.%fZ")
            time_value = time.strftime('%Y-%m-%d %H:%M:%S', time_stamp)
            expires = "[COLOR gold]{}: {}[/COLOR]".format(MediaItem.ExpiresAt, time_value)
            item.description = f"{expires}\n\n{item.description}"
