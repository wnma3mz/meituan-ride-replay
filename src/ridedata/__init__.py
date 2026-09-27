"""Fetch Meituan bike history and prepare it for rendering.

The fetch and prepare stages write JSON under the data directory; `ridevideo`
reads it from there.  Credentials live only in the cURL/HAR inputs and are never
written to any output file.
"""
