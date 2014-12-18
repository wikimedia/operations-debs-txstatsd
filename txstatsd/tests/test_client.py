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
"""Tests for the various client classes."""

import sys

from mock import Mock, call
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, Deferred
from twisted.python import log
from twisted.trial.unittest import TestCase

import txstatsd.client
import txstatsd.metrics.metric
import txstatsd.metrics.metrics
from txstatsd.metrics.metric import Metric
from txstatsd.client import (
    StatsDClientProtocol, TwistedStatsDClient, UdpStatsDClient,
    ConsistentHashingClient
)
from txstatsd.protocol import DataQueue, TransportGateway


class FakeClient(object):

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.data = []
        self.connect_called = False
        self.disconnect_called = False

    def __str__(self):
        return "%s:%d" % (self.host, self.port)

    def write(self, data):
        self.data.append(data)

    def connect(self):
        self.connect_called = True

    def disconnect(self):
        self.disconnect_called = True


class TestClient(TestCase):

    def setUp(self):
        super(TestClient, self).setUp()
        self.client = None
        self.exception = None

    def tearDown(self):
        if self.client:
            self.client.transport.stopListening()
        super(TestClient, self).tearDown()

    def build_protocol(self):
        protocol = StatsDClientProtocol(self.client)
        reactor.listenUDP(0, protocol)

    def test_twistedstatsd_write(self):
        self.client = TwistedStatsDClient('127.0.0.1', 8000)
        self.build_protocol()
        self.client.host_resolved('127.0.0.1')

        def ensure_bytes_sent(bytes_sent):
            self.assertEqual(bytes_sent, len('message'))

        def exercise(callback):
            self.client.write('message', callback=callback)

        d = Deferred()
        d.addCallback(ensure_bytes_sent)
        reactor.callWhenRunning(exercise, d.callback)
        return d

    @inlineCallbacks
    def test_twistedstatsd_write_with_host_resolved(self):
        self.client = TwistedStatsDClient.create(
            'localhost', 8000)
        self.build_protocol()
        yield self.client.resolve_later

        def ensure_bytes_sent(bytes_sent):
            self.assertEqual(bytes_sent, len('message'))
            self.assertEqual(self.client.host, '127.0.0.1')

        def exercise(callback):
            self.client.write('message', callback=callback)

        d = Deferred()
        d.addCallback(ensure_bytes_sent)
        reactor.callWhenRunning(exercise, d.callback)
        yield d

    @inlineCallbacks
    def test_twistedstatsd_with_malformed_address_and_errback(self):
        exceptions_captured = []

        def capture_exception_raised(failure):
            exception = failure.getErrorMessage()
            self.assertTrue(exception.startswith("DNS lookup failed"))
            exceptions_captured.append(exception)

        self.client = TwistedStatsDClient.create(
            '256.0.0.0', 1,
            resolver_errback=capture_exception_raised)
        self.build_protocol()
        yield self.client.resolve_later

        self.assertEqual(len(exceptions_captured), 1)

    @inlineCallbacks
    def test_twistedstatsd_with_malformed_address_and_no_errback(self):
        exceptions_captured = []

        def capture_exception_raised(failure):
            exception = failure.getErrorMessage()
            self.assertTrue(exception.startswith("DNS lookup failed"))
            exceptions_captured.append(exception)

        self.patch(log, "err", capture_exception_raised)

        self.client = TwistedStatsDClient.create(
            '256.0.0.0', 1)
        self.build_protocol()
        yield self.client.resolve_later

        self.assertEqual(len(exceptions_captured), 1)

    def test_udpstatsd_wellformed_address(self):
        client = UdpStatsDClient('localhost', 8000)
        self.assertEqual(client.host, '127.0.0.1')
        client = UdpStatsDClient(None, None)
        self.assertEqual(client.host, None)

    def test_udpstatsd_malformed_address(self):
        self.assertRaises(ValueError,
                          UdpStatsDClient, 'localhost', -1)
        self.assertRaises(ValueError,
                          UdpStatsDClient, 'localhost', 'malformed')
        self.assertRaises(ValueError,
                          UdpStatsDClient, 0, 8000)

    def test_udpstatsd_socket_nonblocking(self):
        client = UdpStatsDClient('localhost', 8000)
        client.connect()
        # According to the python docs (and the source, I've checked)
        # setblocking(0) is the same as settimeout(0.0).
        self.assertEqual(client.socket.gettimeout(), 0.0)

    def test_udp_client_can_be_imported_without_twisted(self):
        """Ensure that the twisted-less client can be used without twisted."""
        unloaded = [(name, mod) for (name, mod) in sys.modules.items()
                    if 'twisted' in name]
        def restore_modules():
            for name, mod in unloaded:
                sys.modules[name] = mod
            reload(txstatsd.client)
            reload(txstatsd.metrics.metrics)
            reload(txstatsd.metrics.metric)
        self.addCleanup(restore_modules)

        # Mark everything twistedish as unavailable
        for name, mod in unloaded:
            sys.modules[name] = None

        reload(txstatsd.client)
        reload(txstatsd.metrics.metrics)
        reload(txstatsd.metrics.metric)
        for mod in sys.modules:
            if 'twisted' in mod:
                self.assertTrue(sys.modules[mod] is None)

    def test_starts_with_data_queue(self):
        """The client starts with a DataQueue."""
        self.client = TwistedStatsDClient('127.0.0.1', 8000)
        self.build_protocol()

        self.assertIsInstance(self.client.data_queue, DataQueue)

    def test_starts_with_transport_gateway_if_ip(self):
        """The client starts without a TransportGateway."""
        self.client = TwistedStatsDClient('127.0.0.1', 8000)
        self.build_protocol()

        self.assertTrue(self.client.transport_gateway is not None)

    def test_starts_without_transport_gateway_if_not_ip(self):
        """The client starts without a TransportGateway."""
        self.client = TwistedStatsDClient('localhost', 8000)
        self.build_protocol()

        self.assertTrue(self.client.transport_gateway is None)

    def test_passes_transport_to_gateway(self):
        """The client passes the transport to the gateway as soon as the client
        is connected."""
        self.client = TwistedStatsDClient('127.0.0.1', 8000)
        self.build_protocol()
        self.client.host_resolved('127.0.0.1')

        self.assertEqual(self.client.transport_gateway.transport,
                         self.client.transport)

    def test_passes_reactor_to_gateway(self):
        """The client passes the reactor to the gateway as soon as the client
        is connected."""
        self.client = TwistedStatsDClient('127.0.0.1', 8000)
        self.build_protocol()
        self.client.host_resolved('127.0.0.1')

        self.assertEqual(self.client.transport_gateway.reactor,
                         self.client.reactor)

    def test_sets_ip_when_host_resolves(self):
        """As soon as the host is resolved, set the IP as the host."""
        self.client = TwistedStatsDClient('localhost', 8000)
        self.build_protocol()
        self.assertEqual(self.client.host, 'localhost')

        self.client.host_resolved('127.0.0.1')
        self.assertEqual(self.client.host, '127.0.0.1')

    def test_sets_transport_gateway_when_host_resolves(self):
        """As soon as the host is resolved, set the transport gateway."""
        self.client = TwistedStatsDClient('localhost', 8000)
        self.build_protocol()

        self.client.transport_gateway = None

        self.client.host_resolved('127.0.0.1')
        self.assertIsInstance(self.client.transport_gateway, TransportGateway)

    def test_calls_connect_callback_when_host_resolves(self):
        """As soon as the host is resolved, call back the connect_callback."""
        self.client = TwistedStatsDClient('localhost', 8000)
        self.build_protocol()

        self.client.connect_callback = Mock()

        self.client.host_resolved('127.0.0.1')
        self.assertTrue(self.client.connect_callback.called)
        self.client.connect_callback.assert_called_once_with()

    def test_sends_messages_to_gateway_after_host_resolves(self):
        """After the host is resolved, send messages to the
        TransportGateway."""
        self.client = TwistedStatsDClient('localhost', 8000)
        self.build_protocol()
        self.client.host_resolved('127.0.0.1')

        message = 'some data'
        bytes_sent = len(message)
        self.client.data_queue =  Mock(spec=DataQueue)
        self.client.transport_gateway = Mock(spec=TransportGateway)
        callback = Mock()
        self.client.transport_gateway.write.return_value = bytes_sent
        self.assertEqual(self.client.write(message, callback), bytes_sent)
        self.client.transport_gateway.write.assert_called_once_with(
            message, callback)

    def test_sends_messages_to_queue_before_host_resolves(self):
        """Before the host is resolved, send messages to the DataQueue."""
        self.client = TwistedStatsDClient('localhost', 8000)
        self.build_protocol()

        message = 'some data'
        self.client.data_queue = Mock(spec=DataQueue)
        callback = Mock()
        self.client.data_queue.write.return_value = None
        result = self.client.write(message, callback)
        self.client.data_queue.write.assert_called_once_with(message, callback)
        self.assertEqual(result, None)

    def test_flushes_queued_messages_to_the_gateway_when_host_resolves(self):
        """As soon as the host is resolved, flush all messages to the
        TransportGateway."""
        self.client = TwistedStatsDClient('localhost', 8000)
        self.build_protocol()

        self.client.data_queue.write('data 1', 'callback 1')
        self.client.data_queue.write('data 2', 'callback 2')
        self.client.data_queue.write('data 3', 'callback 3')

        mock_gateway_write = Mock()
        self.patch(TransportGateway, 'write', mock_gateway_write)
        self.client.host_resolved('127.0.0.1')
        self.assertTrue(mock_gateway_write.call_count, 3)
        expected = [call('data 1', 'callback 1'),
                    call('data 2', 'callback 2'),
                    call('data 3', 'callback 3')]
        self.assertEqual(mock_gateway_write.call_args_list, expected)

    def test_sets_client_transport_when_connected(self):
        """Set the transport as an attribute of the client."""
        self.client = TwistedStatsDClient('localhost', 8000)
        transport = DummyTransport()
        self.client.connect(transport)

        self.assertEqual(self.client.transport, transport)

    def test_sets_gateway_transport_when_connected(self):
        """Set the transport as an attribute of the TransportGateway."""
        self.client = TwistedStatsDClient('localhost', 8000)
        self.client.host_resolved('127.0.0.1')
        transport = DummyTransport()
        self.client.connect(transport)

        self.assertEqual(self.client.transport_gateway.transport, transport)


class DataQueueTest(TestCase):
    """Tests for the DataQueue class."""

    def setUp(self):
        super(DataQueueTest, self).setUp()
        self.queue = DataQueue(limit=2)

    def test_queues_messages_and_callbacks(self):
        """All messages are queued with their respective callbacks."""
        self.queue.write(data=1, callback='1')
        self.queue.write(data=2, callback='2')

        self.assertEqual(self.queue.flush(), [
            (1, '1'),
            (2, '2'),
        ])

    def test_flushes_the_queue(self):
        """All messages are queued with their respective callbacks."""
        self.queue.write(data=1, callback='1')
        self.queue.write(data=2, callback='2')

        self.queue.flush()
        self.assertEqual(self.queue.flush(), [])

    def test_limits_number_of_messages(self):
        """Cannot save more messages than the defined limit."""
        self.queue.write('saved data', 'saved callback')
        self.queue.write('saved data', 'saved callback')
        self.queue.write('discarded data', 'discarded message')

        self.assertEqual(len(self.queue.flush()), 2)

    def test_discards_messages_after_limit(self):
        """Cannot save more messages than the defined limit."""
        self.queue.write('saved data', 'saved callback')
        self.queue.write('saved data', 'saved callback')
        self.queue.write('discarded data', 'discarded message')

        self.assertEqual(set(self.queue.flush()),
                         set([('saved data', 'saved callback')]))

    def test_makes_limit_optional(self):
        """Use the default limit when not given."""
        queue = DataQueue()

        self.assertTrue(queue._limit > 0)


class TestConsistentHashingClient(TestCase):

    def test_hash_with_single_client(self):
        clients = [
            FakeClient("127.0.0.1", 10001),
            ]
        client = ConsistentHashingClient(clients)
        bar = Metric(client, "bar")
        foo = Metric(client, "foo")
        dba = Metric(client, "dba")
        bar.send("1")
        foo.send("1")
        dba.send("1")
        self.assertEqual(clients[0].data, ["bar:1",
                                           "foo:1",
                                           "dba:1"])

    def test_hash_with_two_clients(self):
        clients = [
            FakeClient("127.0.0.1", 10001),
            FakeClient("127.0.0.1", 10002),
            ]
        client = ConsistentHashingClient(clients)
        bar = Metric(client, "bar")
        foo = Metric(client, "foo")
        dba = Metric(client, "dba")
        bar.send("1")
        foo.send("1")
        dba.send("1")
        self.assertEqual(clients[0].data, ["bar:1",
                                           "dba:1"])
        self.assertEqual(clients[1].data, ["foo:1"])

    def test_hash_with_three_clients(self):
        clients = [
            FakeClient("127.0.0.1", 10001),
            FakeClient("127.0.0.1", 10002),
            FakeClient("127.0.0.1", 10003),
            ]
        client = ConsistentHashingClient(clients)
        bar = Metric(client, "bar")
        foo = Metric(client, "foo")
        dba = Metric(client, "dba")
        bar.send("1")
        foo.send("1")
        dba.send("1")
        self.assertEqual(clients[0].data, ["bar:1"])
        self.assertEqual(clients[1].data, ["foo:1"])
        self.assertEqual(clients[2].data, ["dba:1"])

    def test_connect_with_two_clients(self):
        clients = [
            FakeClient("127.0.0.1", 10001),
            FakeClient("127.0.0.1", 10002),
            ]
        client = ConsistentHashingClient(clients)
        client.connect()
        self.assertTrue(clients[0].connect_called)
        self.assertTrue(clients[1].connect_called)

    def test_disconnect_with_two_clients(self):
        clients = [
            FakeClient("127.0.0.1", 10001),
            FakeClient("127.0.0.1", 10002),
            ]
        client = ConsistentHashingClient(clients)
        client.disconnect()
        self.assertTrue(clients[0].disconnect_called)
        self.assertTrue(clients[1].disconnect_called)


class DummyTransport(object):
    def stopListening(self):
        pass
