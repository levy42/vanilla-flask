from flask_validator import Validator
from datetime import datetime
class ModelValidationError(Exception):
    def __init__(self, errors):
        self.errors = errors
        self.msg = 'Validation has been failed'

class MutableValidator(Validator):
    def transform(self, value):
        return value

    # hook over method protection
    def _FlaskValidator__validate(self, target, value, oldvalue, initiator):
        try:
            value = self.transform(value)
        except Exception:
            pass
        return super(MutableValidator, self)._FlaskValidator__validate(
            target, value, oldvalue, initiator)

class ValidateDate(MutableValidator):
    def transform(self, value):
        if isinstance(value, str):
            return datetime.strptime(value, "%Y-%m-%d")
        else:
            return value

    def check_value(self, value):
        # return isinstance(value, date)
        return True


class ValidateDateTime(MutableValidator):
    def transform(self, value):
        if isinstance(value, str):
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ")
        else:
            return value

    def check_value(self, value):
        return isinstance(value, datetime)