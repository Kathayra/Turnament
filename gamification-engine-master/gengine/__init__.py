# -*- coding: utf-8 -*-
from pyramid.events import NewRequest

from gengine.base.context import reset_context
from gengine.base.errors import APIError
from gengine.base.settings import set_settings

__version__ = '0.2.2'

import datetime

import os
from pyramid.config import Configurator
from pyramid.renderers import JSON
from pyramid.settings import asbool
from sqlalchemy import engine_from_config

from gengine.wsgiutil import HTTPSProxied, init_reverse_proxy


def main(global_config, **settings):
    """ This function returns a Pyramid WSGI application.
    """
    
    durl = os.environ.get("DATABASE_URL") #heroku
    if durl:
        settings['sqlalchemy.url']=durl
        
    murl = os.environ.get("MEMCACHED_URL") #heroku
    if murl:
        settings['urlcache_url']=murl

    set_settings(settings)

    engine = engine_from_config(settings, 'sqlalchemy.', connect_args={"options": "-c timezone=utc"}, )
    config = Configurator(settings=settings)
    
    from gengine.app.cache import init_caches
    init_caches()

    from gengine.metadata import init_session, init_declarative_base, init_db

    init_session()
    init_declarative_base()
    init_db(engine)

    from gengine.base.monkeypatch_flaskadmin import do_monkeypatch
    do_monkeypatch()

    def reset_context_on_new_request(event):
        reset_context()
    config.add_subscriber(reset_context_on_new_request,NewRequest)
    config.include('pyramid_dogpile_cache')

    config.include("pyramid_tm")
    config.include('pyramid_chameleon')
    
    urlprefix = settings.get("urlprefix","")
    urlcacheid = settings.get("urlcacheid","gengine")
    force_https = asbool(settings.get("force_https",False))
    init_reverse_proxy(force_https,urlprefix)
    
    urlcache_url = settings.get("urlcache_url","127.0.0.1:11211")
    urlcache_active = asbool(os.environ.get("URLCACHE_ACTIVE", settings.get("urlcache_active",True)))

	#auth
    def get_user(request):
        if not asbool(settings.get("enable_user_authentication",False)):
            return None
        token = request.headers.get('X-Auth-Token')
        if token is not None:
            from gengine.app.model import DBSession, AuthUser, AuthToken
            tokenObj = DBSession.query(AuthToken).filter(AuthToken.token==token).first()
            user = None
            if tokenObj and tokenObj.valid_until<datetime.datetime.utcnow():
                tokenObj.extend()
            if tokenObj:
                user = tokenObj.user
            if not user:
                raise APIError(401, "invalid_token", "Invalid token provided.")
            if not user.active:
                raise APIError(404, "user_is_not_activated", "Your user is not activated.")
            return user
        return None

    def get_permissions(request):
        if not asbool(settings.get("enable_user_authentication", False)):
            return []
        from gengine.app.model import DBSession, t_auth_tokens, t_auth_users, t_auth_roles, t_auth_roles_permissions, t_auth_users_roles
        from sqlalchemy.sql import select
        j = t_auth_tokens.join(t_auth_users).join(t_auth_users_roles).join(t_auth_roles).join(t_auth_roles_permissions)
        q = select([t_auth_roles_permissions.c.name],from_obj=j).where(t_auth_tokens.c.token==request.headers.get("X-Auth-Token"))
        return [r["name"] for r in DBSession.execute(q).fetchall()]

    def has_perm(request, name):
        return name in request.permissions

    config.add_request_method(get_user, 'user', reify=True)
    config.add_request_method(get_permissions, 'permissions', reify=True)
    config.add_request_method(has_perm, 'has_perm')

    #routes
    from gengine.app.route import config_routes as config_app_routes

    config.include(config_app_routes, route_prefix=urlprefix)

    config.add_route('admin_app', '/admin/*subpath')

    from gengine.app.admin import init_admin as init_tenantadmin
    init_tenantadmin(urlprefix=urlprefix,
                     secret=settings.get("flaskadmin_secret","fKY7kJ2xSrbPC5yieEjV"))

    #date serialization
    json_renderer = JSON()
    def datetime_adapter(obj, request):
        return obj.isoformat()
    json_renderer.add_adapter(datetime.datetime, datetime_adapter)
    config.add_renderer('json', json_renderer)
    
    config.scan()
    
    return HTTPSProxied(config.make_wsgi_app())
