import ssl
import logging
from ldap3 import Server, Connection, Tls, SUBTREE

log = logging.getLogger(__name__)


def ldap_authenticate(host, base_dn, ca_cert, username, password):
    """Bind as username against IPA LDAP. Returns dict of user attrs on success, None on failure."""
    user_dn = f'uid={username},cn=users,cn=accounts,{base_dn}'

    try:
        tls = Tls(ca_certs_file=ca_cert, validate=ssl.CERT_REQUIRED)
        server = Server(host, port=636, use_ssl=True, tls=tls)
        conn = Connection(server, user=user_dn, password=password)

        if not conn.bind():
            log.info('LDAP bind failed for %s: %s', username, conn.result)
            return None

        conn.search(
            search_base=f'cn=users,cn=accounts,{base_dn}',
            search_filter=f'(uid={username})',
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
