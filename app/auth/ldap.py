import re
import ssl
import logging
from ldap3 import Server, Connection, Tls, SUBTREE
from ldap3.utils.conv import escape_filter_chars

log = logging.getLogger(__name__)

# IPA usernames: letters, digits, dot, hyphen, underscore only
_SAFE_USERNAME = re.compile(r'^[a-zA-Z0-9._-]{1,64}$')


def ldap_authenticate(host, base_dn, ca_cert, username, password):
    """Bind as username against IPA LDAP. Returns dict of user attrs on success, None on failure."""
    if not _SAFE_USERNAME.match(username):
        log.info('LDAP: rejected username with disallowed characters: %r', username)
        return None

    user_dn = f'uid={username},cn=users,cn=accounts,{base_dn}'

    try:
        tls = Tls(ca_certs_file=ca_cert, validate=ssl.CERT_REQUIRED)
        server = Server(host, port=636, use_ssl=True, tls=tls)
        conn = Connection(server, user=user_dn, password=password)

        if not conn.bind():
            log.info('LDAP bind failed for %s: %s', username, conn.result)
            return None

        safe_uid = escape_filter_chars(username)
        conn.search(
            search_base=f'cn=users,cn=accounts,{base_dn}',
            search_filter=f'(uid={safe_uid})',
            search_scope=SUBTREE,
            attributes=['mail'],
        )

        email = f'{username}@cname.se'
        if conn.entries and conn.entries[0].mail:
            email = str(conn.entries[0].mail)

        conn.unbind()
        return {'email': email}

    except Exception:
        log.exception('LDAP error authenticating %s', username)
        return None
