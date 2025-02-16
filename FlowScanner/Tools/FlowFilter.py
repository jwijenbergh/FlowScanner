"""
Module to filter server IP's from (Net)Flow
"""
#! /usr/bin/env python

import os
import sys
from os import path
from typing import Dict
import logging

import netaddr
import requests


class FlowFilter:
    """
    The FlowFilter class is responsible for filtering the server
    IP's and port out of the flow data.
    """
    ports: Dict[str, Dict[int, float]] = {}
    ports_dict_filled = False
    ip_port_dict = [ ]
    surf_nets = None

    def __init__(self):
        self.surf_nets = netaddr.IPSet()
        if not path.exists(os.getenv('known_ip_nets_file')):
            logging.error("IP address file does not exist at %s", os.getenv('known_ip_nets_file'))
            sys.exit()
        self.LoadIPS(os.getenv('known_ip_nets_file'), os.getenv('ip_block_list_file'))

    def LoadIPS(self, scope_filename, exclude_filename=None):
        '''
        Load IPs in scope from the filename provided. Excludes IPs if excludelist provided.
        '''
        try:
            with open(scope_filename, 'r', encoding="utf-8") as ip_file:
                for net in ip_file.read().splitlines():
                    try:
                        self.surf_nets.add(netaddr.IPNetwork(net))
                    except ValueError as valueerror:
                        logging.warning("Scope file value error: %s", valueerror)
        except (IOError, AttributeError, AssertionError) as openerror:
            logging.error("Error while opening scope file: %s, with error: %s",
                            scope_filename,
                            openerror)
        try:
            with open(exclude_filename, 'r', encoding="utf-8") as block_file:
                for line in block_file:
                    try:
                        self.surf_nets.remove(line)
                    except ValueError as valueerror:
                        logging.warning("Exclude file value error: %s", valueerror)
        except (IOError, AttributeError, AssertionError) as openerror:
            logging.error("Error while opening scope file: %s, with error: %s",
                            exclude_filename,
                            openerror)

    def ServerFilter(self, flowlist: list):
        """
        Main function to filter the server IP's and corresponding ports
        """
        self.ip_port_dict = [ ]
        for flow in flowlist:
            if flow.ip_source.is_multicast() or flow.ip_source.is_link_local():
                continue
            if flow.ip_dest.is_multicast() or flow.ip_dest.is_link_local():
                continue
            if flow.ip_source == netaddr.IPAddress("255.255.255.255"):
                continue
            if flow.ip_dest == netaddr.IPAddress("255.255.255.255"):
                continue

            match self.NmapPortLogic(flow.port_source, flow.port_dest, flow.proto):
                case 1:
                    self.AddIPToList(flow.ip_version, flow.ip_source, flow.port_source, flow.proto)
                case 0:
                    ##More magic (if this event will occur, not sure yet, test with more data)
                    logging.warning("This event needs more magic! "
                                    "Function ServerFilter (in FlowFilter).")
                    logging.warning("More magic for: %s", flow)
                case -1:
                    self.AddIPToList(flow.ip_version, flow.ip_dest, flow.port_dest, flow.proto)
        return self.ip_port_dict

    def LoadNMAPServices(self) -> None:
        """
        Loads values from NMAP services file. Checks if the file
        exists on the disk. If not, it downloads a new one.
        """
        if not path.exists(os.getenv('nmap_services_file_location')):
            logging.debug("Nmap file not found, trying to fetch new one from the internet...")
            try:
                url = os.getenv('nmap_web_file_url',
                                "https://raw.githubusercontent.com/nmap/nmap/master/nmap-services")
                req = requests.get(url, allow_redirects=True)
                with open(os.getenv('nmap_services_file_location'), 'wb') as nmapfile:
                    nmapfile.write(req.content)
            except IOError as exception:
                logging.error("Can not download file: %s", exception)
                sys.exit(1)

        try:
            with open(os.getenv('nmap_services_file_location'), 'r', encoding="utf-8") as nmap_file:
                for line in nmap_file:
                    try:
                        _, ports, freqs = line.split("#", 1)[0].split(None, 3)
                        ports, proto = ports.split("/", 1)
                        port = int(ports)
                        freq = float(freqs)
                    except ValueError:
                        continue
                    self.ports.setdefault(proto, {})[port] = freq
                self.ports_dict_filled = True
        except (IOError, AttributeError, AssertionError) as openerror:
            logging.error("Error while opening Nmap services file: %s, with error: %s",
                            os.getenv('nmap_services_file_location'),
                            openerror)

    def NmapPortLogic(self, port1: int, port2: int, proto: str) -> int:
        """
        Function which checks with the NMAP common port file
        if it is a common port, and what is the probability.
        It returns: 1 when port1 is a server port. 0 when it cannot
        decide which is the server port. -1 when port2 is a server port.
        """
        if not self.ports_dict_filled:
            self.LoadNMAPServices()

        if self.ports_dict_filled:
            portlist = self.ports.get(proto.lower(), {})
            val1, val2 = portlist.get(int(port1), 0), portlist.get(int(port2), 0)
            cmpval = (val1 > val2) - (val1 < val2)
            if cmpval == 0:
                return (port2 > port1) - (port2 < port1)
        return cmpval

    def AddIPToList(self, ip_version, ip_address, port, proto) -> None:
        """
        Function to add IP address, with port number to the list.
        Checks if IP already exists. If so, it also checks if the
        port already exsists with that IP address.
        """
        port_tcp = None
        port_udp = None
        if proto == "TCP":
            port_tcp = port
        elif proto == "UDP":
            port_udp = port

        searchresult = next((item for item in self.ip_port_dict
                            if item["ipaddress"] == ip_address), None)

        if searchresult is None:
            if netaddr.IPAddress(ip_address) in self.surf_nets:
                temp_dict = {
                                "ip_version": ip_version,
                                "ipaddress": ip_address,
                                "portlist_tcp": [ ],
                                "portlist_udp": [ ]
                            }
                if port_tcp is not None:
                    temp_dict['portlist_tcp'] = [ port_tcp ]
                if port_udp is not None:
                    temp_dict['portlist_udp'] = [ port_udp ]
                self.ip_port_dict.append(temp_dict)
        else:
            if port_tcp:
                if not port_tcp in searchresult["portlist_tcp"]:
                    searchresult["portlist_tcp"].append(port_tcp)
            elif port_udp:
                if not port_udp in searchresult["portlist_udp"]:
                    searchresult["portlist_udp"].append(port_udp)
