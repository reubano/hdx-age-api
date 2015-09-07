# -*- coding: utf-8 -*-
"""
    app.utils
    ~~~~~~~~~

    Provides misc utility functions
"""
from __future__ import (
    absolute_import, division, print_function, with_statement,
    unicode_literals)

import itertools as it
import requests

from json import dumps, loads, JSONEncoder
from ast import literal_eval
from datetime import datetime as dt
from bisect import bisect
from operator import itemgetter
from functools import partial
from random import choice
from timeit import default_timer as timer
from dateutil.relativedelta import relativedelta

from flask import make_response, request
from ckanutils import CKAN
from tabutils import process as tup

encoding = 'utf8'
statuses = ['Up-to-date', 'Due for update', 'Overdue', 'Delinquent']

breakpoints = {
    0: [0, 0, 0],
    1: [1, 2, 3],
    7: [7, 14, 21],
    14: [14, 21, 28],
    30: [30, 44, 60],
    90: [90, 120, 150],
    180: [180, 210, 240],
    365: [365, 425, 455],
}

categories = {
    0: 'Archived',
    1: 'Every day',
    7: 'Every week',
    14: 'Every two weeks',
    30: 'Every month',
    90: 'Every three months',
    180: 'Every six months',
    365: 'Every year',
}


class CustomEncoder(JSONEncoder):
    def default(self, obj):
        if set(['quantize', 'year']).intersection(dir(obj)):
            return str(obj)
        elif set(['next', 'union']).intersection(dir(obj)):
            return list(obj)
        return JSONEncoder.default(self, obj)


def jsonify(status=200, indent=2, sort_keys=True, **kwargs):
    options = {'indent': indent, 'sort_keys': sort_keys, 'ensure_ascii': False}
    response = make_response(dumps(kwargs, cls=CustomEncoder, **options))
    response.headers['Content-Type'] = 'application/json; charset=utf-8'
    response.headers['mimetype'] = 'application/json'
    response.status_code = status
    return response


def gen_elapsed(end, start):
    # http://stackoverflow.com/a/11157649/408556
    # http://stackoverflow.com/a/25823885/408556
    attrs = ['years', 'months', 'days', 'hours', 'minutes', 'seconds']
    elapsed = end - start
    delta = relativedelta(seconds=elapsed)

    for attr in attrs:
        value = getattr(delta, attr)

        if value:
            yield '%d %s' % (value, attr if value > 1 else attr[:-1])


def patch_or_post(endpoint, record):
    url = '%s/%s' % (endpoint, record['dataset_id'])
    headers = {'Content-Type': 'application/json'}
    data = dumps(record)

    if requests.head(url, headers=headers).ok:
        r = requests.patch(url, data=data, headers=headers)
    else:
        r = requests.post(endpoint, data=data, headers=headers)

    return r


def make_cache_key(*args, **kwargs):
    return request.url


def parse(string):
    string = string.encode(encoding)

    if string.lower() in ('true', 'false'):
        return loads(string.lower())
    else:
        try:
            return literal_eval(string)
        except (ValueError, SyntaxError):
            return string


def gen_data(ckan, pids, mock_freq=False):
    for pid in pids:
        package = ckan.package_show(id=pid)
        resources = package['resources']

        if not resources:
            continue

        downloads = sum(int(r['tracking_summary']['total']) for r in resources)

        if mock_freq:
            frequency = choice(breakpoints.keys())
        else:
            frequency = int(package.get('data_update_frequency'))

        breaks = breakpoints.get(frequency)
        last_updated = max(it.imap(ckan.get_update_date, resources))
        age = dt.now() - last_updated

        if breaks:
            status = statuses[bisect(breaks, age.days)]
        else:
            status = 'Invalid frequency'

        data = {
            'dataset_id': package['id'],
            'dataset_name': package['name'],
            'dataset_title': package['title'],
            'last_updated': last_updated.isoformat(),
            'needs_update': status in statuses[1:],
            'status': status,
            'age': int(age.days),
            'frequency': frequency,
            'frequency_category': categories.get(frequency),
            'downloads': downloads
        }

        yield data


def update(endpoint, **kwargs):
    start = timer()
    pid = kwargs.pop('pid', None)
    chunk_size = kwargs.get('chunk_size')
    row_limit = kwargs.get('row_limit')
    err_limit = kwargs.get('err_limit')

    rows = 0
    ckan = CKAN(**kwargs)

    if pid:
        pids = [pid]
    else:
        org_show = partial(ckan.organization_show, include_datasets=True)
        orgs_basic = ckan.organization_list(permission='read')
        org_ids = it.imap(itemgetter('id'), orgs_basic)
        orgs = (org_show(id=org_id) for org_id in org_ids)
        package_lists = it.imap(itemgetter('packages'), orgs)
        pid_getter = partial(map, itemgetter('id'))
        pids = it.chain.from_iterable(it.imap(pid_getter, package_lists))

    data = gen_data(ckan, pids, kwargs.get('mock_freq'))
    errors = {}

    for records in tup.chunk(data, min(row_limit or 'inf', chunk_size)):
        rs = map(partial(patch_or_post, endpoint), records)
        rows += len(filter(lambda r: r.ok, rs))
        ids = map(itemgetter('dataset_id'), records)
        errors.update(dict((k, r.json()) for k, r in zip(ids, rs) if not r.ok))

        if row_limit and rows >= row_limit:
            break

        if err_limit and len(errors) >= err_limit:
            raise Exception(errors)

    elapsed_time = ' ,'.join(gen_elapsed(timer(), start))
    return {'rows_added': rows, 'errors': errors, 'elapsed_time': elapsed_time}


def count_letters(word=''):
    return len(word)


def expensive_func(x):
    return x * 10
