from datetime import datetime, date
from sqlalchemy import (
    Boolean, Integer, String, DateTime,
    ForeignKey, UniqueConstraint, inspect
)
from sqlalchemy.orm import validates
from sqlalchemy.sql.expression import true
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm.interfaces import MANYTOONE
import json
from flask import request, g, abort
from flask_validator import (ValidateNumeric, ValidateInteger, ValidateLength,
                             ValidateString)

from flask import json
from . import db
from .validation import ModelValidationError, ValidateDate, ValidateDateTime
from .query import QueryWithSoftDeleteAndAccess


class VersionMixin:
    version_id = db.Column(Integer, nullable=False)
    __mapper_args__ = {
        "version_id_col": version_id
    }


class BaseModel(object):
    id = db.Column(Integer, primary_key=True, autoincrement=True)
    created_at = db.Column(DateTime, default=datetime.now,
                           onupdate=datetime.now, protected=True)
    updated_at = db.Column(DateTime, default=datetime.now,
                           onupdate=datetime.now, protected=True)
    deleted_at = db.Column(DateTime, protected=True)
    deleted = db.Column(Boolean, default=False, server_default=true(),
                        nullable=False, protected=True)

    query_class = QueryWithSoftDeleteAndAccess

    def soft_delete(self, session):
        """Mark this object as deleted."""
        self.deleted = True
        self.deleted_at = datetime.now()
        session.add(self)

    def populate_from_request(self):
        self.populate(**json.loads(request.data))

    def populate(self, **data):
        data.pop('id', None)  # can be protected but better to exclude it
        if request:
            errors = {}
            saved = inspect(self).persistent
            for key, value in data.items():
                field = self.__table__.columns._data.get(key)
                if field is None or field.is_protected or field.is_private or (
                        saved and not field.is_mutable):
                    continue
                try:
                    setattr(self, key, value)
                except ValueError as e:
                    errors[key] = str(e)
            if errors:
                raise ModelValidationError(errors)
        else:
            for key, value in data.items():
                setattr(self, key, value)

    def as_dict(self):
        self.id  # lazy reload for __dict__, a bit of hack
        return {k: v for k, v in self.__dict__.items() if
                k != '_sa_instance_state'}

    def to_api(self, join_relations=True):
        self.id  # lazy reload for __dict__, a bit of hack
        public_cols = [col.name for col in self.__table__.columns
                       if not col.is_private]
        data = {k: v for k, v in self.as_dict().items() if
                k in public_cols}

        # whether to include relationships, example: include=posts,comments
        if request.args.get('include') and join_relations:
            for relation in request.args.get('include').split(','):
                try:
                    entry = getattr(self, relation)  # for lazy load
                    if isinstance(entry, BaseModel):
                        data[relation] = entry.to_api(
                            join_relations=False) \
                            if not entry.deleted and entry.check_permission(
                            Permission.READ, abort_on_fail=False) else None
                    elif isinstance(entry, list):
                        data[relation] = [i.to_api(join_relations=False) for i
                                          in entry if
                                          not i.deleted and i.check_permission(
                                              Permission.READ,
                                              abort_on_fail=False)]
                except AttributeError:
                    abort(400, f"No such relation: {relation}")
        return data

    @classmethod
    def access_filter(cls, query):
        return query

    @classmethod
    def __declare_last__(cls):
        for col in cls.__table__.columns:
            type = col.type.python_type

            if type == str:
                ValidateString(getattr(cls, col.name), allow_null=col.nullable,
                               throw_exception=True)
                if col.type.length:
                    ValidateLength(
                        getattr(cls, col.name), col.type.length,
                        throw_exception=True,
                        message=f'Max length is {col.type.length}')
            elif type == int:
                ValidateInteger(
                    getattr(cls, col.name),
                    allow_null=col.nullable, throw_exception=True)
            elif type == float:
                ValidateNumeric(
                    getattr(cls, col.name),
                    allow_null=col.nullable, throw_exception=True)
            elif type == date:
                ValidateDate(getattr(cls, col.name), allow_null=col.nullable,
                             throw_exception=True)
            elif type == datetime:
                ValidateDateTime(getattr(cls, col.name),
                                 allow_null=col.nullable, throw_exception=True)

        BaseModel.validators()

    @staticmethod
    def validators():
        """Put here your validators"""
        pass

    def _validate_not_null_columns(self):
        errors = {}
        public_cols = [col for col in self.__table__.columns
                       if not col.is_private]
        for col in public_cols:
            if not col.nullable and \
                    not col.default and \
                    not col.server_default \
                    and not col.autoincrement \
                    and not getattr(self, col.name):
                errors[col.name] = 'Should be specified'
        if errors:
            raise ModelValidationError(errors=errors)

    def validate_on_create(self):
        self._validate_not_null_columns()
        self.validate()

    def validate(self):
        pass

    def _check_permission(self, action):
        return True

    def check_permission(self, action, abort_on_fail=True):
        has_permission = self._check_permission(action)
        if not has_permission and abort_on_fail:
            abort(401)
        return has_permission


class AccessType:
    PRIVATE = 'private'
    PROTECTED = 'protected'
    TENANT_PUBLIC = 'tenant_public'
    PUBLIC = 'public'


class BaseEntity(BaseModel):
    @declared_attr
    def user(cls):
        return db.relationship('User')

    @declared_attr
    def user_id(cls):
        return db.Column(Integer, ForeignKey('user.id'))

    @classmethod
    def access_filter(cls, query):
        query = query.filter(
            (cls.access != AccessType.PRIVATE) | (
                    cls.user_id == g.user.id)
        )
        return query

    def populate_from_request(self):
        super(BaseEntity, self).populate_from_request()
        self.user_id = g.user.id

    def _check_permission(self, action):
        if not g.user:
            return False
        if g.user.has_role(DefaultRoles.SUPER_ADMIN.name):
            return True

        if g.user.id != self.user_id:
            if self.access != AccessType.PUBLIC or action != Permission.READ:
                return False

        return True

    def is_unique(self, field, value):
        _filter = {field: value, 'user_id': g.user.id}
        return not bool(self.__class__.query.filter_by(
            **_filter).count())

    def _verify_relationships(self):
        for name, rel in inspect(self.__class__).relationships.items():
            if not rel.is_protected or rel.direction != MANYTOONE:
                continue

            rel_column = list(rel._calculated_foreign_keys)[0]
            if rel_column.is_protected or rel_column.is_private:
                continue
            rel_class = rel.mapper.class_
            if not issubclass(rel_class, BaseEntity):
                continue
            unverified_id = getattr(self, rel_column.name)
            if not unverified_id:
                continue
            obj = rel_class.query.get(unverified_id)
            if not obj:
                raise ModelValidationError(errors={
                    rel_column:
                        f'{unverified_id} : object with such id not found'
                })
            else:
                obj.check_permission(Permission.WRITE)

    def _verify_relationships_old(self):
        for name, rel in inspect(self.__class__).relationships.items():
            if rel.direction != MANYTOONE:
                continue
            rel_column = list(rel._calculated_foreign_keys)[0]
            if rel_column.is_protected or rel_column.is_private:
                continue
            rel_class = rel.mapper.class_
            if not issubclass(rel_class, BaseEntity):
                continue
            if rel_class not in self.__verify_relationships_list__:
                continue
            unverified_id = getattr(self, rel_column.name)
            if not unverified_id:
                continue
            obj = rel_class.query.get(unverified_id)
            if not obj:
                raise ModelValidationError(errors={
                    rel_column:
                        f'{unverified_id} : object with such id not found'
                })
            else:
                obj.check_permission(Permission.WRITE)

    def validate_on_create(self):
        self._validate_not_null_columns()
        self._verify_relationships()
        self.validate()

    access = db.Column(String, default=AccessType.TENANT_PUBLIC)


class UniqueNameEntity(BaseEntity):
    """Should only extends BaseEntity"""
    __table_args__ = (UniqueConstraint('name', 'user_id',
                                       name='_unique_name_user'),)

    @declared_attr
    def name(cls):
        return db.Column(String, nullable=False)

    @validates('name')
    def validate_name(self, key, name):
        # consider 'self' to be a BaseEntity subclass
        if self.name == name:
            return name
        if not self.is_unique('name', name):
            raise ModelValidationError(errors={'name': 'Already taken'})
        return name


class BaseMultiTenantEntity(BaseEntity):

    @declared_attr
    def tenant_id(cls):
        return db.Column(Integer, ForeignKey('tenant.id'))

    @declared_attr
    def tenant(cls):
        return db.relationship('Tenant')

    def populate_from_request(self):
        super(BaseMultiTenantEntity, self).populate_from_request()
        self.user_id = g.user.id
        self.tenant_id = g.user.tenant_id

    def _check_permission(self, action):
        if not g.user:
            return False
        if g.user.has_role(DefaultRoles.SUPER_ADMIN.name):
            return True
        if g.user.has_role(DefaultRoles.TENANT_ADMIN.name):
            return True
        if not (g.user.has_permission('ALL') or g.user.has_permission(
                action, self.__tablename__)):
            return False
        if g.user.id != self.user_id:
            if self.access == AccessType.PRIVATE:
                return False
            elif self.access == AccessType.PROTECTED and \
                    action != Permission.READ:
                return False

        return True

    def is_unique(self, field, value):
        _filter = {field: value, 'tenant_id': g.user.tenant_id}
        return not bool(self.__class__.query.filter_by(
            **_filter).count())

    @classmethod
    def access_filter(cls, query):
        query = query.filter_by(tenant_id=g.user.tenant_id)
        query = query.filter(
            (cls.access != AccessType.PRIVATE) | (
                    cls.user_id == g.user.id)
        )
        return query


class UniqueNameTenantEntity(BaseMultiTenantEntity):
    """Should only extends BaseMultiTenantEntity"""
    __table_args__ = (UniqueConstraint('name', 'tenant_id',
                                       name='_unique_name_tenant'),)

    @declared_attr
    def name(cls):
        return db.Column(String)

    @validates('name')
    def validate_name(self, key, name):
        # consider 'self' to be a BaseMultiTenantEntity subclass
        if self.name == name:
            return name
        if not self.is_unique('name', name):
            raise ModelValidationError(errors={'name': 'Already taken'})
        return name


user_to_role = db.Table(
    'user_to_role', db.Model.metadata,
    db.Column('user_id', Integer, ForeignKey('user.id')),
    db.Column('role_name', String, ForeignKey('role.name'))
)


class UserBase(BaseModel):
    @declared_attr
    def roles(cls):
        return db.relationship('Role', secondary=user_to_role, lazy='joined')

    def has_role(self, role):
        name = role if isinstance(role, str) else role.name
        return name in [r.name for r in self.roles]

    def has_permission(self, action, model=None):
        return bool(action in self.permissions.get(model, []))

    @property
    def permissions(self):
        permissions = {}
        for role in self.roles:
            for p in role.permissions:
                if not permissions.get(p.model):
                    permissions[p.model] = set(p.type)
                else:
                    permissions[p.model].add(p.type)
        return permissions

    def to_api(self, join_relations=True):
        data = super(UserBase, self).to_api(join_relations=join_relations)
        data['roles'] = [r.name for r in self.roles]
        return data


class TenantUser(UserBase):
    @declared_attr
    def tenant_id(cls):
        return db.Column(Integer, ForeignKey('tenant.id'))

    @declared_attr
    def tenant(cls):
        return db.relationship('Tenant')


class Role(BaseModel, db.Model):
    name = db.Column(String(length=50), primary_key=True)
    description = db.Column(db.Text())
    permissions = db.relationship('Permission')

    def id(self):
        # for compatibility
        return self.name

    def to_api(self, join_relations=True):
        data = super(Role, self).to_api(join_relations=join_relations)
        data['permissions'] = [p.to_api() for p in self.permissions]

    def populate_from_request(self):
        super(Role, self).populate_from_request()
        for permission_id in request.json['permissions']:
            permission = Permission.query.get(permission_id)
            if permission:
                Role.permissions.append(permission)


class Permission(BaseModel, db.Model):
    # Default permissions
    READ = 'READ'
    WRITE = 'WRITE'
    HARD_WRITE = 'HARD WRITE'
    READ_DELETED = 'READ_DELETED'
    SUPER_ADMIN = 'SUPER_ADMIN'

    id = db.Column(Integer, primary_key=True, autoincrement=True)
    type = db.Column(String)
    model = db.Column(String, default='ALL')
    role_name = db.Column(Integer, ForeignKey('role.name'))
    role = db.relationship('Role')

    def __eq__(self, other):
        return self.type == other.type and self.model == other.model

    def __hash__(self):
        return hash(self.type + (self.model or ""))


class DefaultRoles:
    SUPER_ADMIN = Role(name='super-admin', permissions=[
        Permission(type=Permission.READ),
        Permission(type=Permission.WRITE),
        Permission(type=Permission.HARD_WRITE),
        Permission(type=Permission.SUPER_ADMIN),
        Permission(type=Permission.READ_DELETED)])
    TENANT_ADMIN = Role(name='tenant-admin', permissions=[
        Permission(type=Permission.READ),
        Permission(type=Permission.WRITE),
        Permission(type=Permission.HARD_WRITE)])
    USER = Role(name='user', permissions=[
        Permission(type=Permission.READ),
        Permission(type=Permission.WRITE)])
    GUEST = Role(name='guest', permissions=[
        Permission(type=Permission.READ)])

    ALL = [SUPER_ADMIN, TENANT_ADMIN, USER, GUEST]


class TenantBase(BaseModel):
    @declared_attr
    def users(cls):
        return db.relationship('User')
