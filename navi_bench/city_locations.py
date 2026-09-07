"""Canonical location/timezone metadata for well-known cities.

Shared by domain matchers (opentable, resy) that each build their own ``CITY_METADATA``-shaped
lookup keyed by their own domain-specific city spelling (e.g. resy's lowercase ``"new york"``/
``"sf"`` with an extra ``city_slug`` field vs opentable's ``"NYC"``/``"SF"``/``"Boston"``/
``"Los Angeles"`` with no slug). The two dicts previously hardcoded the identical
``location``/``timezone`` pair for San Francisco and for New York independently, so the strings
could drift out of sync if only one of the two files were ever updated. Centralizing just that
overlapping (location, timezone) data here removes the duplication without imposing a single key
vocabulary on either caller -- each file still keys its own dict however it needs to.
"""

SAN_FRANCISCO = {"location": "San Francisco, CA, United States", "timezone": "America/Los_Angeles"}
NEW_YORK = {"location": "New York, NY, United States", "timezone": "America/New_York"}
BOSTON = {"location": "Boston, MA, United States", "timezone": "America/New_York"}
LOS_ANGELES = {"location": "Los Angeles, CA, United States", "timezone": "America/Los_Angeles"}
