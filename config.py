import os


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'mysql+pymysql://flexuser:flexpass@localhost:3306/flexblankett'
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MB — prevents large body DoS
    WTF_CSRF_ENABLED = True
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True

    LDAP_ENABLED = os.environ.get('LDAP_ENABLED', 'false').lower() == 'true'
    LDAP_HOST = os.environ.get('LDAP_HOST', 'ipa.cname.se')
    LDAP_BASE_DN = os.environ.get('LDAP_BASE_DN', 'dc=cname,dc=se')
    LDAP_CA_CERT = os.environ.get('LDAP_CA_CERT', '/etc/ssl/certs/ipa-ca.pem')
