# Copyright (C) 2011-2012 Canonical Services Ltd
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
# IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
# CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""
Implements a probabilistic distinct counter with sliding windows.

Based on:
http://citeseerx.ist.psu.edu/viewdoc/summary?doi=10.1.1.12.7100

And extended for sliding windows.
"""
import random
import time
import sys

from zope.interface import implementer, implements

from txstatsd.metrics.metric import Metric
from txstatsd.itxstatsd import IMetric


class SBoxHash(object):
    """A very fast hash.

    This class create a random hash function that is very fast.
    Based on SBOXes. Not Crypto Strong.

    Two instances of this class will hash differently.
    """

    def __init__(self):
        self.table = [random.randint(0, 0xFFFFFFFF - 1) for i in range(256)]

    def hash(self, data):
        value = 0
        for c in data:
            value = value ^ self.table[ord(c)]
            value = value * 3
            value = value & 0xFFFFFFFF
        return value


def hash(data):
    """Hash data using a random hasher."""
    p = SBoxHash()
    return p.hash(data)


def zeros(n):
    """Count the zeros to the right of the binary representation of n."""
    count = 0
    i = 0
    while True:
        v = (n >> i)
        if v <= 0:
            return count
        if v & 1:
            return count
        count += 1
        i += 1
    return count


class SlidingDistinctCounter(object):
    """A probabilistic distinct counter with sliding windows."""

    def __init__(self, n_hashes, n_buckets):
        self.n_hashes = n_hashes
        self.n_buckets = n_buckets

        self.hashes = [SBoxHash() for i in range(n_hashes)]
        self.buckets = [[0] * n_buckets for i in range(n_hashes)]

    def add(self, when, item):
        hashes = (h.hash(item) for h in self.hashes)
        for i, value in enumerate(hashes):
            self.buckets[i][min(self.n_buckets - 1, zeros(value))] = when

    def distinct(self, since=0):
        total = 0.0
        for i in range(self.n_hashes):
            least0 = 0
            for b in range(self.n_buckets):
                if self.buckets[i][b] <= since:
                    break
                least0 += 1
            total += least0
        v = total / self.n_hashes
        return int((2 ** v) / 0.77351)


class DistinctMetric(Metric):
    """
    Keeps an estimate of the distinct numbers of items seen on various
    sliding windows of time.
    """

    def mark(self, item):
        """Report this item was seen."""
        self.send("%s|d" % item)


class DistinctMetricReporter(object):
    """
    Keeps an estimate of the distinct numbers of items seen on various
    sliding windows of time.
    """
    if sys.version_info[0:2] == (2,6):
        implements(IMetric)

    def __init__(self, name, wall_time_func=time.time, prefix=""):
        """Construct a metric we expect to be periodically updated.

        @param name: Indicates what is being instrumented.
        @param wall_time_func: Function for obtaining wall time.
        @param prefix: If present, a string to prepend to the message
            composed when C{report} is called.
        """
        self.name = name
        self.wall_time_func = wall_time_func
        self.counter = SlidingDistinctCounter(32, 32)
        if prefix:
            prefix += "."
        self.prefix = prefix

    def count(self):
        return self.counter.distinct()

    def count_1min(self, now):
        return self.counter.distinct(now - 60)

    def count_1hour(self, now):
        return self.counter.distinct(now - 60 * 60)

    def count_1day(self, now):
        return self.counter.distinct(now - 60 * 60 * 24)

    def process(self, fields):
        self.update(fields[0])

    def update(self, item):
        self.counter.add(self.wall_time_func(), item)

    def flush(self, interval, timestamp):
        now = self.wall_time_func()
        metrics = []
        items = {".count": self.count(),
                 ".count_1min": self.count_1min(now),
                 ".count_1hour": self.count_1hour(now),
                 ".count_1day": self.count_1day(now)}
        for item, value in sorted(items.iteritems()):
            metrics.append((self.prefix + self.name + item, value, timestamp))
        return metrics

# if we are running anything >= 2.7
if sys.version_info[0:2] >= (2,7):
    DistinctMetricReporter = implementer(IMetric)(DistinctMetricReporter)
