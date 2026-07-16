from app.models.BaseClasses import Base
from sqlalchemy import Column, String

class LupZipDatabase(Base):
    __tablename__ = "lup_zip_database"
    __table_args__ = {"schema": "wpo"}

    state_code = Column(String(10))
    state_name = Column(String(100))
    fips_state = Column(String(10))
    county_name = Column(String(150))
    fips_county = Column(String(10))
    rating_area = Column(String(50))
    city_name = Column(String(150))
    zip_code = Column(String(20), primary_key=True)
    population_cnt = Column(String(20))
    cbsa_name = Column(String(150))
    load_date = Column(String(50))
    raw_file_name = Column(String(255))

    def __repr__(self):
        values = {c.name: getattr(self, c.name) for c in self.__table__.columns}
        return f"<LupZipDatabase {values}>"