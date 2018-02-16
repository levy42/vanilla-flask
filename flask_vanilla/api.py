from model import Role
from datetime import datetime, date
from sqlalchemy.orm import class_mapper, ColumnProperty
from sqlalchemy.exc import IntegrityError
import json
from flask import jsonify, request, g, abort, current_app
from model import Permission, BaseEntity
from validation import ModelValidationError
from . import db


def route(path, **options):
    """Works only for class (extends BaseAPI) methods"""

    def decorator(f):
        f.route = (path, options)
        return f

    return decorator


class BaseAPI:
    def register(self, api, prefix):
        method_list = [getattr(self.__class__, func) for func in
                       dir(self.__class__) if
                       callable(getattr(self.__class__, func))]

        for f in method_list:
            if hasattr(f, 'route'):
                path, options = f.route
                if not path.startswith('/'):
                    path = '/' + path
                api.add_url_rule(
                    f'/{prefix}{path}', f'{f.__name__}', f, **options
                )


class ModelAPI(BaseAPI):
    class Methods:
        GET = 1
        CREATE = 2
        UPDATE = 3
        DELETE = 4
        GET_LIST = 5
        DELETE_LIST = 6
        SOFT_DELETE = 7
        GET_DELETED = 8
        DEFAULT_ALL = [CREATE, UPDATE, SOFT_DELETE, GET, GET_LIST, DELETE_LIST,
                       DELETE]

    def check_permission(self, obj, action):
        obj.check_permission(action)

    def __init__(self, model_class, db=None, app=None, methods=(),
                 max_results=100, name=None, prefix=''):
        self.model = model_class
        self.name = name or self.model.__tablename__
        self.full_prefix = prefix + self.name
        self.max_results = max_results
        self.fields = [
            prop.key for prop in
            class_mapper(self.model).iterate_properties
            if isinstance(prop, ColumnProperty)
        ]
        self.methods = methods or ModelAPI.Methods.DEFAULT_ALL

        if app:
            self.app = app
            self.init_app(app)
            self.db = app.db
        else:
            self.db = db

    def init_app(self, app):
        self.register(app)

    def get(self, id):
        obj = self.model.query.get_or_404(id)
        self.check_permission(obj, Permission.READ)
        return jsonify(obj.to_api())

    def query_access_filter(self, query):
        """override this to add custom query filter"""
        return query

    def get_list(self):
        filters = request.args
        page = filters.get('page', type=int)
        per_page = filters.get('limit', type=int)
        sort_by = filters.get('sort_by')
        decs = filters.get('decs', default=False, type=bool)
        with_deleted = request.args.get('with-deleted', type=bool,
                                        default=False)
        query = self.model.query.with_access_check()

        if with_deleted and g.user.has_permission(Permission.READ_DELETED,
                                                  self.model):
            query = query.with_deleted()

        for name, value in filters.items():
            if name.endswith('-min'):
                field_name = name.split('-min')[0]
                if field_name in self.fields:
                    query = query.filter(
                        getattr(self.model, field_name) >= value)
            elif name.endswith('-max'):
                field_name = name.split('-max')[0]
                if field_name in self.fields:
                    query = query.filter(
                        getattr(self.model, field_name) <= value)
            elif name.endswith('-like'):
                field_name = name.split('-like')[0]
                if field_name in self.fields:
                    query = query.filter(
                        getattr(self.model, field_name).like(value))
            else:
                if name in self.fields:
                    query = query.filter(getattr(self.model, name) == value)

        query = self.query_access_filter(query)

        if sort_by:
            query = query.order_by(sort_by + ' desc' if decs else '')

        if page:
            query = query.paginate(page=page, per_page=per_page)
            return jsonify(
                {'items': [obj.to_api() for obj in query.items],
                 'pages': query.pages})

        return jsonify(
            [obj.to_api() for obj in query.limit(self.max_results).all()])

    def delete(self, id):
        obj = self.model.query.get_or_404(id)
        self.check_permission(obj, Permission.WRITE)
        obj.soft_delete(self.db.session)
        self.db.session.commit()
        self.app.log_user_action(obj, 'deleted')
        return 'DELETED'

    def hard_delete(self, id):
        obj = db.session.query(self.model).get(id)
        if not obj:
            abort(404)
        self.check_permission(obj, Permission.HARD_WRITE)
        db.session.delete(obj)
        self.db.session.commit()
        self.app.log_user_action(obj, 'deleted')
        return 'DELETED'

    def delete_all(self):
        deleted = []
        for obj_id in request.json.get('id_list', []):
            try:
                obj = db.session.query(self.model).get(id)
                if not obj:
                    continue
                self.check_permission(obj, Permission.HARD_WRITE)
                db.session.delete(obj)
                db.session.commit()
                deleted.append(obj_id)
            except Exception as e:
                self.app.logger.exeption(f'Failed to delete obj, id: {obj_id}')
        return json.dumps(deleted)

    def restore(self, id):
        obj = self.model.query.get_with_deleted(id)
        if not obj:
            abort(404)
        self.check_permission(obj, Permission.HARD_WRITE)
        self.pre_restore(obj)
        obj.deleted = False
        self.db.session.add(obj)
        self.db.session.commit()
        self.post_restore(obj)
        self.app.log_user_action(obj, 'restored')
        return jsonify(obj.to_api())

    def create(self):
        f"""HER{self.model}"""
        obj = self.model()
        obj.populate_from_request()
        self.check_permission(obj, Permission.WRITE)
        self.pre_create(obj)
        obj.validate_on_create()  # needed only for create
        self.db.session.add(obj)
        self.db.session.commit()
        self.post_create(obj)
        self.app.log_user_action(obj, 'created')
        return jsonify(obj.to_api())

    def update(self, id):
        obj = self.model.query.get_or_404(id)
        self.check_permission(obj, Permission.WRITE)
        self.pre_update(obj)
        obj.populate_from_request()
        obj.validate()
        self.db.session.add(obj)
        self.db.session.commit()
        self.post_update(obj)
        self.app.log_user_action(obj, 'updated')
        return jsonify(obj.to_api())

    def pre_create(self, obj):
        pass

    def post_create(self, obj):
        pass

    def pre_update(self, obj):
        pass

    def post_update(self, obj):
        pass

    def pre_delete(self, obj, hard=False):
        pass

    def post_delete(self, obj, hard=False):
        pass

    def pre_delete_all(self, ids):
        pass

    def post_delete_all(self, ids):
        pass

    def pre_restore(self, obj):
        pass

    def post_restore(self, obj):
        pass

    def check_if_is_unique(self, field, value):
        # value is a string, so it should be converted first
        col_type = getattr(self, field).type.python_type
        try:
            value = col_type(value)
        except ValueError:
            return 'Invalid value', 400
        return jsonify({'result': self.model.is_unique(field, value)})

    def register(self, api):
        super(ModelAPI, self).register(api, self.full_prefix)
        if ModelAPI.Methods.GET in self.methods:
            api.add_url_rule(
                f'/{self.full_prefix}/<int:id>', f'get_{self.name}',
                self.get, methods=['GET']
            )
        if ModelAPI.Methods.GET_LIST in self.methods:
            api.add_url_rule(
                f'/{self.full_prefix}/', f'get_{self.name}_list',
                self.get_list, methods=['GET']
            )
        if ModelAPI.Methods.SOFT_DELETE in self.methods:
            api.add_url_rule(
                f'/{self.full_prefix}/<int:id>', f'delete_{self.name}',
                self.delete, methods=['DELETE']
            )
        if ModelAPI.Methods.DELETE in self.methods:
            api.add_url_rule(
                f'/{self.full_prefix}/<int:id>/hard-delete',
                f'hard_delete_{self.name}',
                self.hard_delete, methods=['DELETE']
            )
        if ModelAPI.Methods.DELETE in self.methods:
            api.add_url_rule(
                f'/{self.full_prefix}/<int:id>/delete-all',
                f'delete_all_{self.name}',
                self.delete_all, methods=['DELETE']
            )
        if ModelAPI.Methods.SOFT_DELETE in self.methods:
            api.add_url_rule(
                f'/{self.full_prefix}/<int:id>/restore',
                f'restore_{self.name}',
                self.restore, methods=['POST']
            )
        if ModelAPI.Methods.CREATE in self.methods:
            api.add_url_rule(
                f'/{self.full_prefix}', f'create_{self.name}',
                self.create, methods=['POST']
            )
        if ModelAPI.Methods.UPDATE in self.methods:
            api.add_url_rule(
                f'/{self.full_prefix}/<int:id>', f'update_{self.name}',
                self.update, methods=['PUT']
            )
        api.add_url_rule(
            f'/{self.full_prefix}/<field>/is-unique/<value>',
            f'check_unique_{self.name}',
            self.check_if_is_unique, methods=['GET']
        )


class TenantAdminAPI(ModelAPI):
    def query_access_filter(self, query):
        return query.filter_by(tenant_id=g.user.tenant_id)

    def check_permission(self, obj=None, action=None):
        return g.user.has_role('tenant-admin')

    @route('/add-role/<int:user_id>/<role_name>', methods=['post'])
    def add_role(self, user_id, role_name):
        self.check_permission()
        user = self.app.User.query.get_or_404(user_id)
        role = Role.query.get_or_404(role_name)
        user.roles.append(role)
        self.db.session.add(user)
        self.db.session.commit()
        self.app.log_user_action(user, f'role added: {role_name}')

    @route('/remove-role/<int:user_id>/<role_name>', methods=['post'])
    def remove_role(self, user_id, role_name):
        self.check_permission()
        user = self.app.User.query.get_or_404(user_id)
        try:
            role = [r for r in user.roles if r.name == role_name][0]
        except KeyError:
            return
        user.roles.remove(role)
        self.db.session.add(user)
        self.db.session.commit()
        self.app.log_user_action(user, f'role removed: {role_name}')

    def register(self, api):
        super(TenantAdminAPI, self).register(api)


class SuperAdminAPI(ModelAPI):

    def query_access_filter(self, query):
        return query

    def check_permission(self, obj, action):
        return g.user.has_role('super-admin')


class RoleAPI(SuperAdminAPI):
    def __init__(self, db=None, app=None, methods=(),
                 max_results=100, name=None):
        super(RoleAPI, self).__init__(Role, app, max_results=max_results)


class PermissionsAPI(SuperAdminAPI):
    def __init__(self, db=None, app=None, methods=(),
                 max_results=100, name=None):
        super(PermissionsAPI, self).__init__(Permission, app,
                                             max_results=max_results)


def _get_entities():
    return [model for model in db.Model._decl_class_registry.values()
            if isinstance(model, type) and issubclass(model, BaseEntity)]


def init_error_handlers(app):
    @app.errorhandler(ModelValidationError)
    def model_validation(e):
        return json.dumps({'errors': e.errors}), 422

    @app.errorhandler(IntegrityError)
    def orm_fail(e):
        return f'Invalid entity', 422

    @app.errorhandler(ValueError)
    def model_modification_validation(e):
        return json.dumps({'error': str(e)}), 400


def init_user_modifications_tracking(app):
    class UserAction(app.db.Model):
        id = db.Column(db.Integer, primary_key=True, autoincrement=True)
        name = db.Column(db.String)
        datetime = db.Column(db.DateTime, default=datetime.now)
        message = db.Column(db.Text)
        entity = db.Column(db.String)
        user_id = db.Column(db.Integer)

    @app.user_action_handler
    def add_action(obj, action, message=None):
        app.db.session.add(
            UserAction(name=action, message=message, entity=obj.__tablename__,
                       user_id=g.user.id))
        app.db.session.commit()
