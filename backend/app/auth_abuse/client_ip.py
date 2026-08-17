"""Trusted-proxy-aware client address resolution for security controls."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network

from fastapi import Request

_Address = IPv4Address | IPv6Address
_Network = IPv4Network | IPv6Network


class ClientIpResolver:
    """Trust forwarded addresses only when the immediate peer is explicitly trusted."""

    def __init__(self, trusted_proxy_cidrs: tuple[str, ...] = ()) -> None:
        """Parse the explicitly configured direct-proxy networks."""
        self._trusted_networks: tuple[_Network, ...] = tuple(
            ip_network(cidr, strict=False) for cidr in trusted_proxy_cidrs
        )

    def __call__(self, request: Request) -> str:
        """Return a canonical direct or trusted-chain client address."""
        direct_host = request.client.host.strip() if request.client is not None else "unknown"
        try:
            direct_address = ip_address(direct_host)
        except ValueError:
            return direct_host.casefold() or "unknown"
        if not self._is_trusted(direct_address):
            return str(direct_address)

        forwarded = request.headers.get("x-forwarded-for", "")
        if not forwarded.strip():
            return str(direct_address)
        try:
            chain = tuple(ip_address(value.strip()) for value in forwarded.split(",") if value.strip())
        except ValueError:
            return str(direct_address)
        if not chain:
            return str(direct_address)
        for candidate in reversed(chain):
            if not self._is_trusted(candidate):
                return str(candidate)
        return str(chain[0])

    def _is_trusted(self, address: _Address) -> bool:
        return any(address.version == network.version and address in network for network in self._trusted_networks)
