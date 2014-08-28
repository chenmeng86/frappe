# -*- encoding=utf-8 -*-
"""
The locale models moudle. It must contain the locale
"""
__author__ = "joaonrb"


from django.db import models
from django.db.models.signals import post_save, m2m_changed
from django.dispatch import receiver
from django.utils.translation import ugettext as _
from recommendation.models import Item, User, CacheManager, IterableCacheManager


class Locale(models.Model):
    """
    Model for a specific locale
    """
    language_code = models.CharField(_("language code"), max_length=2)
    country_code = models.CharField(_("country code"), max_length=2, default="")
    name = models.CharField(_("country"), max_length=255, default="")
    items = models.ManyToManyField(Item, verbose_name=_("items"), related_name="available_locales", blank=True)
    users = models.ManyToManyField(User, verbose_name=_("users"), related_name="required_locales", blank=True)

    all_locales = IterableCacheManager("locallid")
    item_locales = CacheManager("locits")
    user_locales = CacheManager("locusr")
    items_by_locale = CacheManager("locitsb")
    users_by_locale = CacheManager("locusrb")

    class Meta:
        verbose_name = _("locale")
        verbose_name_plural = _("locales")
        unique_together = ("language_code", "country_code")

    def __str__(self):
        return "%s%s" % (self.language_code, "-%s" % self.country_code if self.country_code else "")

    def save(self, *args, **kwargs):
        """
        Makes the codes to be saved always using lower case
        :param args:
        :param kwargs:
        :return:
        """
        self.language_code = self.language_code.lower()
        self.country_code = self.country_code.lower()
        super(Locale, self).save(*args, **kwargs)

    @staticmethod
    def load_locale():
        for instance in Locale.objects.all():
            for i in instance.items.all():
                Locale.item_locales[i] = Locale.item_locales.get(i, set([]).union((instance.pk,)))
                Locale.items_by_locale[instance.pk] = Locale.items_by_locale.get(instance.pk, set([])).union((i,))
            for u in instance.users.all():
                Locale.user_locales[u] = Locale.user_locales.get(u, set([]).union((instance.pk,)))
                Locale.users_by_locales[instance.pk] = Locale.users_by_locales.get(instance.pk, set([]).union((u,)))


@receiver(post_save, sender=Locale)
def add_locale_to_cache(sender, instance, created, raw, using, update_fields, *args, **kwargs):
    """
    Add locale to cache upon creation
    """
    Locale.all_locales[instance.pk] = instance


@receiver(m2m_changed, sender=Locale.items.through)
def add_item_locale_to_cache(sender, instance, action, reverse, model, pk_set, using, *args, **kwargs):
    """
    Add item to locale cache upon creation
    """
    if action == "post_save":
        for i in pk_set:
            Locale.item_locales[i] = Locale.item_locales.get(i, set([]).union((instance.pk,)))
            Locale.items_by_locale[instance.pk] = Locale.items_by_locale.get(instance.pk, set([])).union((i,))
    if action == "post_remove":
        for i in pk_set:
            l = Locale.item_locales.get(i, set([]))
            l.discard(instance.pk)
            Locale.item_locales[i] = l
            l = Locale.items_by_locales.get(instance.pk, set([]))
            l.discard(i)
            Locale.items_by_locales[instance.pk] = l


@receiver(m2m_changed, sender=Locale.users.through)
def add_user_locale_to_cache(sender, instance, action, reverse, model, pk_set, using, *args, **kwargs):
    """
    Add users to locale cache upon creation
    """
    if action == "post_save":
        for u in pk_set:
            Locale.user_locales[u] = Locale.user_locales.get(u, set([]).union((instance.pk,)))
            Locale.users_by_locales[instance.pk] = Locale.users_by_locales.get(instance.pk, set([]).union((u,)))
    if action == "post_remove":
        for u in pk_set:
            l = Locale.user_locales.get(u, set([]))
            l.discard(instance.pk)
            Locale.user_locales[u] = l
            l = Locale.users_by_locales.get(instance.pk, set([]))
            l.discard(u)
            Locale.users_by_locales[instance.pk] = l


from django.contrib import admin
admin.site.register([Locale])