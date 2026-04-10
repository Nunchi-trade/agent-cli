class VenueCircuitBreakerOpen(Exception):
    """Raised when a venue adapter decides trading must halt due to repeated API failures."""
