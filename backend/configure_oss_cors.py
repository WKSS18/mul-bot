"""Append exact browser origins to OSS CORS, preserving existing rules."""
import argparse

import oss2
from oss2.models import BucketCors, CorsRule

from app.config import settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('origins', nargs='+', help='Exact http(s) origins, no trailing slash')
    args = parser.parse_args()
    if not (settings.oss_access_key_id and settings.oss_access_key_secret):
        raise SystemExit('OSS credentials missing')
    for origin in args.origins:
        if not origin.startswith(('https://', 'http://localhost:', 'http://127.0.0.1:')) or origin.endswith('/'):
            raise SystemExit(f'Invalid origin: {origin}')
    auth = oss2.AuthV4(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket, region=settings.oss_region)
    try:
        rules = list(bucket.get_bucket_cors().rules)
    except oss2.exceptions.NoSuchCors:
        rules = []
    existing = {origin for rule in rules for origin in rule.allowed_origins}
    missing = [origin for origin in args.origins if origin not in existing]
    if not missing:
        print('OSS CORS already contains all requested origins')
        return
    rules.append(CorsRule(allowed_origins=missing, allowed_methods=['POST', 'GET', 'HEAD'], allowed_headers=['*'], expose_headers=['ETag', 'x-oss-request-id'], max_age_seconds=600))
    bucket.put_bucket_cors(BucketCors(rules))
    updated = bucket.get_bucket_cors()
    now = {origin for rule in updated.rules for origin in rule.allowed_origins}
    if not set(missing).issubset(now):
        raise RuntimeError('CORS verification failed')
    print('OSS CORS configured for:', ', '.join(missing))


if __name__ == '__main__':
    main()
