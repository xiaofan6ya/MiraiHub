"""Discover LAN IPv4 addresses without contacting an external service."""
import ipaddress
import os
import socket
import struct

_LAN = tuple(ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def lan_ipv4():
    candidates = set()
    try:
        candidates.update(row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    if os.name != 'nt':
        try:
            import fcntl
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                for _, name in socket.if_nameindex():
                    try:
                        result = fcntl.ioctl(sock.fileno(), 0x8915, struct.pack('256s', name.encode()[:15]))
                        candidates.add(socket.inet_ntoa(result[20:24]))
                    except OSError:
                        continue
        except (ImportError, OSError):
            pass
    # UDP connect selects a route locally; it does not transmit any packets.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('192.0.2.1', 9))
            candidates.add(sock.getsockname()[0])
    except OSError:
        pass
    addresses = set()
    for value in candidates:
        try:
            address = ipaddress.ip_address(value)
            if any(address in network for network in _LAN):
                addresses.add(address)
        except ValueError:
            continue
    return [str(address) for address in sorted(addresses)]