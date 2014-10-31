#! -*- encoding: utf-8 -*-
"""
Models for Module configuration of frappe system.
"""
__author__ = "joaonrb"

try:
    import cPickle as pickle
except ImportError:
    import pickle
import json
import zlib
import numpy as np
from six import string_types
from django.db import models
from django.utils.translation import ugettext as _
from django.utils.six import with_metaclass
from django.db.models.signals import pre_save
from django.dispatch import receiver
from frappe.decorators import Cached


class PythonObjectField(with_metaclass(models.SubfieldBase, models.TextField)):
    """
    Mapped structure to be saved in database.
    """

    description = """Python object field."""
    __metaclass__ = models.SubfieldBase

    def to_python(self, value):
        """
        Convert the value from the database to python like object

        :param value: String from database.
        :return: A python objects.
        """
        if isinstance(value, string_types) and value:
            return pickle.loads(zlib.decompress(value))
        return value

    def get_prep_value(self, value):
        """
        Prepare the value from python like object to database like value.

        :param value: Matrix to keep in database
        :return: Pickled object.
        """
        return zlib.compress(pickle.dumps(value))


class JSONField(with_metaclass(models.SubfieldBase, models.TextField)):
    """
    JSON structure to be saved in database.
    """

    description = """Json field."""
    __metaclass__ = models.SubfieldBase

    def to_python(self, value):
        """
        Convert the value from the database to json.

        :param value: String from database.
        :return: A python objects.
        """
        if isinstance(value, string_types) and value:
            return json.loads(value)
        return value

    def get_prep_value(self, value):
        """
        Prepare the value from python dictionary to database like value.

        :param value: Matrix to keep in database
        :return: Pickled object.
        """
        return json.dumps(value)


class AlgorithmData(models.Model):
    """
    Data to feed the predictor algorithm
    """

    identifier = models.CharField(_("identifier"), max_length=255)
    model_id = models.SmallIntegerField(_("model identifier"), null=True, blank=True)
    data = PythonObjectField(_("data"))
    timestamp = models.DateTimeField(_("timestamp"), auto_now_add=True)

    class Meta:
        verbose_name = _("algorithm data")
        verbose_name_plural = _("algorithm data")

    def __str__(self):
        """
        Algorithm string representation.
        """
        return "%s, %s" % (self.identifier, self.timestamp)

    def __unicode__(self):
        """
        Algorithm string representation.
        """
        return "%s, %s" % (self.identifier, self.timestamp)


class PythonObject(models.Model):
    """
    Python object
    """

    identifier = models.CharField(_("identifier"), max_length=255)
    obj = PythonObjectField(_("object"))

    class Meta:
        verbose_name = _("python object")
        verbose_name_plural = _("python objects")

    def __str__(self):
        """
        Object string representation.
        """
        return self.identifier

    def __unicode__(self):
        """
        Object string representation.
        """
        return self.identifier

    @staticmethod
    @Cached(cache="local")
    def get_object(object_id):
        return PythonObject.objects.get(pk=object_id).obj


class Predictor(models.Model):
    """
    Predictor algorithm.
    """

    identifier = models.CharField(_("identifier"), max_length=255)
    python_class = models.CharField(_("python class"), max_length=255)
    kwargs = JSONField(_("kwargs"), default={})
    data = models.ManyToManyField(AlgorithmData, verbose_name=_("data"))

    class Meta:
        verbose_name = _("predictor")
        verbose_name_plural = _("predictors")

    def __str__(self):
        """
        Predictor string representation.
        """
        return self.identifier

    def __unicode__(self):
        """
        Predictor string representation.
        """
        return self.identifier

    @staticmethod
    @Cached(cache="local")
    def get_predictor(predictor_id):
        """
        Return the predictor
        """
        return Predictor.objects.get(pk=predictor_id)

    @staticmethod
    @Cached(cache="local")
    def get_class(predictor_id):
        """
        Get this predictor class
        """
        class_parts = Predictor.get_predictor(predictor_id).python_class.split(".")
        module, cls = ".".join(class_parts[:-1]), class_parts[-1]
        return getattr(__import__(module), cls)


@receiver(pre_save, sender=Predictor)
def check_predictor(sender, instance, using, **kwarg):
    class_parts = instance.python_class.split(".")
    module, cls = ".".join(class_parts[:-1]), class_parts[-1]
    getattr(__import__(module), cls)(**json.loads(instance.kwargs))


class Module(models.Model):
    """
    Module holding recommendation configuration.
    """

    identifier = models.CharField(_("identifier"), max_length=255)
    listed_items = JSONField(_("items"), default={})
    predictors = models.ManyToManyField(Predictor, verbose_name=_("predictors"), related_name="modules",
                                        through="PredictorWithAggregator")
    filters = models.ManyToManyField(PythonObject, verbose_name=_("filters"), related_name="module_as_filter",
                                     through="Filter")
    rerankers = models.ManyToManyField(PythonObject, verbose_name=_("re-rankers"), related_name="module_as_reranker",
                                       through="ReRanker")

    class Meta:
        verbose_name = _("module")
        verbose_name_plural = _("modules")

    def __str__(self):
        """
        Predictor string representation.
        """
        return self.identifier

    def __unicode__(self):
        """
        Predictor string representation.
        """
        return self.identifier

    @staticmethod
    @Cached()
    def get_module(module_id):
        """
        Return module based on module id
        :param module_id:
        :return:
        """
        return Module.objects.get(pk=module_id)

    @staticmethod
    @Cached()
    def get_predictors(module_id):
        """
        Get all predictors for this module
        :param module_id:
        :return:
        """
        return [pid for pid, in Predictor.objects.filter(modules_id=module_id).values_list("pk")]

    @staticmethod
    @Cached()
    def get_predictor(module_id, predictor_id):
        """
        Return predictor for this module
        """
        module = Module.get_module(module_id)
        predictor = Predictor.get_predictor(predictor_id)
        predictor_class = Predictor.get_class(predictor_id)
        return predictor_class.load_predictor(predictor, module)

    @staticmethod
    @Cached()
    def get_aggregator(module_id):
        return {agg.predictor_id: agg.weight for agg in PredictorWithAggregator.objects.filter(module_id=module_id)}

    def aggregate(self, predictions):
        """
        Aggregate all predictions in one recommendation
        :param predictions:
        :return:
        """
        weights = self.get_aggregator(self.pk)
        return np.sum(map(lambda pid: predictions[pid]*weights[pid], predictions.keys()))

    def predict_scores(self, user, size):
        """
        Predict score
        :param user: User to get recommendation
        :param size: Size of requested recommendation
        :return: A list with recommendation
        """
        recommendations = {
            predictor_id: self.get_predictor(self.pk, predictor_id)(user, size)
            for predictor_id in self.get_predictors(self.pk)
        }
        recommendation = self.aggregate(recommendations)
        for rfilter in self.get_filters(self.pk):
            recommendation = rfilter(recommendation, size)
        for reranker in self.get_re_renkers(self.pk):
            recommendation = reranker(recommendation, size)
        return recommendation


class PredictorWithAggregator(models.Model):
    """
    Aggregates module with predictor
    """

    predictor = models.ForeignKey(Predictor, verbose_name=_("predictor"))
    module = models.ForeignKey(Module, verbose_name=_("module"))
    weight = models.FloatField(_("weight"), null=True, blank=True)

    class Meta:
        verbose_name = _("predictor with aggregator")
        verbose_name_plural = _("predictor with aggregators")

    def __str__(self):
        """
        Predictor string representation.
        """
        return "%f for %s in module %s" % (self.weight, self.predictor, self.module)

    def __unicode__(self):
        """
        Predictor string representation.
        """
        return "%f for %s in module %s" % (self.weight, self.predictor, self.module)


class Filter(models.Model):
    """
    Model for many to many filters.
    """

    obj = models.ForeignKey(PythonObject, verbose_name=_("filter object"))
    module = models.ForeignKey(Module, verbose_name=_("module"))

    class Meta:
        verbose_name = _("filter")
        verbose_name_plural = _("filters")


class ReRanker(models.Model):
    """
    Model for many to many filters.
    """

    obj = models.ForeignKey(PythonObject, verbose_name=_("re-ranker object"))
    module = models.ForeignKey(Module, verbose_name=_("module"))

    class Meta:
        verbose_name = _("re-ranker")
        verbose_name_plural = _("re-rankers")