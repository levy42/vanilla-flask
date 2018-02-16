from datetime import datetime, date
from logging.config import dictConfig
from sqlalchemy import (types,
                        TypeDecorator
                        )
import json
from flask import g, Flask, current_app
from json import JSONEncoder
from flask_cache import Cache
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
cache = Cache()


class Json(TypeDecorator):

    @property
    def python_type(self):
        return object

    impl = types.String

    def process_bind_param(self, value, dialect):
        return json.dumps(value, cls=VanillaJSONEncoder)

    def process_literal_param(self, value, dialect):
        return value

    def process_result_value(self, value, dialect):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return None


def default_tenant(context):
    return context.current_parameters['user'].tenant_id


def _init__default_logging_config(app):
    dictConfig({
        "version": 1,
        "disable_existing_loggers": 0,
        "root": {
            "level": "DEBUG",
            "handlers": [
                "console",
                "file",
            ]
        },
        "loggers": {

        },
        "formatters": {
            "precise": {
                "format": "%(asctime)s %(name)-15s %(levelname)-8s %(message)s"
            },
        },
        "handlers": {
            "console": {
                "formatter": "precise",
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "level": "DEBUG"
            },
            "file": {
                "formatter": "precise",
                "backupCount": 3,
                "level": "WARNING",
                "maxBytes": 10240000,
                "class": "logging.handlers.RotatingFileHandler",
                "filename": f"{app.name}.log"
            }
        }
    })


class UserMode:
    SIMPLE = 1
    MULTI_TENANT = 2


class VanillaJSONEncoder(JSONEncoder):
    def default(self, o):
        if isinstance(o, (date, datetime)):
            return o.isoformat()

        return super().default(o)


class FlaskVanilla(Flask):
    json_encoder = VanillaJSONEncoder

    def __init__(self, import_name, user_extension=None, tenant_extension=None,
                 user_action_tracking=True, user_mode=UserMode.SIMPLE,
                 default_logging=False, **kwargs):

        super(FlaskVanilla, self).__init__(
            import_name=import_name,
            **kwargs
        )

        self._default_configs(logging=default_logging)
        from column_utils import VanillaColumn, VanillaRelationshipProperty
        db.relationship = VanillaRelationshipProperty
        db.Column = VanillaColumn
        db.init_app(self)
        self.db = db
        self.models = []
        self.user_mode = user_mode

        class EmptyExtension:
            pass

        user_extension = user_extension or EmptyExtension
        tenant_extension = tenant_extension or EmptyExtension

        from api import SuperAdminAPI, TenantAdminAPI, init_error_handlers, ModelAPI  # noqa
        from model import TenantUser, UserBase, TenantBase  # noqa

        if user_mode == UserMode.MULTI_TENANT:
            class User(user_extension, TenantUser, db.Model):
                pass

            class Tenant(tenant_extension, TenantBase, db.Model):
                pass

            self.Tenant = Tenant
            self.User = User
            SuperAdminAPI(Tenant, app=self)
            TenantAdminAPI(User, app=self)

        else:
            class User(user_extension, UserBase, db.Model):
                pass

            self.User = User
            SuperAdminAPI(User, app=self)

        self.init_api()

        init_error_handlers(self)

        self.user_action_handlers = []

        if user_action_tracking:
            self.init_user_modifications_tracking()

    def add_model_rest_api(self, model):
        from api import ModelAPI
        ModelAPI(model, self.db).register(self)

    def init_api(self):
        from api import ModelAPI
        global MODELS
        for model in MODELS:
            ModelAPI(model, db, self)
            self.models.append(model)

    def log_user_action(self, obj, action):
        self.logger.info(f'{obj.__tablename__} {action}. User ID: {g.user.id}')
        for f in self.user_action_handlers:
            f(obj, action)

    def user_action_handler(self, f):
        self.user_action_handlers.append(f)

    def init_user_modifications_tracking(self):
        from api import init_user_modifications_tracking
        init_user_modifications_tracking(self)

    def entity_event(self, model, action):
        def wrapper(f):
            def handler(obj, _action):
                if obj.__tablename__ == model.__tablename__ \
                        and action == _action:
                    f(obj, _action)

            self.user_action_handlers.append(handler)

        return wrapper

    def _default_configs(self, logging=False):
        self.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{self.name}.db'
        self.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = True
        self.config['CACHE_TYPE'] = 'simple'
        if logging:
            _init__default_logging_config(self)


MODELS = []


def setup_cli(app):
    from model import DefaultRoles, Role
    @app.cli.command()
    def init_default_data():
        with current_app.app_context():
            for role in DefaultRoles.ALL:
                if not Role.query.get(role.name):
                    app.db.session.add(role)
            app.db.session.commit()


def generate_api(model_class):
    global MODELS
    MODELS.append(model_class)
    return model_class
