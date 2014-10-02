#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Frappe fill - Fill database

Usage:
  fill (items|users) <path> [options]
  fill (items|users) --webservice=<url> [options]
  fill items --mozilla (dev | prod) [today | yesterday | <date>] [--verbose]
  fill (items|users) --mozilla <path> [--verbose]
  fill --help
  fill --version

Options:
  -i --item=<field>                Item identifier in file [default: external_id].
  -u --user=<field>                User identifier in file [default: external_id].
  --item-file-identifier=<field>   Field that identify item json file [default: item].
  --user-file-identifier=<field>   File that identify user json file [default: user].
  --item-genres=<field>            Field in items for genres [default: genres].
  --item-locales=<field>           Field in items for locales [default: locales].
  --user-items=<field>             Field in user for user items [default: items].
  --user-item-identifier=<field>   Field to identify item in user inventory [default: external_id].
  --user-item-acquired=<field>     Field to identify item acquisition date [default: acquired].
  --user-item-dropped=<field>      Field to identify item acquisition date [default: dropped].
  --date-format=<field>            Field to date format [default: %Y-%m-%dT%H:%M:%S]
  -v --verbose                     Set verbose mode.
  -h --help                        Show this screen.
  --version                        Show version.
"""
__author__ = "joaonrb"

import os
import sys
import traceback
import logging
import tempfile
import urllib
import tarfile
import json
import errno
import shutil
from datetime import date, timedelta, datetime
from django.db import connection
from django.db.models import Q
from django_docopt_command import DocOptCommand
from django.conf import settings
from recommendation.models import Item, User, Inventory
from recommendation.diversity.models import Genre, ItemGenre
from recommendation.language.models import Locale, ItemLocale
from recommendation.default_settings import TESTING_MODE

MOZILLA_DEV_ITEMS_API = "https://marketplace-dev-cdn.allizom.org/dumped-apps/tarballs/%Y-%m-%d.tgz"
MOZILLA_PROD_ITEMS_API = "https://marketplace.cdn.mozilla.net/dumped-apps/tarballs/%Y-%m-%d.tgz"

MOZILLA_ITEM_FILE_IDENTIFIER = "app_type"
MOZILLA_ITEM_FIELD = "id"
MOZILLA_ITEM_LOCALES_FIELD = "supported_locales"
MOZILLA_ITEM_GENRES_FIELD = "categories"
MOZILLA_USER_FILE_IDENTIFIER = "user"
MOZILLA_USER_FIELD = "user"
MOZILLA_USER_ITEMS = "installed_apps"
MOZILLA_USER_ITEM_IDENTIFIER = "id"
MOZILLA_USER_ITEM_ACQUISITION_FIELD = "installed"
MOZILLA_USER_ITEM_DROPPED_FIELD = "dropped"
MOZILLA_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

SQLITE_MAX_ROWS = 950


class FillTool(object):

    TMP_FILE = "tmp.tgz"

    def __init__(self, parameters):
        self.parameters = parameters
        self.is_item = parameters.get("items", False)
        self.is_user = parameters.get("users", False)
        self.use_tmp = True
        self.path = self.tmp_dir = None
        if parameters.get("--version", False):
            print("Frappe fill 2.0")
            return
        if parameters.get("--webservice", False):
            self.tmp_dir = self.path = self.get_files(parameters["--webservice"])
        elif parameters.get("--mozilla", False):
            if parameters.get("dev", False) or parameters.get("prod", False):
                mozilla = MOZILLA_DEV_ITEMS_API if parameters.get("dev", False) else MOZILLA_PROD_ITEMS_API
                url = datetime.strftime(self.get_date(), mozilla)
                self.tmp_dir = self.path = self.get_files(url)
            else:
                self.path = parameters["<path>"]
                self.use_tmp = False
            parameters["--item-file-identifier"] = MOZILLA_ITEM_FILE_IDENTIFIER
            parameters["--item"] = MOZILLA_ITEM_FIELD
            parameters["--item-locales"] = MOZILLA_ITEM_LOCALES_FIELD
            parameters["--item-genres"] = MOZILLA_ITEM_GENRES_FIELD
            parameters["--user-file-identifier"] = MOZILLA_USER_FILE_IDENTIFIER
            parameters["--user"] = MOZILLA_USER_FIELD
            parameters["--user-items"] = MOZILLA_USER_ITEMS
            parameters["--user-item-identifier"] = MOZILLA_USER_ITEM_IDENTIFIER
            parameters["--user-item-acquired"] = MOZILLA_USER_ITEM_ACQUISITION_FIELD
            parameters["--user-item-dropped"] = MOZILLA_USER_ITEM_DROPPED_FIELD
            parameters["--date-format"] = MOZILLA_DATE_FORMAT
        elif parameters.get("<path>", False):
            self.path = parameters["<path>"]
            self.use_tmp = False
        self.objects = []
        self.item_file_identifier_field = parameters["--item-file-identifier"]
        self.item_field = parameters["--item"]
        self.item_locales_field = parameters["--item-locales"]
        self.item_genres_field = parameters["--item-genres"]
        self.user_file_identifier_field = parameters["--user-file-identifier"]
        self.user_field = parameters["--user"]
        self.user_items_field = parameters["--user-items"]
        self.user_item_identifier_field = parameters["--user-item-identifier"]
        self.user_item_acquisition_field = parameters["--user-item-acquired"]
        self.user_item_dropped_field = parameters["--user-item-dropped"]
        self.date_format = parameters["--date-format"]
        if parameters.get("--verbose", False):
            logging.basicConfig(format="%(asctime)s %(levelname)s: %(message)s",
                                datefmt="%m-%d-%Y %H:%M:%S",
                                level=logging.DEBUG)

    def get_files(self, url):
        tmp_path = tempfile.mkdtemp(suffix="_frappe")
        tmp_file = "/".join([tmp_path, self.TMP_FILE])
        urllib.urlretrieve(url, tmp_file)
        tar = tarfile.open(tmp_file)
        tar.extractall(members=FillTool.json_files(tar), path=tmp_path)
        tar.close()
        return tmp_path

    @staticmethod
    def json_files(members):
        """
        Pull .json files only
        """
        for tarinfo in members:
            name = os.path.splitext(tarinfo.name)
            if name[1] == ".json" and name[0][0] != ".":
                yield tarinfo

    def get_date(self):
        """
        Return a date passed in the pararameters
        :return: A datetime
        """
        if self.parameters.get("<date>", False):
            return datetime.strptime(self.parameters["<date>"], "%Y-%m.%d").date()
        if self.parameters.get("today", False):
            return date.today()
        # Return yesterday
        return date.today() - timedelta(1)

    def load(self):
        """
        Load the files to db
        """
        if self.path:
            try:
                self.load_files()
                logging.debug("Load files into memory")
                self.fill_db()
                logging.debug("Files loaded to database")
            finally:
                if self.use_tmp:
                    self.clean_tmp()
                    logging.debug("Tmp files deleted")
                logging.debug("Done!")

    def load_files(self):
        """
        Load files to memory
        """
        for path, _, files in os.walk(self.path):
            for name in files:
                if name[-5:].lower() == '.json':
                    f = '/'.join([path, name])
                    self.objects.append(json.load(open(f)))

    def fill_db(self):
        """
        Put objects in db
        """
        if self.is_item:
            self.fill_db_with_items()
        else:
            self.fill_db_with_users()

    def fill_db_with_items(self):
        """
        Put items in db
        """
        objs = []
        for obj in self.objects:
            if self.item_file_identifier_field in obj:
                obj[self.item_field] = str(obj[self.item_field])
                objs.append(obj)
        self.objects = objs
        json_items = {json_item[self.item_field]: json_item for json_item in self.objects}  # Map items for easy treatment
        if connection.vendor == "sqlite":
            items = {}
            jsl_items = list(json_items.keys())
            for i in range(0, len(json_items), SQLITE_MAX_ROWS):
                items.update(
                    {
                        item.external_id: item
                        for item in Item.objects.filter(external_id__in=jsl_items[i:i+SQLITE_MAX_ROWS])
                    }
                )
        else:
            items = {item.external_id: item for item in Item.objects.filter(external_id__in=json_items.keys())}
        new_items = {}
        categories = set([])
        locales = set([])
        for item_eid, json_item in json_items.items():
            if item_eid not in items:
                try:
                    name = json_item["name"][json_item["default_locale"]]
                except KeyError:
                    name = json_item.get("name", "NO NAME")
                except UnicodeEncodeError as e:
                    logging.error(e)
                    name = "NO NAME"
                new_items[item_eid] = Item(external_id=item_eid, name=name)

            # In case of json[self.item_genres_field] = None
            json_categories = json_item.get(self.item_genres_field, None) or ()
            if isinstance(json_categories, basestring):
                categories.add(json_categories)
            else:
                categories = categories.union(json_item[self.item_genres_field])

            # In case of json[self.item_locales_field] = None
            json_locales = json_item.get(self.item_locales_field, None) or ()
            if isinstance(json_locales, basestring):
                locales.add(json_locales)
            else:
                locales = locales.union(json_locales)
        logging.debug("Items ready to be saved")
        if connection.vendor == "sqlite":
            new_items_list = list(new_items.values())
            for i in range(0, len(new_items_list), SQLITE_MAX_ROWS):
                j = i+SQLITE_MAX_ROWS
                Item.objects.bulk_create(new_items_list[i:j])
        else:
            Item.objects.bulk_create(new_items.values())
        logging.debug("%d new items saved with bulk_create" % len(new_items))

        if connection.vendor == "sqlite":
            jsl_items = list(json_items.keys())
            for i in range(0, len(json_items), SQLITE_MAX_ROWS):
                items.update(
                    {
                        item.external_id: item
                        for item in Item.objects.filter(external_id__in=jsl_items[i:i+SQLITE_MAX_ROWS])
                    }
                )
        else:
            items.update({item.external_id: item for item in Item.objects.filter(external_id__in=json_items.keys())})

        assert len(items) == len(self.objects), \
            "Size of items and size of self.objects are different (%d != %d)" % (len(items), len(self.objects))

        if "recommendation.diversity" in settings.INSTALLED_APPS and not TESTING_MODE:
            logging.debug("Preparing genres")
            db_categories = self.get_genres(categories)
            self.fill_item_genre(items, db_categories)
            logging.debug("Genres loaded")

        if "recommendation.language" in settings.INSTALLED_APPS and not TESTING_MODE:
            logging.debug("Preparing languages")
            db_locales = self.get_locales(locales)
            self.fill_item_locale(items, db_locales)
            logging.debug("Locales loaded")

    @staticmethod
    def get_genres(genres_names):
        """
        Get categories from database and create the ones that don't exist.
        :param genres_names:
        :return: A dict with Genres mapped to their name
        """
        genres = {genre.name: genre for genre in Genre.objects.filter(name__in=genres_names)}
        if len(genres) != len(genres_names):
            new_genres = {}
            for genre_name in genres_names:
                if genre_name not in genres:
                    new_genres[genre_name] = Genre(name=genre_name)

            if connection.vendor == "sqlite":
                new_genres_list = list(new_genres.values())
                for i in range(0, len(new_genres_list), SQLITE_MAX_ROWS):
                    j = i+SQLITE_MAX_ROWS
                    Genre.objects.bulk_create(new_genres_list[i:j])
            else:
                Genre.objects.bulk_create(new_genres.values())
            for genre in Genre.objects.filter(name__in=new_genres):
                genres[genre.name] = genre
        return genres

    @staticmethod
    def get_locales(locales_names):
        """
        Get locales from database and create the ones that don't exist.
        :param locales_names:
        :return: A dict with Locales mapped to their name
        """
        query_locales = Q()
        json_locales = {}
        for locale in locales_names:
            try:
                language_code, country_code = locale.split("-")
            except ValueError:
                language_code, country_code = locale, ""
            if len(language_code) < 3 and len(country_code) < 3:
                json_locales[locale] = (language_code, country_code)
                query_locales = query_locales | Q(language_code=language_code, country_code=country_code)
            else:
                logging.warn("Dropped locale %s for not respecting format XX-XX or XX" % locale)

        locales = {str(locale): locale for locale in Locale.objects.filter(query_locales)}
        if len(locales) != len(locales_names):
            new_locales = []
            new_query = Q()
            for json_locale in locales_names:
                if json_locale not in locales:
                    if json_locale in json_locales:
                        language_code, country_code = json_locales[json_locale]
                        new_locales.append(Locale(language_code=language_code, country_code=country_code))
                        new_query = new_query | Q(language_code=language_code, country_code=country_code)

            if connection.vendor == "sqlite":
                for i in range(0, len(new_locales), SQLITE_MAX_ROWS):
                    j = i+SQLITE_MAX_ROWS
                    Locale.objects.bulk_create(new_locales[i:j])
            else:
                Locale.objects.bulk_create(new_locales)
            for locale in Locale.objects.filter(new_query):
                locales[str(locale)] = locale
        return locales

    def fill_item_genre(self, items, genres):
        """
        Fill item genres connection
        :param items:
        :param genres:
        :return:
        """
        if connection.vendor == "sqlite":
            query_item_genres = Q()
            item_genres = {}
            i = 0
            for json_item in self.objects:
                json_genres = json_item.get(self.item_genres_field, None) or ()
                for json_genre in json_genres:
                    query_item_genres = \
                        query_item_genres | Q(item_id=items[json_item[self.item_field]].pk, type_id=genres[json_genre].pk)
                    item_genres[(items[json_item[self.item_field]].pk, genres[json_genre].pk)] = \
                        ItemGenre(item=items[json_item[self.item_field]], type=genres[json_genre])
                    i += 1
                    if i >= SQLITE_MAX_ROWS:
                        for item_genre in ItemGenre.objects.filter(query_item_genres):
                            del item_genres[(item_genre.item_id, item_genre.type_id)]
                        query_item_genres = Q()
                        i = 0
            for item_genre in ItemGenre.objects.filter(query_item_genres):
                del item_genres[(item_genre.item_id, item_genre.type_id)]

            for i in range(0, len(item_genres), SQLITE_MAX_ROWS):
                j = i+SQLITE_MAX_ROWS
                ItemGenre.objects.bulk_create(item_genres.values()[i:j])
        else:
            query_item_genres = Q()
            item_genres = {}
            for json_item in self.objects:
                json_genres = json_item.get(self.item_genres_field, None) or ()
                for json_genre in json_genres:
                    query_item_genres = \
                        query_item_genres | Q(item_id=items[json_item[self.item_field]].pk, type_id=genres[json_genre].pk)
                    item_genres[(items[json_item[self.item_field]].pk, genres[json_genre].pk)] = \
                        ItemGenre(item=items[json_item[self.item_field]], type=genres[json_genre])
            for item_genre in ItemGenre.objects.filter(query_item_genres):
                del item_genres[(item_genre.item_id, item_genre.type_id)]
            ItemGenre.objects.bulk_create(item_genres)

    def fill_item_locale(self, items, locales):
        """
        Fill item locales connection
        :param items:
        :param locales:
        :return:
        """
        if connection.vendor == "sqlite":
            query_item_locales = Q()
            item_locales = {}
            i = 0
            for json_item in self.objects:
                json_locales = json_item.get(self.item_locales_field, None) or ()
                for locale in json_locales:
                    if locale in locales:
                        query_item_locales = \
                            query_item_locales | Q(locale_id=locales[locale].pk,
                                                   item_id=items[json_item[self.item_field]].pk)
                        item_locales[locales[locale].pk, items[json_item[self.item_field]].pk] = \
                            ItemLocale(locale=locales[locale], item=items[json_item[self.item_field]])
                        i += 1
                        if i >= SQLITE_MAX_ROWS:
                            for item_locale in ItemLocale.objects.filter(query_item_locales):
                                del item_locales[item_locale.locale_id, item_locale.item_id]
                            i = 0
                            query_item_locales = Q()
            for item_locale in ItemLocale.objects.filter(query_item_locales):
                del item_locales[item_locale.locale_id, item_locale.item_id]
            for i in range(0, len(item_locales), SQLITE_MAX_ROWS):
                j = i+SQLITE_MAX_ROWS
                ItemLocale.objects.bulk_create(item_locales.values()[i:j])
        else:
            query_item_locales = Q()
            item_locales = {}
            for json_item in self.objects:
                json_locales = json_item.get(self.item_locales_field, None) or ()
                for locale in json_locales:
                    if locale in locales:
                        query_item_locales = \
                            query_item_locales | Q(locale_id=locales[locale].pk,
                                                   item_id=items[json_item[self.item_field]].pk)
                        item_locales[locales[locale].pk, items[json_item[self.item_field]].pk] = \
                            ItemLocale(locale=locales[locale], item=items[json_item[self.item_field]])
            for item_locale in ItemLocale.objects.filter(query_item_locales):
                del item_locales[item_locale.locale_id, item_locale.item_id]
            ItemLocale.objects.bulk_create(item_locales.values())

    def clean_tmp(self):
        """
        Remove items from tmp path
        :return:
        """
        try:
            shutil.rmtree(self.tmp_dir)  # delete directory
        except OSError as exc:
            if exc.errno != errno.ENOENT:  # ENOENT - no such file or directory
                raise  # re-raise exception

    def fill_db_with_users(self):
        """
        Put users in db
        :return:
        """
        objs = []
        for obj in self.objects:
            if self.user_file_identifier_field in obj:
                obj[self.user_field] = str(obj[self.user_field])
                for item in obj.get(self.user_items_field, ()):
                    item[self.user_item_identifier_field] = str(item[self.user_item_identifier_field])
                objs.append(obj)
        self.objects = objs

        json_users = {json_user[self.user_field]: json_user for json_user in self.objects}  # Map users for easy treatment
        users = {user.external_id: user for user in User.objects.filter(external_id__in=json_users)}
        new_users = {}
        item_eids = set([])
        for user_eid, json_user in json_users.items():
            if user_eid not in users:
                new_users[user_eid] = User(external_id=user_eid)
            item_eids = item_eids.union(map(lambda x: str(x[self.user_item_identifier_field]),
                                            json_user.get(self.user_items_field, ())))
        logging.debug("Users ready to be saved")
        User.objects.bulk_create(new_users.values())
        for user in User.objects.filter(external_id__in=new_users.keys()):
            users[user.external_id] = user

        assert len(users) == len(self.objects), \
            "Size of loaded objects and users in db is not the same (%d != %d)" % (len(users), len(self.objects))

        logging.debug("%d new users saved with bulk_create" % len(new_users))

        logging.debug("Preparing items")
        items = {item.external_id: item for item in Item.objects.filter(external_id__in=item_eids)}

        self.fill_inventory(users, items)
        logging.debug("Items loaded")

    def fill_inventory(self, users, items):
        """
        Fill the user inventory
        :param users:
        :param items:
        :return:
        """
        query_inventory = Q()
        inventory = {}
        for json_user in self.objects:
            for json_item in json_user.get(self.user_items_field, ()):
                try:
                    item_id = items[str(json_item[self.user_item_identifier_field])].pk
                except KeyError:
                    logging.warn("Item with external_id %s does not exist!" % json_item[self.user_item_identifier_field])
                else:
                    user_id = users[json_user[self.user_field]].pk
                    query_inventory = query_inventory | Q(item_id=item_id, user_id=user_id)
                    inv = Inventory(item_id=item_id, user_id=user_id)
                    inv.acquisition_date = datetime.strptime(json_item[self.user_item_acquisition_field], self.date_format)
                    if self.user_item_dropped_field in json_item:
                        inv.dropped_date = datetime.strptime(json_item[self.user_item_dropped_field], self.date_format)
                    inventory[item_id, user_id] = inv

        for inv in Inventory.objects.filter(query_inventory):
            item_id, user_id = inv.item_id, inv.user_id
            if (item_id, user_id) in inventory:
                tmp_inv = inventory[(item_id, user_id)]
                if inv.acquisition_date != tmp_inv.acquisition_date or inv.dropped_date != tmp_inv.dropped_date:
                    inv.acquisition_date = tmp_inv.acquisition_date
                    inv.dropped_date = tmp_inv.dropped_date
                    inventory[(item_id, user_id)] = inv
                    logging.debug(">>> Item %s will be updated for user %s")
                else:
                    del inventory[(item_id, user_id)]

        Inventory.objects.bulk_create(inventory.values())


class Command(DocOptCommand):
    docs = __doc__

    def handle_docopt(self, arguments):
        # arguments contains a dictionary with the options
        try:
            fill_command = FillTool(arguments)
            fill_command.load()
        except:
            traceback.print_exception(*sys.exc_info())
