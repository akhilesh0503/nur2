"""Shared HTTP retry/backoff helper for every network call in the pipeline.

Retries on connection/timeout errors and retryable HTTP statuses (rate
limiting, server errors). Does NOT retry other 4xx errors (e.g. 404) since
retrying a permanent client error just wastes time and delays surfacing the
real problem.
"""
import time

import requests

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _request_retry(method, url, timeout=30, max_retries=4, backoff_base=1.5, **kwargs):
    last_exc = None
    for attempt in range(max_retries):
        try:
            r = requests.request(method, url, timeout=timeout, **kwargs)
            if r.status_code in RETRYABLE_STATUS:
                raise requests.exceptions.HTTPError(f"retryable HTTP {r.status_code}", response=r)
            r.raise_for_status()
            return r
        except requests.exceptions.RequestException as e:
            last_exc = e
            is_http_error = isinstance(e, requests.exceptions.HTTPError)
            is_retryable = not is_http_error or (
                e.response is not None and e.response.status_code in RETRYABLE_STATUS
            )
            if not is_retryable or attempt == max_retries - 1:
                raise
            time.sleep(backoff_base ** attempt)
    raise last_exc


def _get_retry(url, params=None, timeout=30, max_retries=4, backoff_base=1.5):
    return _request_retry("GET", url, timeout, max_retries, backoff_base, params=params)


def get_json_retry(url, params=None, timeout=30, max_retries=4, backoff_base=1.5):
    return _get_retry(url, params, timeout, max_retries, backoff_base).json()


def post_json_retry(url, data=None, timeout=30, max_retries=4, backoff_base=1.5):
    return _request_retry("POST", url, timeout, max_retries, backoff_base, data=data).json()


def get_text_retry(url, params=None, timeout=30, max_retries=4, backoff_base=1.5):
    return _get_retry(url, params, timeout, max_retries, backoff_base).text


def get_bytes_retry(url, params=None, timeout=60, max_retries=4, backoff_base=1.5):
    return _get_retry(url, params, timeout, max_retries, backoff_base).content
