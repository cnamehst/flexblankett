import os
from datetime import timedelta


def _db_engine_options() -> dict:
    """TLS to the database when DB_SSL_CA names a CA file: the server
    certificate must chain to that CA and match the host name in DATABASE_URL.
    Unset: plain connection, as before. Set but missing: refuse to start
    rather than fall back to an unencrypted connection."""
    ca = os.environ.get('DB_SSL_CA', '')
    if not ca:
        return {}
    if not os.path.isfile(ca):
        raise RuntimeError(f'DB_SSL_CA is set to {ca} but that file does not exist')
    return {'connect_args': {'ssl': {'ca': ca}}}


class Config:
    # No dev fallback: a known default key would let anyone forge sessions.
    SECRET_KEY = os.environ['SECRET_KEY']
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'mysql+pymysql://flexuser:flexpass@localhost:3306/flexblankett'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _db_engine_options()
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MB — prevents large body DoS
    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_DURATION = timedelta(days=30)

    # Rate-limit counters (login brute-force protection). memory:// is
    # per-process, so with N gunicorn workers x M replicas the effective limit
    # is N*M times the configured one. Point at a shared redis:// in production.
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')
    RATELIMIT_KEY_PREFIX = 'flexblankett'
    # If the store is unreachable, keep limiting per-process instead of 500-ing.
    RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = True

    # Addresses of reverse proxies in front of Traefik (comma separated), as
    # Traefik sees them. Needed to tell internet clients apart; Traefik must
    # also trust the same addresses for X-Forwarded-For.
    TRUSTED_PROXIES = frozenset(
        a.strip() for a in os.environ.get('TRUSTED_PROXIES', '').split(',') if a.strip()
    )

    LDAP_ENABLED = os.environ.get('LDAP_ENABLED', 'false').lower() == 'true'
    LDAP_HOST = os.environ.get('LDAP_HOST', 'idm.cname.se')
    LDAP_BASE_DN = os.environ.get('LDAP_BASE_DN', 'dc=cname,dc=se')
    LDAP_CA_CERT = os.environ.get('LDAP_CA_CERT', '/etc/ssl/certs/ipa-ca.pem')
    LDAP_REQUIRED_GROUP = os.environ.get('LDAP_REQUIRED_GROUP', 'app-flexblankett')
