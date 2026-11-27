import os

# config.Config reads the environment when it is imported.
os.environ.setdefault('SECRET_KEY', 'test-only')
os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('TRUSTED_PROXIES', '192.168.203.9')
