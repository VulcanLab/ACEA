from enum import Enum
from pydantic import BaseModel, HttpUrl


class TeamRole(str, Enum):
    red = "red"
    blue = "blue"


class ServiceRegistration(BaseModel):
    team_role: TeamRole
    endpoint: str
    token: str
    name: str = ""
    version: str = "1.0.0"
