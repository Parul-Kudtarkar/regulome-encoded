from future.standard_library import install_aliases
install_aliases()  # NOQA
import encoded.schema_formats # needed to import before snovault to add FormatCheckers
import base64
import codecs
import json
import netaddr
import os
try:
    import subprocess32 as subprocess  # Closes pipes on failure
except ImportError:
    import subprocess
from pyramid.config import Configurator
from pyramid.path import (
    AssetResolver,
    caller_package,
)
from pyramid.session import SignedCookieSessionFactory
from pyramid.settings import (
    aslist,
    asbool,
)
from sqlalchemy import engine_from_config
from sqlalchemy.ext.declarative import declarative_base
from webob.cookies import JSONSerializer
from snovault.elasticsearch import (
    PyramidJSONSerializer,
    TimedUrllib3HttpConnection,
)
from snovault.json_renderer import json_renderer
STATIC_MAX_AGE = 0


def json_asset(spec, **kw):
    utf8 = codecs.getreader("utf-8")
    asset = AssetResolver(caller_package()).resolve(spec)
    return json.load(utf8(asset.stream()), **kw)


def static_resources(config):
    from pkg_resources import resource_filename
    import mimetypes
    mimetypes.init()
    mimetypes.init([resource_filename('encoded', 'static/mime.types')])
    config.add_static_view('static', 'static', cache_max_age=STATIC_MAX_AGE)
    config.add_static_view('profiles', 'schemas', cache_max_age=STATIC_MAX_AGE)

    favicon_path = '/static/img/favicon.ico'
    if config.route_prefix:
        favicon_path = '/%s%s' % (config.route_prefix, favicon_path)
    config.add_route('favicon.ico', 'favicon.ico')

    def favicon(request):
        subreq = request.copy()
        subreq.path_info = favicon_path
        response = request.invoke_subrequest(subreq)
        return response

    config.add_view(favicon, route_name='favicon.ico')


def configure_engine(settings):
    engine_url = settings['sqlalchemy.url']
    engine_opts = {}
    if engine_url.startswith('postgresql'):
        application_name = 'app'
        engine_opts = dict(
            isolation_level='REPEATABLE READ',
            json_serializer=json_renderer.dumps,
            connect_args={'application_name': application_name}
        )
    engine = engine_from_config(settings, 'sqlalchemy.', **engine_opts)
    if engine.url.drivername == 'postgresql':
        timeout = settings.get('postgresql.statement_timeout')
        if timeout:
            timeout = int(timeout) * 1000
            set_postgresql_statement_timeout(engine, timeout)
    return engine


def set_postgresql_statement_timeout(engine, timeout=20 * 1000):
    """ Prevent Postgres waiting indefinitely for a lock.
    """
    from sqlalchemy import event
    import psycopg2

    @event.listens_for(engine, 'connect')
    def connect(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("SET statement_timeout TO %d" % timeout)
        except psycopg2.Error:
            dbapi_connection.rollback()
        finally:
            cursor.close()
            dbapi_connection.commit()


def configure_dbsession(config):
    settings = config.registry.settings
    DBSession = settings.pop("dbsession", None)
    if DBSession is None:
        engine = configure_engine(settings)

        if asbool(settings.get('create_tables', False)):
            Base = declarative_base()
            Base.metadata.create_all(engine)

        import zope.sqlalchemy
        from sqlalchemy import orm

        DBSession = orm.scoped_session(orm.sessionmaker(bind=engine))
        zope.sqlalchemy.register(DBSession)

    config.registry["dbsession"] = DBSession


def session(config):
    """ To create a session secret on the server:

    $ cat /dev/urandom | head -c 256 | base64 > session-secret.b64
    """
    settings = config.registry.settings
    if 'session.secret' in settings:
        secret = settings['session.secret'].strip()
        if secret.startswith('/'):
            secret = open(secret).read()
            secret = base64.b64decode(secret)
    else:
        secret = os.urandom(256)
    # auth_tkt has no timeout set
    # cookie will still expire at browser close
    if 'session.timeout' in settings:
        timeout = int(settings['session.timeout'])
    else:
        timeout = 60 * 60 * 24
    session_factory = SignedCookieSessionFactory(
        secret=secret,
        timeout=timeout,
        reissue_time=2**32,  # None does not work
        serializer=JSONSerializer(),
    )
    config.set_session_factory(session_factory)


def app_version(config):
    import hashlib
    import os
    import subprocess
    try:
        version = subprocess.check_output(
            ['git', '-C', os.path.dirname(__file__), 'describe']).decode('utf-8').strip()
        diff = subprocess.check_output(
            ['git', '-C', os.path.dirname(__file__), 'diff', '--no-ext-diff'])
        if diff:
            version += '-patch' + hashlib.sha1(diff).hexdigest()[:7]
    except:
        # Travis can't run git describe without crashing
        version = 'version_test'
    config.registry.settings['snovault.app_version'] = version


def main(global_config, **local_config):
    """ This function returns a Pyramid WSGI application.
    """
    settings = global_config
    settings.update(local_config)

    settings['snovault.jsonld.namespaces'] = json_asset('encoded:schemas/namespaces.json')
    settings['snovault.jsonld.terms_namespace'] = 'https://www.encodeproject.org/terms/'
    settings['snovault.jsonld.terms_prefix'] = 'encode'
    settings['snovault.elasticsearch.index'] = 'snovault'

    config = Configurator(settings=settings)
    config.include(app_version)

    config.include('pyramid_multiauth')  # must be before calling set_authorization_policy
    from pyramid_localroles import LocalRolesAuthorizationPolicy
    # Override default authz policy set by pyramid_multiauth
    config.set_authorization_policy(LocalRolesAuthorizationPolicy())
    config.include(session)
    config.include('.auth0')

    config.include(configure_dbsession)
    config.include('snovault')
    config.commit()  # commit so search can override listing

    # Render an HTML page to browsers and a JSON document for API clients
    config.include('.renderers')
    config.include('.authentication')
    config.include('.server_defaults')
    config.include('.types')
    config.include('.root')
    config.include('.batch_download')
    config.include('.visualization')

    config.include('.regulome_search')
    config.include('.regulome_help')
    config.include('encoded.viewconfigs.views')

    config.include(static_resources)

    app = config.make_wsgi_app()

    return app
