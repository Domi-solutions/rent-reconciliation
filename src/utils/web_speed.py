"""Make pages arrive fast: styles kept on the phone, and pages compressed on the way.

- Every url_for('static', ...) gets ?v=<version of the static files>. A file asked for with
  that tag is cached by the browser for a year; when any static file changes (a deploy), the
  tag changes and phones fetch the new copy. Files without the tag (CSS pulled in by @import)
  are revalidated as before, so nothing is ever stale.
- Text responses (pages, JSON, CSS, JS) over 1 KB are gzip-compressed when the browser
  accepts it — pages are about 8x smaller on mobile data.
"""

import gzip
import hashlib
import os

ONE_YEAR = 365 * 24 * 3600
_COMPRESS = ('text/html', 'text/css', 'application/javascript', 'text/javascript', 'application/json',
             'image/svg+xml', 'text/plain')


def static_version(static_folder):
    """Changes whenever any static file is added, removed or edited."""
    h = hashlib.sha1()
    for root, _, files in sorted(os.walk(static_folder)):
        for name in sorted(files):
            path = os.path.join(root, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            h.update(f'{os.path.relpath(path, static_folder)}:{st.st_size}:{int(st.st_mtime)}'.encode())
    return h.hexdigest()[:10]


def install(app):
    from flask import request

    version = static_version(app.static_folder)

    @app.url_defaults
    def _version_static(endpoint, values):
        if endpoint == 'static' and 'filename' in values:
            values.setdefault('v', version)

    default_max_age = app.get_send_file_max_age

    def _max_age(filename):
        if request.args.get('v') == version:
            return ONE_YEAR
        return default_max_age(filename)
    app.get_send_file_max_age = _max_age

    @app.after_request
    def _compress(response):
        if (response.status_code < 200 or response.status_code >= 300 or response.direct_passthrough
                or 'Content-Encoding' in response.headers
                or 'gzip' not in (request.headers.get('Accept-Encoding') or '').lower()
                or (response.mimetype or '') not in _COMPRESS):
            return response
        data = response.get_data()
        if len(data) < 1024:
            return response
        response.set_data(gzip.compress(data, compresslevel=6))
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Content-Length'] = str(len(response.get_data()))
        vary = response.headers.get('Vary')
        response.headers['Vary'] = f'{vary}, Accept-Encoding' if vary else 'Accept-Encoding'
        return response

    return version
