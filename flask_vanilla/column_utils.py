from sqlalchemy import Column
from sqlalchemy.orm.relationships import RelationshipProperty


class VanillaColumn(Column):
    def __init__(self, *args, protected=False, mutable=True, private=False,
                 **kwargs):
        super(VanillaColumn, self).__init__(*args, **kwargs)
        self.is_protected = protected
        self.is_mutable = mutable
        self.is_private = private

    def copy(self, *args, **kwargs):
        c = super(VanillaColumn, self).copy(*args, **kwargs)
        c.is_protected = self.is_protected
        c.is_mutable = self.is_mutable
        c.is_private = self.is_private
        return c


class VanillaRelationshipProperty(RelationshipProperty):
    def __init__(self, *args, protected=True, **kwargs):
        super(VanillaRelationshipProperty, self).__init__(*args, **kwargs)
        self.is_protected = protected
