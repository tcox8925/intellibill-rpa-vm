
from sqlalchemy.ext.declarative import declarative_base, declared_attr

class CustomBase:
	@declared_attr
	def __tablename__(cls):
		return cls.__name__.lower()

	__table_args__ = {'schema': 'wpo'}

Base = declarative_base(cls=CustomBase)
