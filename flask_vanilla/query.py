from flask_sqlalchemy import BaseQuery
from flask import request
from flask import current_app
from sqlalchemy.orm import joinedload

db = current_app.db # noqa

class QueryWithSoftDelete(BaseQuery):
    def __new__(cls, *args, **kwargs):
        obj = super(QueryWithSoftDelete, cls).__new__(cls)
        with_deleted = kwargs.pop('_with_deleted', False)
        if len(args) > 0:
            super(QueryWithSoftDelete, obj).__init__(*args, **kwargs)
            return obj.filter_by(deleted=False) if not with_deleted else obj
        return obj

    def __init__(self, *args, **kwargs):
        pass

    def with_deleted(self):
        return self.__class__(db.class_mapper(self._mapper_zero().class_),
                              session=db.session(), _with_deleted=True)

    def _get(self, *args, **kwargs):
        # this calls the original query.get function from the base class
        return super(QueryWithSoftDelete, self).get(*args, **kwargs)

    def get(self, *args, **kwargs):
        # the query.get method does not like it if there is a filter clause
        # pre-loaded, so we need to implement it using a workaround
        obj = self.with_deleted()._get(*args, **kwargs)
        return obj if obj is not None and not obj.deleted else None


class QueryWithSoftDeleteAndAccess(BaseQuery):
    _with_deleted = False
    _with_access_check = False

    def __new__(cls, *args, **kwargs):
        obj = super(QueryWithSoftDeleteAndAccess, cls).__new__(cls)
        obj._with_deleted = kwargs.pop('_with_deleted', False)
        obj._with_access_check = kwargs.pop('_with_access_check', False)
        if len(args) > 0:
            super(QueryWithSoftDeleteAndAccess, obj).__init__(*args, **kwargs)
            obj = obj.filter_by(
                deleted=False) if not obj._with_deleted else obj
            if request and obj._with_access_check:
                for entity in obj._entities:
                    obj = entity.mapper.class_.access_filter(obj)
            if request and request.args.get('include'):
                join_list = request.args.get('include').split(',')
                options = []
                for join_entry in join_list:
                    options.append(joinedload(join_entry))
                obj = obj.options(options)
        return obj

    def __init__(self, *args, **kwargs):
        pass

    def with_deleted(self):
        return self.__class__(db.class_mapper(self._mapper_zero().class_),
                              session=db.session(), _with_deleted=True)

    def with_access_check(self):
        return self.__class__(db.class_mapper(self._mapper_zero().class_),
                              session=db.session(), _with_access_check=True)

    def raw(self):
        return self.__class__(db.class_mapper(self._mapper_zero().class_),
                              session=db.session(), _with_deleted=True,
                              _with_access_check=False)

    def _get(self, *args, **kwargs):
        # this calls the original query.get function from the base class
        return super(QueryWithSoftDeleteAndAccess, self).get(*args, **kwargs)

    def get(self, *args, **kwargs):
        # the query.get method does not like it if there is a filter clause
        # pre-loaded, so we need to implement it using a workaround
        obj = self.with_deleted()._get(*args, **kwargs)
        return obj if obj is None or self._with_deleted or not \
            obj.deleted else None

    def get_with_deleted(self, *args, **kwargs):
        return self.with_deleted()._get(*args, **kwargs)
