"""Adapters around bundled vendor implementations."""

from .gateway import call_vendor, load_vendor_server

__all__ = ["call_vendor", "load_vendor_server"]
