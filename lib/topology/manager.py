# -*- coding: utf-8 -*-
#
# Copyright (C) 2015 Hewlett Packard Enterprise Development LP <asicapi@hp.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""
topology manager module.

This module takes care of understanding the topology defined in the test
suite, commanding its construction to appropriate topology platform plugin
and finally commanding its destruction to the plugin also.
"""

from __future__ import unicode_literals, absolute_import
from __future__ import print_function, division

import logging
from copy import deepcopy
from datetime import datetime
from traceback import format_exc
from collections import OrderedDict

from pynml.manager import ExtendedNMLManager

from .parser import parse_txtmeta
from .platforms.base import BaseNode
from .platforms.manager import platforms, DEFAULT_PLATFORM


log = logging.getLogger(__name__)


class TopologyManager(object):
    """
    Main Topology Manager object.

    This object is responsible to build a topology given a description.

    There is three options to build a topology:

    - Using the simplified textual description using a Graphviz like syntax.
      To use this option call the :meth:`parse`.
    - Using a basic Python dictionary to load the description of the topology.
      To use this option call the :meth:`load`.
    - Build a full NML topology using NML objects and relations and register
      all the objects in the namespace using the enbbedded
      :class:`pynml.manager.ExtendedNMLManager`, for example:

      ::

         mgr = TopologyManager()
         sw1 = mgr.create_node(name='My Switch 1')
         sw2 = mgr.create_node(name='My Switch 2')
         sw1p1 = mgr.create_biport(sw1)
         # ...

      See :class:`pynml.manager.ExtendedNMLManager` for more information of
      this usage.

    :param str engine: Name of the engine platform to build the topology.
     See :func:`platforms` for how to get and discover available platforms.
    """

    def __init__(self, engine=DEFAULT_PLATFORM, **kwargs):
        self.nml = ExtendedNMLManager(**kwargs)
        self.engine = engine
        self.nodes = OrderedDict()
        self._platform = None
        self._built = False

    def load(self, dictmeta):
        """
        Load a topology description in a dictionary format.

        Dictionary format:

        ::

            {
                'nodes': [
                    {
                        'nodes': ('sw1', 'hs1'),
                        'attributes': {...}
                    },
                ],
                'links': [
                    {
                        'endpoints': (('sw1', 1), ('hs1', 1)),
                        'attributes': {...}
                    },
                ]
            }

        See also the module :mod:`topology.parser`.

        :param dict dictmeta: The dictionary to load the topology from.
        """
        # Load nodes
        for nodes_spec in dictmeta.get('nodes', []):
            attributes = nodes_spec['attributes']
            for node_id in nodes_spec['nodes']:
                self.nml.create_node(
                    identifier=node_id, **attributes
                )

        # Load links
        for link_spec in dictmeta.get('links', []):

            # Create biports
            endpoints = [None, None]
            for idx, (node_id, port) in enumerate(link_spec['endpoints']):

                # Auto-create nodes
                if node_id not in self.nml.namespace:
                    self.nml.create_node(identifier=node_id)

                # FIXME: Implement auto creation features
                if port is None:
                    raise NotImplementedError('Auto-port feature is missing')

                node = self.nml.get_object(node_id)
                identifier = node_id + '_p{}'.format(port)
                biport = self.nml.create_biport(
                    node, identifier=identifier, port_number=port
                )

                # Register endpoint
                endpoints[idx] = biport

            # Create links
            attrs = deepcopy(link_spec['attributes'])
            self.nml.create_bilink(*endpoints, **attrs)

    def parse(self, txtmeta, load=True):
        """
        Parse a textual topology meta-description.

        For a description of the textual format see the module
        :mod:`topology.parser`.
        """
        data = parse_txtmeta(txtmeta)
        if load:
            self.load(data)
        return data

    def is_built(self):
        """
        Check if the current topology was built.

        :rtype: bool
        :return: True if the topology was succesfully built.
        """
        return self._built

    def build(self):
        """
        Build the topology hold by this manager.

        This method instance the platform engine and request the build of the
        topology defined.
        """
        timestamp = datetime.now().replace(microsecond=0).isoformat()
        stage = 'instance'

        # Instance platform
        plugin = platforms()[self.engine]
        self._platform = plugin(timestamp, self.nml)

        try:
            stage = 'pre_build'
            self._platform.pre_build()

            stage = 'add_node'
            for node in self.nml.nodes():
                enode = self._platform.add_node(node)

                # Check that engine node implements the minimum interface
                if not isinstance(enode, BaseNode):
                    msg = (
                        'Platform {} returned an invalid '
                        'engine node {}.'
                    ).format(self.engine, enode)
                    log.critical(msg)
                    raise Exception(msg)

                self.nodes[enode.identifier] = enode

            stage = 'add_biport'
            for node, biport in self.nml.biports():
                self._platform.add_biport(node, biport)

            stage = 'add_bilink'
            for node_porta, node_portb, bilink in self.nml.bilinks():
                self._platform.add_bilink(node_porta, node_portb, bilink)

            stage = 'post_build'
            self._platform.post_build()

        except Exception as e:
            log.critical(
                (
                    'Build failed at stage "{}" with "{}". '
                    'Calling plugin rollback routine...'
                ).format(e, stage)
            )
            log.debug(format_exc())
            self._platform.rollback(stage, self.nodes, e)
            raise e

        self._built = True

    def unbuild(self):
        """
        Undo the topology.

        This removes all references to the engine nodes and request the
        platform to destroy the topology.
        """
        if not self._built:
            raise RuntimeError(
                'You cannot unbuild and never built topology.'
            )

        # Remove own reference to enodes
        self.nodes = OrderedDict()

        # Call platform destroy hook
        self._platform.destroy()

        # Explicitly delete platform
        del self._platform
        self._platform = None

    def get(self, identifier):
        """
        Get a engine platform with given identifier.

        :param str identifier: The node identifier.
        :rtype: A subclass of :class:`topology.platforms.base.BaseNode`
        :return: The engine node implementing the communication with the node.
        """
        return self.nodes.get(identifier, None)


__all__ = ['TopologyManager']
